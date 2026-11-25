from __future__ import annotations

import ctypes
import ctypes.util
from dataclasses import dataclass
import socket
import time

from .protocol import CONTROL_SERVICE_UUID


@dataclass(frozen=True)
class Device:
    path: str
    address: str
    name: str
    connected: bool
    paired: bool
    trusted: bool
    service_uuids: tuple[str, ...]


class SdpError(RuntimeError):
    pass


class _Bdaddr(ctypes.Structure):
    _fields_ = [("b", ctypes.c_uint8 * 6)]


class _SdpList(ctypes.Structure):
    pass


_SdpList._fields_ = [
    ("next", ctypes.POINTER(_SdpList)),
    ("data", ctypes.c_void_p),
]


class BlueZ:
    def __init__(self) -> None:
        try:
            import dbus
        except ImportError as exc:
            raise RuntimeError("缺少 dbus-python；Fedora 可安装 python3-dbus") from exc
        self._dbus = dbus
        self._bus = dbus.SystemBus()
        self._manager = dbus.Interface(
            self._bus.get_object("org.bluez", "/"),
            "org.freedesktop.DBus.ObjectManager",
        )

    def list_devices(self) -> list[Device]:
        devices = []
        for path, interfaces in self._manager.GetManagedObjects().items():
            properties = interfaces.get("org.bluez.Device1")
            if properties is None:
                continue
            devices.append(Device(
                path=str(path),
                address=str(properties.get("Address", "")),
                name=str(properties.get("Name", "")),
                connected=bool(properties.get("Connected", False)),
                paired=bool(properties.get("Paired", False)),
                trusted=bool(properties.get("Trusted", False)),
                service_uuids=tuple(str(value).lower() for value in properties.get("UUIDs", [])),
            ))
        return sorted(devices, key=lambda device: (device.name, device.address))

    def get_device(self, address: str) -> Device:
        normalized = address.lower()
        for device in self.list_devices():
            if device.address.lower() == normalized:
                return device
        raise RuntimeError(f"BlueZ 中没有找到设备 {address}；请先在系统蓝牙中配对")

    def _device_object(self, device: Device):
        return self._bus.get_object("org.bluez", device.path)

    def ensure_connected(self, device: Device, timeout: float = 20.0) -> Device:
        connect_error: self._dbus.DBusException | None = None
        if not device.connected:
            interface = self._dbus.Interface(self._device_object(device), "org.bluez.Device1")
            try:
                interface.Connect(timeout=timeout)
            except self._dbus.DBusException as exc:
                connect_error = exc
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = self.get_device(device.address)
            if current.connected:
                return current
            time.sleep(0.2)
        if connect_error is not None:
            raise RuntimeError(f"连接 {device.name} 失败：{connect_error}") from connect_error
        raise RuntimeError(f"等待 {device.name} 进入连接状态超时")


def _load_sdp_library():
    name = ctypes.util.find_library("bluetooth") or "libbluetooth.so.3"
    try:
        lib = ctypes.CDLL(name)
    except OSError as exc:
        raise SdpError("找不到 libbluetooth；Fedora 可安装 bluez-libs") from exc
    list_pointer = ctypes.POINTER(_SdpList)
    lib.sdp_connect.argtypes = [ctypes.POINTER(_Bdaddr), ctypes.POINTER(_Bdaddr), ctypes.c_uint32]
    lib.sdp_connect.restype = ctypes.c_void_p
    lib.sdp_close.argtypes = [ctypes.c_void_p]
    lib.sdp_close.restype = ctypes.c_int
    lib.sdp_uuid16_create.argtypes = [ctypes.c_void_p, ctypes.c_uint16]
    lib.sdp_uuid16_create.restype = ctypes.c_void_p
    lib.sdp_list_append.argtypes = [list_pointer, ctypes.c_void_p]
    lib.sdp_list_append.restype = list_pointer
    lib.sdp_list_free.argtypes = [list_pointer, ctypes.c_void_p]
    lib.sdp_list_free.restype = None
    lib.sdp_service_search_attr_req.argtypes = [
        ctypes.c_void_p,
        list_pointer,
        ctypes.c_int,
        list_pointer,
        ctypes.POINTER(list_pointer),
    ]
    lib.sdp_service_search_attr_req.restype = ctypes.c_int
    lib.sdp_get_error.argtypes = [ctypes.c_void_p]
    lib.sdp_get_error.restype = ctypes.c_int
    lib.sdp_get_access_protos.argtypes = [ctypes.c_void_p, ctypes.POINTER(list_pointer)]
    lib.sdp_get_access_protos.restype = ctypes.c_int
    lib.sdp_get_proto_port.argtypes = [list_pointer, ctypes.c_int]
    lib.sdp_get_proto_port.restype = ctypes.c_int
    lib.sdp_record_free.argtypes = [ctypes.c_void_p]
    lib.sdp_record_free.restype = None
    return lib


