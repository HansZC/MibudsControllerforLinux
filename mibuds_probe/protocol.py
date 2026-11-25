from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import secrets


CONTROL_SERVICE_UUID = "0000fd2d-0000-1000-8000-00805f9b34fb"
RFCOMM_UUID = "00000003-0000-1000-8000-00805f9b34fb"


class MessageType(IntEnum):
    PHONE_REQUEST = 0xC4
    RESPONSE = 0x04
    EARBUDS_REQUEST = 0xC0
    EARBUDS_NOTIFY = 0xC7


class Opcode(IntEnum):
    GET_DEVICE_INFO = 0x02
    SET_ANC = 0x08
    GET_RUN_INFO = 0x09
    REPORT_STATUS = 0x0E
    AUTH_CHALLENGE = 0x50
    AUTH_CONFIRM = 0x51
    SET_CONFIG = 0xF2
    GET_CONFIG = 0xF3
    NOTIFY_CONFIG = 0xF4


class RcspAncModes(IntEnum):
    OFF = 0
    NOISE_CANCELLING = 1
    TRANSPARENCY = 2


class ConfigId(IntEnum):
    AUDIO_MODE = 0x01
    EFFECT_STRENGTH = 0x0B
    SPATIAL_AUDIO_SWITCH = 0x1D
    SPATIAL_AUDIO_BITMAP = 0x1E
    ADAPTIVE_ANC = 0x25
    LOW_LATENCY = 0x2F
    SCENE_RENDERING = 0x36
    DOLBY_AUDIO_SPATIAL_MODE = 0x68
    SMART_NOISE = 0x66


READ_ONLY_CONFIG_IDS = (
    ConfigId.AUDIO_MODE,
    ConfigId.EFFECT_STRENGTH,
    ConfigId.SPATIAL_AUDIO_SWITCH,
    ConfigId.SPATIAL_AUDIO_BITMAP,
    ConfigId.LOW_LATENCY,
    ConfigId.SCENE_RENDERING,
    ConfigId.SMART_NOISE,
    ConfigId.DOLBY_AUDIO_SPATIAL_MODE,
)


@dataclass(frozen=True)
class Frame:
    type: MessageType
    opcode: int
    sequence: int
    payload: bytes

    def encode(self) -> bytes:
        is_request = bool(int(self.type) & 0x40)
        payload_length = len(self.payload) + (1 if is_request else 2)
        if payload_length > 0xFFFF:
            raise ValueError("RCSP payload is too large")
        frame = bytearray((0xFE, 0xDC, 0xBA, int(self.type), self.opcode))
        frame.extend(payload_length.to_bytes(2, "big"))
        if not is_request:
            frame.append(0)
        frame.append(self.sequence)
        frame.extend(self.payload)
        frame.append(0xEF)
        return bytes(frame)

    @classmethod
    def decode(cls, raw: bytes) -> Frame:
        if len(raw) < 9:
            raise ValueError("RCSP frame is too short")
        if raw[:3] != b"\xfe\xdc\xba" or raw[-1:] != b"\xef":
            raise ValueError("Invalid RCSP frame boundary")
        message_type = MessageType(raw[3])
        payload_length = int.from_bytes(raw[5:7], "big")
        if len(raw) != payload_length + 8:
            raise ValueError("RCSP frame length mismatch")
        payload_offset = 8 if int(message_type) & 0x40 else 9
        return cls(
            message_type,
            raw[4],
            raw[payload_offset - 1],
            raw[payload_offset:-1],
        )


class FrameParser:
    def __init__(self, max_buffer_size: int = 4096) -> None:
        self.max_buffer_size = max_buffer_size
        self._buffer = bytearray()

    def append(self, data: bytes) -> None:
        self._buffer.extend(data)
        if len(self._buffer) > self.max_buffer_size:
            del self._buffer[: len(self._buffer) - self.max_buffer_size]

    def drain(self) -> list[Frame]:
        frames: list[Frame] = []
        while True:
            start = self._buffer.find(b"\xfe\xdc\xba")
            if start < 0:
                self._buffer.clear()
                break
            if start:
                del self._buffer[:start]
            if len(self._buffer) < 7:
                break
            payload_length = int.from_bytes(self._buffer[5:7], "big")
            total_length = payload_length + 8
            if total_length > self.max_buffer_size:
                del self._buffer[0]
                continue
            if len(self._buffer) < total_length:
                break
            if self._buffer[total_length - 1] != 0xEF:
                del self._buffer[0]
                continue
            raw = bytes(self._buffer[:total_length])
            del self._buffer[:total_length]
            try:
                frames.append(Frame.decode(raw))
            except ValueError:
                continue
        return frames


