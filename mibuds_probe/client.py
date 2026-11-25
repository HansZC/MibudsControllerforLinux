from __future__ import annotations

from collections import defaultdict, deque
import threading
import time
from typing import Callable, Protocol

from .protocol import (
    AUTH_CONFIRM_REQUEST,
    AUTH_CONFIRM_RESPONSE,
    READ_ONLY_CONFIG_IDS,
    ConfigId,
    Frame,
    FrameParser,
    MessageType,
    Opcode,
    SaferPlus,
    auth_challenge,
    auth_challenge_response,
    encode_set_anc_mode,
    encode_set_config,
    get_config,
    get_device_info,
    get_run_info,
    nibble_version,
    parse_battery,
    walk_tlv,
)


class Transport(Protocol):
    def sendall(self, data: bytes) -> None: ...
    def recv(self, length: int) -> bytes: ...
    def close(self) -> None: ...


class ProtocolError(RuntimeError):
    pass


class ProtocolTimeout(ProtocolError):
    pass


class RcspClient:
    def __init__(
        self,
        transport: Transport,
        timeout: float = 5.0,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.transport = transport
        self.timeout = timeout
        self._logger = logger or (lambda message: None)
        self._condition = threading.Condition()
        self._responses: dict[int, deque[Frame]] = defaultdict(deque)
        self._inbound: dict[int, deque[Frame]] = defaultdict(deque)
        self._send_lock = threading.Lock()
        self._stop = threading.Event()
        self._reader_error: BaseException | None = None
        self._reader: threading.Thread | None = None
        self._sequence = 0
        self.frames: list[tuple[str, Frame]] = []

    def start(self) -> None:
        if self._reader is not None:
            return
        self._reader = threading.Thread(target=self._read_loop, name="rcsp-reader", daemon=True)
        self._reader.start()

    def close(self) -> None:
        self._stop.set()
        try:
            self.transport.close()
        except OSError:
            pass
        if self._reader is not None:
            self._reader.join(timeout=2)
        with self._condition:
            self._condition.notify_all()

    def _next_sequence(self) -> int:
        sequence = self._sequence
        self._sequence = (self._sequence + 1) & 0xFF
        return sequence

    def _write(self, frame: Frame) -> None:
        with self._send_lock:
            raw = frame.encode()
            self._log("TX", frame, raw)
            self.transport.sendall(raw)

    def send_no_wait(self, opcode: int, payload: bytes) -> int:
        sequence = self._next_sequence()
        self._write(Frame(MessageType.PHONE_REQUEST, opcode, sequence, payload))
        return sequence

    def request(self, opcode: int, payload: bytes, timeout: float | None = None) -> Frame:
        self.send_no_wait(opcode, payload)
        return self._wait(opcode, timeout or self.timeout, inbound=False)

    def _wait(self, opcode: int, timeout: float, inbound: bool) -> Frame:
        source = self._inbound if inbound else self._responses
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                if source[opcode]:
                    return source[opcode].popleft()
                if self._reader_error is not None:
                    raise ProtocolError(f"RFCOMM 读取失败：{self._reader_error}") from self._reader_error
                if self._stop.is_set():
                    raise ProtocolError("连接已关闭")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    direction = "耳机主动消息" if inbound else "响应"
                    raise ProtocolTimeout(f"等待 opcode 0x{opcode:02X} 的{direction}超时")
                self._condition.wait(remaining)

    def _wait_response_or_inbound(self, opcode: int, timeout: float) -> tuple[Frame, str]:
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                if self._responses[opcode]:
                    return self._responses[opcode].popleft(), "response"
                if self._inbound[opcode]:
                    return self._inbound[opcode].popleft(), "earbuds"
                if self._reader_error is not None:
                    raise ProtocolError(f"RFCOMM 读取失败：{self._reader_error}") from self._reader_error
                if self._stop.is_set():
                    raise ProtocolError("连接已关闭")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProtocolTimeout(f"等待鉴权确认 0x{opcode:02X} 超时")
                self._condition.wait(remaining)

    def authenticate(self) -> str:
        time.sleep(0.5)
        safer = SaferPlus()
        challenge = safer.generate_challenge()
        self._log_text(f"AUTH challenge={challenge.hex()}")
        response = self.request(Opcode.AUTH_CHALLENGE, auth_challenge(challenge))
        answer = response.payload[-16:]
        if len(answer) != 16:
            raise ProtocolError("SAFER+ 响应不足 16 字节")
        expected = safer.compute_challenge_response(challenge)
        if answer != expected:
            self._log_text("AUTH device returned its own challenge; sending computed response")
            sequence = response.sequence
            self._write(Frame(
                MessageType.RESPONSE,
                Opcode.AUTH_CHALLENGE,
                sequence,
                auth_challenge_response(safer.compute_challenge_response(answer)),
            ))
        else:
            self._log_text("AUTH challenge response verified")
        self.send_no_wait(Opcode.AUTH_CONFIRM, AUTH_CONFIRM_REQUEST)
        _, source = self._wait_response_or_inbound(Opcode.AUTH_CONFIRM, self.timeout)
        self._log_text(f"AUTH confirmed via {source}")
        return source

    def read_state(self) -> dict[str, object]:
        result: dict[str, object] = {"errors": []}
        errors = result["errors"]
        assert isinstance(errors, list)

        try:
            frame = self.request(Opcode.GET_DEVICE_INFO, get_device_info())
            info: dict[str, object] = {"raw": frame.payload.hex()}
            for index, data in walk_tlv(frame.payload):
                if index == 0x01 and len(data) >= 4:
                    info["firmware_1"] = nibble_version(data[0], data[1])
                    info["firmware_2"] = nibble_version(data[2], data[3])
                elif index == 0x03 and len(data) >= 4:
                    info["vid"] = f"0x{data[0]:02X}{data[1]:02X}"
                    info["pid"] = f"0x{data[2]:02X}{data[3]:02X}"
                elif index == 0x07 and len(data) >= 3:
                    batteries = {
                        name: parse_battery(value)
                        for name, value in zip(("left", "right", "case"), data[:3])
                    }
                    info["battery"] = batteries
            result["device_info"] = info
        except ProtocolError as exc:
            errors.append(f"GET_DEVICE_INFO: {exc}")

        try:
            frame = self.request(Opcode.GET_RUN_INFO, get_run_info())
            run_info: dict[str, object] = {"raw": frame.payload.hex()}
            for index, data in walk_tlv(frame.payload):
                if index == 0x09 and data:
                    run_info["anc_mode"] = data[0]
            result["run_info"] = run_info
        except ProtocolError as exc:
            errors.append(f"GET_RUN_INFO: {exc}")

        configs: dict[str, object] = {}
        for config_id in READ_ONLY_CONFIG_IDS:
            try:
                frame = self.request(Opcode.GET_CONFIG, get_config(int(config_id)))
                if len(frame.payload) < 3:
                    raise ProtocolError("GET_CONFIG 响应载荷过短")
                configs[f"0x{int(config_id):02X}"] = {
                    "reported_id": frame.payload[2],
                    "value": frame.payload[3:].hex(),
                }
            except ProtocolError as exc:
                errors.append(f"GET_CONFIG 0x{int(config_id):02X}: {exc}")
        result["configs"] = configs
        return result

    def set_anc_mode(self, mode: int) -> int:
        return self.send_no_wait(Opcode.SET_ANC, encode_set_anc_mode(mode))

    def set_low_latency(self, enabled: bool) -> int:
        value = bytes((1 if enabled else 0,))
        return self.send_no_wait(
            Opcode.SET_CONFIG,
            encode_set_config(int(ConfigId.LOW_LATENCY), value),
        )

    def listen(self, seconds: float) -> int:
        deadline = time.monotonic() + seconds
        initial_count = len(self.frames)
        while time.monotonic() < deadline and not self._stop.is_set():
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        return len(self.frames) - initial_count

    def _read_loop(self) -> None:
        parser = FrameParser()
        try:
            while not self._stop.is_set():
                try:
                    chunk = self.transport.recv(2048)
                except TimeoutError:
                    continue
                if not chunk:
                    break
                parser.append(chunk)
                for frame in parser.drain():
                    self.frames.append(("RX", frame))
                    self._log("RX", frame)
                    with self._condition:
                        if frame.type == MessageType.RESPONSE:
                            self._responses[frame.opcode].append(frame)
                        else:
                            self._inbound[frame.opcode].append(frame)
                        self._condition.notify_all()
                    self._handle_inbound(frame)
        except BaseException as exc:
            if not self._stop.is_set():
                with self._condition:
                    self._reader_error = exc
                    self._condition.notify_all()
        finally:
            with self._condition:
                self._condition.notify_all()

    def _handle_inbound(self, frame: Frame) -> None:
        if frame.type == MessageType.RESPONSE:
            if frame.opcode == 0:
                self._log_text(f"WARNING unexpected ACK seq={frame.sequence}")
            return
        if frame.opcode == Opcode.AUTH_CHALLENGE:
            if len(frame.payload) < 16:
                return
            challenge = frame.payload[-16:]
            response = SaferPlus().compute_challenge_response(challenge)
            self._write(Frame(
                MessageType.RESPONSE,
                Opcode.AUTH_CHALLENGE,
                frame.sequence,
                auth_challenge_response(response),
            ))
        elif frame.opcode == Opcode.AUTH_CONFIRM:
            self._write(Frame(
                MessageType.RESPONSE,
                Opcode.AUTH_CONFIRM,
                frame.sequence,
                AUTH_CONFIRM_RESPONSE,
            ))
        elif frame.opcode in (Opcode.REPORT_STATUS, Opcode.NOTIFY_CONFIG):
            self._write(Frame(
                MessageType.RESPONSE,
                frame.opcode,
                frame.sequence,
                b"",
            ))

    def _log(self, direction: str, frame: Frame, raw: bytes | None = None) -> None:
        encoded = raw if raw is not None else frame.encode()
        self._log_text(
            f"{direction} type=0x{int(frame.type):02X} opcode=0x{frame.opcode:02X} "
            f"seq={frame.sequence} payload={frame.payload.hex() or '-'} frame={encoded.hex()}"
        )

    def _log_text(self, message: str) -> None:
        self._logger(message)
