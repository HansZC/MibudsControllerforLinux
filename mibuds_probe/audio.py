from __future__ import annotations

from dataclasses import dataclass
import json
import os
import shutil
import subprocess


@dataclass(frozen=True)
class AudioInfo:
    sink_id: int
    sink_name: str
    device_name: str
    codec: str
    profile: str
    sample_rate: int | None
    channels: int | None
    format: str


def get_audio_info(address: str) -> AudioInfo | None:
    try:
        output = subprocess.check_output(["pw-dump"], text=True, timeout=5)
        objects = json.loads(output)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    for item in objects:
        if item.get("type") != "PipeWire:Interface:Node":
            continue
        info = item.get("info", {})
        props = info.get("props", {})
        if props.get("media.class") != "Audio/Sink" or props.get("api.bluez5.address") != address:
            continue
        formats = info.get("params", {}).get("EnumFormat", [])
        selected = formats[0] if formats else {}
        return AudioInfo(
            sink_id=int(item["id"]),
            sink_name=str(props.get("node.name", "")),
            device_name=str(props.get("node.description", props.get("media.name", ""))),
            codec=str(props.get("api.bluez5.codec", "unknown")).upper(),
            profile=str(props.get("api.bluez5.profile", "unknown")),
            sample_rate=int(selected["rate"]) if selected.get("rate") else None,
            channels=int(selected["channels"]) if selected.get("channels") else None,
            format=str(selected.get("format", "unknown")),
        )
    return None


def open_audio_settings() -> bool:
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    commands = []
    if "gnome" in desktop:
        commands.append(["gnome-control-center", "sound"])
    elif "kde" in desktop:
        commands.append(["systemsettings", "multimedia"])
    commands.extend((
        ["gnome-control-center", "sound"],
        ["systemsettings", "multimedia"],
    ))
    for command in commands:
        if shutil.which(command[0]):
            try:
                subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                return True
            except OSError:
                continue
    return False


def easyeffects_command() -> list[str] | None:
    executable = shutil.which("easyeffects")
    if executable:
        return [executable]
    if shutil.which("flatpak"):
        return ["flatpak", "run", "com.github.wwmm.easyeffects"]
    return None


def easyeffects_installed() -> bool:
    if shutil.which("easyeffects"):
        return True
    if not shutil.which("flatpak"):
        return False
    try:
        output = subprocess.check_output(
            ["flatpak", "list", "--app", "--columns=application"],
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "com.github.wwmm.easyeffects" in output


def launch_easyeffects() -> bool:
    command = easyeffects_command()
    if command is None:
        return False
    try:
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False