def get_device_info() -> bytes:
    return b"\xff\xff\xff\xff"


def get_run_info() -> bytes:
    return b"\xff\xff\xff\xff"


def get_config(config_id: int) -> bytes:
    return bytes((0, config_id))


def encode_set_anc_mode(mode: int) -> bytes:
    if mode not in (0, 1, 2):
        raise ValueError("ANC mode must be 0, 1 or 2")
    return bytes((2, 4, mode))


def encode_set_config(config_id: int, values: bytes) -> bytes:
    if not 0 <= config_id <= 0xFF or len(values) > 0xFD:
        raise ValueError("Invalid config ID or value length")
    return bytes((len(values) + 2, 0, config_id)) + values


def auth_challenge(random: bytes) -> bytes:
    if len(random) != 16:
        raise ValueError("Challenge must be 16 bytes")
    return b"\x01" + random


def auth_challenge_response(response: bytes) -> bytes:
    if len(response) != 16:
        raise ValueError("Response must be 16 bytes")
    return b"\x01" + response


AUTH_CONFIRM_REQUEST = b"\x01\x00"
AUTH_CONFIRM_RESPONSE = b"\x01"


def walk_tlv(payload: bytes, index_offset: int = 1) -> list[tuple[int, bytes]]:
    entries: list[tuple[int, bytes]] = []
    offset = 0
    while offset + 1 < len(payload):
        length = payload[offset]
        if length <= index_offset:
            break
        index_position = offset + index_offset
        data_start = index_position + 1
        data_length = length - index_offset
        if index_position >= len(payload) or data_start + data_length > len(payload):
            break
        entries.append((payload[index_position], payload[data_start : data_start + data_length]))
        offset += length + 1
    return entries


def parse_battery(value: int) -> tuple[int, bool] | None:
    if value == 0xFF:
        return None
    return value & 0x7F, bool(value & 0x80)


def nibble_version(high: int, low: int) -> str:
    return f"{high >> 4}.{high & 0xF}.{low >> 4}.{low & 0xF}"