def find_rfcomm_channel(
    address: str,
    service_uuid: str = CONTROL_SERVICE_UUID,
) -> int:
    compact_uuid = service_uuid.removeprefix("0000").split("-", 1)[0]
    if len(compact_uuid) != 4:
        raise ValueError("SDP lookup currently requires a 16-bit UUID")
    try:
        address_bytes = bytes.fromhex(address.replace(":", ""))
    except ValueError as exc:
        raise ValueError(f"无效蓝牙地址：{address}") from exc
    if len(address_bytes) != 6:
        raise ValueError(f"无效蓝牙地址：{address}")

    lib = _load_sdp_library()
    remote = _Bdaddr((ctypes.c_uint8 * 6).from_buffer_copy(address_bytes[::-1]))
    local = _Bdaddr((ctypes.c_uint8 * 6)())
    uuid_buffer = ctypes.create_string_buffer(32)
    attribute = ctypes.c_uint16(4)
    search = ctypes.POINTER(_SdpList)()
    attributes = ctypes.POINTER(_SdpList)()
    records = ctypes.POINTER(_SdpList)()
    session = lib.sdp_connect(ctypes.byref(local), ctypes.byref(remote), 1)
    if not session:
        raise SdpError(f"无法连接 {address} 的 SDP 服务器")
    try:
        lib.sdp_uuid16_create(uuid_buffer, int(compact_uuid, 16))
        search = lib.sdp_list_append(None, ctypes.byref(uuid_buffer))
        attributes = lib.sdp_list_append(None, ctypes.byref(attribute))
        status = lib.sdp_service_search_attr_req(
            session,
            search,
            1,
            attributes,
            ctypes.byref(records),
        )
        if status < 0:
            error = lib.sdp_get_error(session)
            raise SdpError(f"SDP 服务查询失败（status={status}, errno={error}）")
        channels = []
        node = records
        while node:
            protocols = ctypes.POINTER(_SdpList)()
            if lib.sdp_get_access_protos(node.contents.data, ctypes.byref(protocols)) == 0:
                channel = lib.sdp_get_proto_port(protocols, 3)
                if channel > 0:
                    channels.append(channel)
                protocol = protocols
                while protocol:
                    lib.sdp_list_free(
                        ctypes.cast(protocol.contents.data, ctypes.POINTER(_SdpList)),
                        None,
                    )
                    protocol = protocol.contents.next
                lib.sdp_list_free(protocols, None)
            lib.sdp_record_free(node.contents.data)
            node = node.contents.next
        if not channels:
            raise SdpError(f"{service_uuid} 的 SDP 记录中没有找到 RFCOMM 通道")
        return channels[0]
    finally:
        lib.sdp_list_free(records, None)
        lib.sdp_list_free(search, None)
        lib.sdp_list_free(attributes, None)
        lib.sdp_close(session)


class RfcommTransport:
    def __init__(self, address: str, channel: int, timeout: float = 10.0) -> None:
        self.address = address
        self.channel = channel
        self._socket = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        self._socket.settimeout(timeout)
        try:
            self._socket.connect((address, channel))
        except OSError:
            self._socket.close()
            raise

    def sendall(self, data: bytes) -> None:
        self._socket.sendall(data)

    def recv(self, length: int) -> bytes:
        return self._socket.recv(length)

    def close(self) -> None:
        self._socket.close()
