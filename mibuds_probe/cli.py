from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .bluez import BlueZ, Device, RfcommTransport, find_rfcomm_channel
from .client import ProtocolError, RcspClient
from .protocol import CONTROL_SERVICE_UUID


def _select_device(bluez: BlueZ, address: str | None) -> Device:
    devices = bluez.list_devices()
    if address:
        return bluez.get_device(address)
    exact = [device for device in devices if device.name.casefold() == "redmi buds 8 pro"]
    candidates = exact or [
        device for device in devices
        if device.paired and any(word in device.name.casefold() for word in ("redmi", "xiaomi", "buds"))
    ]
    if not candidates:
        raise RuntimeError("没有找到已配对的小米/Redmi 耳机；请先配对，或用 --address 指定")
    if len(candidates) > 1:
        choices = ", ".join(f"{device.name} ({device.address})" for device in candidates)
        raise RuntimeError(f"找到多个候选设备，请用 --address 指定：{choices}")
    return candidates[0]


def _print_device(device) -> None:
    state = "connected" if device.connected else "disconnected"
    service = "fd2d" if CONTROL_SERVICE_UUID in device.service_uuids else "no-fd2d"
    print(f"{device.name}\t{device.address}\t{state}\t{service}")


def command_list(args: argparse.Namespace) -> int:
    devices = BlueZ().list_devices()
    if args.json:
        print(json.dumps([
            {
                "name": device.name,
                "address": device.address,
                "connected": device.connected,
                "paired": device.paired,
                "trusted": device.trusted,
                "has_control_service": CONTROL_SERVICE_UUID in device.service_uuids,
            }
            for device in devices
        ], ensure_ascii=False, indent=2))
        return 0
    if not devices:
        print("BlueZ 中没有已配对设备")
        return 0
    print("NAME\tADDRESS\tSTATE\tCONTROL_SERVICE")
    for device in devices:
        _print_device(device)
    return 0


def command_inspect(args: argparse.Namespace) -> int:
    bluez = BlueZ()
    device = _select_device(bluez, args.address)
    channel = find_rfcomm_channel(device.address)
    result = {
        "name": device.name,
        "address": device.address,
        "connected": device.connected,
        "control_service": CONTROL_SERVICE_UUID,
        "rfcomm_channel": channel,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Device: {device.name} ({device.address})")
        print(f"BlueZ connected: {device.connected}")
        print(f"Control service: {CONTROL_SERVICE_UUID}")
        print(f"RFCOMM channel: {channel}")
    return 0


def command_probe(args: argparse.Namespace) -> int:
    bluez = BlueZ()
    device = _select_device(bluez, args.address)
    if not device.paired:
        raise RuntimeError(f"{device.name} 尚未配对；请先在系统蓝牙中配对")
    if args.no_connect:
        if not device.connected:
            raise RuntimeError(f"{device.name} 当前未连接，且指定了 --no-connect")
    else:
        device = bluez.ensure_connected(device, timeout=args.timeout)

    logger = (lambda message: print(message, file=sys.stderr, flush=True)) if args.verbose else None
    if logger:
        logger(f"BLUEZ device={device.name} address={device.address} connected={device.connected}")
    channel = args.channel or find_rfcomm_channel(device.address)
    if logger:
        logger(f"RFCOMM address={device.address} channel={channel}")

    transport = RfcommTransport(device.address, channel, timeout=min(args.timeout, 1.0))
    client = RcspClient(transport, timeout=args.timeout, logger=logger)
    try:
        client.start()
        auth = "skipped" if args.skip_auth else client.authenticate()
        result = client.read_state()
        result.update({
            "device": {
                "name": device.name,
                "address": device.address,
                "rfcomm_channel": channel,
            },
            "authentication": auth,
            "frames_received": len([direction for direction, _ in client.frames if direction == "RX"]),
        })
        if args.listen > 0:
            result["listen_frames"] = client.listen(args.listen)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"Device: {device.name} ({device.address})")
            print(f"RFCOMM channel: {channel}")
            print(f"Authentication: {auth}")
            print(json.dumps(result["device_info"], ensure_ascii=False, indent=2))
            print(json.dumps(result["run_info"], ensure_ascii=False, indent=2))
            print(json.dumps(result["configs"], ensure_ascii=False, indent=2))
            if result["errors"]:
                print("Errors:")
                for error in result["errors"]:
                    print(f"  - {error}")
        return 2 if result["errors"] else 0
    finally:
        client.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mibuds-probe",
        description="Linux 小米耳机 RCSP 只读协议探针；不会写入耳机设置",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--verbose", action="store_true", help="输出 RCSP 原始收发帧")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="列出 BlueZ 已配对设备")
    list_parser.add_argument("--json", action="store_true")
    list_parser.set_defaults(handler=command_list)

    inspect_parser = subparsers.add_parser("inspect", help="查询 fd2d 服务的 RFCOMM 通道")
    inspect_parser.add_argument("--address", help="蓝牙 MAC 地址")
    inspect_parser.add_argument("--json", action="store_true")
    inspect_parser.set_defaults(handler=command_inspect)

    probe_parser = subparsers.add_parser("probe", help="连接、鉴权并只读设备状态")
    probe_parser.add_argument("--address", help="蓝牙 MAC 地址")
    probe_parser.add_argument("--channel", type=int, help="手动指定 RFCOMM 通道，跳过 SDP 查询")
    probe_parser.add_argument("--timeout", type=float, default=5.0, help="单次协议响应超时秒数")
    probe_parser.add_argument("--listen", type=float, default=0.0, help="状态读取后继续监听通知的秒数")
    probe_parser.add_argument("--skip-auth", action="store_true", help="跳过 SAFER+ 鉴权，仅用于排查")
    probe_parser.add_argument("--no-connect", action="store_true", help="不主动连接，只使用系统已连接状态")
    probe_parser.add_argument("--json", action="store_true")
    probe_parser.set_defaults(handler=command_probe)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
    except (RuntimeError, OSError, ProtocolError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