class SaferPlus:
    PATTERN = 0x9999
    BLOCK_SIZE = 16
    SEQUENCE = bytes((0x11, 0x22, 0x33, 0x33, 0x22, 0x11, 0x11, 0x22,
                      0x33, 0x33, 0x22, 0x11, 0x11, 0x22, 0x33, 0x33))
    COEFFICIENTS = (
        (2, 1, 1, 1, 4, 2, 1, 1, 2, 2, 4, 2, 4, 4, 16, 8),
        (2, 1, 1, 1, 4, 2, 1, 1, 1, 1, 2, 1, 2, 2, 8, 4),
        (1, 1, 4, 2, 2, 2, 4, 2, 16, 8, 4, 4, 2, 1, 1, 1),
        (1, 1, 4, 2, 1, 1, 2, 1, 8, 4, 2, 2, 2, 1, 1, 1),
        (16, 8, 2, 2, 4, 2, 4, 4, 1, 1, 4, 2, 1, 1, 2, 1),
        (8, 4, 1, 1, 2, 1, 2, 2, 1, 1, 4, 2, 1, 1, 2, 1),
        (2, 2, 4, 2, 4, 4, 16, 8, 2, 1, 1, 1, 4, 2, 1, 1),
        (1, 1, 2, 1, 2, 2, 8, 4, 2, 1, 1, 1, 4, 2, 1, 1),
        (4, 2, 4, 4, 16, 8, 2, 2, 1, 1, 2, 1, 1, 1, 4, 2),
        (2, 1, 2, 2, 8, 4, 1, 1, 1, 1, 2, 1, 1, 1, 4, 2),
        (4, 4, 16, 8, 1, 1, 2, 1, 4, 2, 1, 1, 4, 2, 2, 2),
        (2, 2, 8, 4, 1, 1, 2, 1, 4, 2, 1, 1, 2, 1, 1, 1),
        (1, 1, 2, 1, 1, 1, 4, 2, 4, 4, 16, 8, 2, 2, 4, 2),
        (1, 1, 2, 1, 1, 1, 4, 2, 2, 2, 8, 4, 1, 1, 2, 1),
        (4, 2, 1, 1, 2, 1, 1, 1, 4, 2, 2, 2, 16, 8, 4, 4),
        (4, 2, 1, 1, 2, 1, 1, 1, 2, 1, 1, 1, 8, 4, 2, 2),
    )

    def __init__(self) -> None:
        self._bias = self._generate_bias_matrix()
        self._exp = [self._mod_pow(45, i, 257) for i in range(256)]
        self._log = [128] * 256
        for i, value in enumerate(self._exp):
            if i != 128 and value != 256:
                self._log[value] = i

    @staticmethod
    def generate_challenge() -> bytes:
        return secrets.token_bytes(16)

    @staticmethod
    def _mod_pow(value: int, exponent: int, modulus: int) -> int:
        result = 1
        base = value % modulus
        while exponent > 0:
            if exponent & 1:
                result = result * base % modulus
            base = base * base % modulus
            exponent >>= 1
        return result

    def _generate_bias_matrix(self) -> list[list[int]]:
        matrix = []
        for i in range(16):
            row = []
            for j in range(16):
                exponent = 17 * (i + 2) + j + 1
                value = self._mod_pow(45, self._mod_pow(45, exponent, 257), 257)
                row.append(0 if value == 256 else value)
            matrix.append(row)
        return matrix

    def _bit_set(self, index: int) -> bool:
        return bool((1 << index) & self.PATTERN)

    def compute_challenge_response(self, challenge: bytes) -> bytes:
        key_init = bytearray(challenge)
        key_init[15] ^= 6
        keys = [bytes(key_init)]
        register = bytearray(17)
        register[:16] = key_init
        register[16] = 0
        for value in key_init:
            register[16] ^= value
        for key_index in range(1, 17):
            register = bytearray(
                ((value >> 5) | ((value & 0x1F) << 3)) & 0xFF
                for value in register
            )
            keys.append(bytes(
                (register[(key_index + i) % 17] + self._bias[key_index - 1][i]) & 0xFF
                for i in range(16)
            ))
        return self._encrypt(self.SEQUENCE, keys)

    def _encrypt(self, plaintext: bytes, keys: list[bytes]) -> bytes:
        ciphertext = bytearray(plaintext)
        for round_index in range(8):
            if round_index == 2:
                for i in range(16):
                    if self._bit_set(i):
                        ciphertext[i] ^= plaintext[i]
                    else:
                        ciphertext[i] = (ciphertext[i] + plaintext[i]) & 0xFF
            for i in range(16):
                if self._bit_set(i):
                    ciphertext[i] ^= keys[round_index * 2][i]
                else:
                    ciphertext[i] = (ciphertext[i] + keys[round_index * 2][i]) & 0xFF
            for i in range(16):
                ciphertext[i] = (
                    self._exp[ciphertext[i]] if self._bit_set(i) else self._log[ciphertext[i]]
                ) & 0xFF
            for i in range(16):
                if self._bit_set(i):
                    ciphertext[i] = (keys[round_index * 2 + 1][i] + ciphertext[i]) & 0xFF
                else:
                    ciphertext[i] ^= keys[round_index * 2 + 1][i]
            previous = bytes(ciphertext)
            for i in range(16):
                ciphertext[i] = sum(
                    self.COEFFICIENTS[i][j] * previous[j] for j in range(16)
                ) & 0xFF
        for i in range(16):
            if self._bit_set(i):
                ciphertext[i] ^= keys[16][i]
            else:
                ciphertext[i] = (keys[16][i] + ciphertext[i]) & 0xFF
        return bytes(ciphertext)
