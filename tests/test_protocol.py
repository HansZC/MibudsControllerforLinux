from __future__ import annotations

from collections import deque
import threading
import time
import unittest

from mibuds_probe.client import ProtocolTimeout, RcspClient
from mibuds_probe.protocol import (
    Frame,
    FrameParser,
    MessageType,
    Opcode,
    RcspAncModes,
    SaferPlus,
    get_device_info,
)


class FakeTransport:
    def __init__(self) -> None:
        self.sent = bytearray()
        self.incoming = deque()
        self.pending = deque()
        self.condition = threading.Condition()
        self.closed = False

    def sendall(self, data: bytes) -> None:
        with self.condition:
            self.sent.extend(data)
            self.condition.notify_all()

    def recv(self, length: int) -> bytes:
        with self.condition:
            if not self.incoming and not self.closed:
                self.condition.wait(0.05)
            if self.closed:
                return b""
            if not self.incoming:
                raise TimeoutError
            data = bytes(self.incoming.popleft())
            return data[:length]

    def close(self) -> None:
        with self.condition:
            self.closed = True
            self.condition.notify_all()

    def receive(self, frame: Frame) -> None:
        with self.condition:
            self.incoming.append(frame.encode())
            self.condition.notify_all()

    def wait_for_frame(self, timeout: float = 1.0) -> Frame:
        deadline = time.monotonic() + timeout
        parser = FrameParser()
        with self.condition:
            while time.monotonic() < deadline:
                if self.pending:
                    return self.pending.popleft()
                raw = bytes(self.sent)
                self.sent.clear()
                parser.append(raw)
                self.pending.extend(parser.drain())
                if self.pending:
                    return self.pending.popleft()
                self.condition.wait(deadline - time.monotonic())
        raise AssertionError("No frame sent")


class ProtocolTests(unittest.TestCase):
    def test_request_round_trip(self) -> None:
        frame = Frame(MessageType.PHONE_REQUEST, Opcode.GET_DEVICE_INFO, 7, get_device_info())
        self.assertEqual(Frame.decode(frame.encode()), frame)

    def test_response_round_trip(self) -> None:
        frame = Frame(MessageType.RESPONSE, Opcode.GET_CONFIG, 9, b"\x00\x2f\x01")
        self.assertEqual(Frame.decode(frame.encode()), frame)

    def test_parser_handles_partial_and_multiple_frames(self) -> None:
        first = Frame(MessageType.PHONE_REQUEST, Opcode.GET_DEVICE_INFO, 1, b"\xff" * 4)
        second = Frame(MessageType.RESPONSE, Opcode.GET_DEVICE_INFO, 1, b"\x01\x04\x12\x34")
        parser = FrameParser()
        raw = first.encode() + second.encode()
        parser.append(raw[:5])
        self.assertEqual(parser.drain(), [])
        parser.append(raw[5:17])
        parser.append(raw[17:])
        self.assertEqual(parser.drain(), [first, second])

    def test_parser_skips_bad_frame_boundary(self) -> None:
        valid = Frame(MessageType.RESPONSE, 0x02, 1, b"ok")
        raw = bytearray(valid.encode())
        raw[-1] = 0
        parser = FrameParser()
        parser.append(bytes(raw) + valid.encode())
        self.assertEqual(parser.drain(), [valid])

    def test_safer_response_is_deterministic_and_sized(self) -> None:
        challenge = bytes(range(16))
        first = SaferPlus().compute_challenge_response(challenge)
        second = SaferPlus().compute_challenge_response(challenge)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 16)
        self.assertNotEqual(first, challenge)

    def test_safer_response_matches_pinned_vector(self) -> None:
        challenge = bytes(range(16))
        self.assertEqual(
            SaferPlus().compute_challenge_response(challenge).hex(),
            "8713913c41434e091cad794a1b5d95c4",
        )


class ClientTests(unittest.TestCase):
    def test_request_matches_opcode_and_type(self) -> None:
        transport = FakeTransport()
        client = RcspClient(transport, timeout=0.5)
        client.start()
        try:
            transport.receive(Frame(MessageType.EARBUDS_NOTIFY, Opcode.GET_CONFIG, 99, b"noise"))
            transport.receive(Frame(MessageType.RESPONSE, Opcode.GET_CONFIG, 7, b"\x00\x2f\x01"))
            response = client.request(Opcode.GET_CONFIG, b"\x00\x2f")
            self.assertEqual(response.payload, b"\x00\x2f\x01")
            sent = transport.wait_for_frame()
            self.assertEqual(sent.opcode, Opcode.GET_CONFIG)
            self.assertEqual(sent.type, MessageType.PHONE_REQUEST)
        finally:
            client.close()

    def test_control_methods_encode_expected_frames(self) -> None:
        transport = FakeTransport()
        client = RcspClient(transport)
        try:
            self.assertEqual(client.set_anc_mode(int(RcspAncModes.TRANSPARENCY)), 0)
            anc = transport.wait_for_frame()
            self.assertEqual(anc.type, MessageType.PHONE_REQUEST)
            self.assertEqual(anc.opcode, Opcode.SET_ANC)
            self.assertEqual(anc.payload, b"\x02\x04\x02")

            self.assertEqual(client.set_low_latency(True), 1)
            low_latency = transport.wait_for_frame()
            self.assertEqual(low_latency.opcode, Opcode.SET_CONFIG)
            self.assertEqual(low_latency.payload, b"\x03\x00\x2f\x01")
        finally:
            client.close()

    def test_request_timeout(self) -> None:
        transport = FakeTransport()
        client = RcspClient(transport, timeout=0.05)
        client.start()
        try:
            with self.assertRaises(ProtocolTimeout):
                client.request(Opcode.GET_RUN_INFO, b"\xff" * 4)
        finally:
            client.close()

    def test_authentication_accepts_device_challenge(self) -> None:
        transport = FakeTransport()
        client = RcspClient(transport, timeout=0.5)
        client.start()
        outcome: list[str] = []
        errors: list[BaseException] = []

        def authenticate() -> None:
            try:
                outcome.append(client.authenticate())
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=authenticate)
        worker.start()
        try:
            request = transport.wait_for_frame()
            self.assertEqual(request.opcode, Opcode.AUTH_CHALLENGE)
            safer = SaferPlus()
            device_challenge = bytes(reversed(range(16)))
            transport.receive(Frame(
                MessageType.RESPONSE,
                Opcode.AUTH_CHALLENGE,
                request.sequence,
                safer.compute_challenge_response(device_challenge),
            ))
            challenge_response = transport.wait_for_frame()
            self.assertEqual(challenge_response.type, MessageType.RESPONSE)
            self.assertEqual(challenge_response.opcode, Opcode.AUTH_CHALLENGE)
            device_answer = safer.compute_challenge_response(device_challenge)
            self.assertEqual(
                challenge_response.payload[-16:],
                safer.compute_challenge_response(device_answer),
            )
            confirm_request = transport.wait_for_frame()
            self.assertEqual(confirm_request.opcode, Opcode.AUTH_CONFIRM)
            transport.receive(Frame(
                MessageType.EARBUDS_REQUEST,
                Opcode.AUTH_CONFIRM,
                confirm_request.sequence,
                b"\x01",
            ))
            confirm_response = transport.wait_for_frame()
            self.assertEqual(confirm_response.type, MessageType.RESPONSE)
            self.assertEqual(confirm_response.opcode, Opcode.AUTH_CONFIRM)
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(outcome, ["earbuds"])
        finally:
            client.close()
            worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
