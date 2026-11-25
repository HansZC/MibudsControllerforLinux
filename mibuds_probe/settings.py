from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import tempfile


class Settings:
    def __init__(self) -> None:
        config_root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        self.config_dir = config_root / "mibuds-controller"
        self.path = self.config_dir / "settings.json"
        self.autostart_path = config_root / "autostart" / "mibuds-controller.desktop"
        self.data = {
            "auto_reconnect": True,
            "auto_connect": True,
            "minimize_to_tray": True,
            "last_device_address": "",
        }
        self.load()

    def load(self) -> None:
        try:
            loaded = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return
        if isinstance(loaded, dict):
            for key in self.data:
                if key in loaded:
                    if isinstance(self.data[key], bool):
                        self.data[key] = bool(loaded[key])
                    else:
                        self.data[key] = str(loaded[key])

    def save(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix="settings-", dir=self.config_dir)
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump(self.data, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.replace(temporary, self.path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    @property
    def auto_reconnect(self) -> bool:
        return bool(self.data["auto_reconnect"])

    @property
    def auto_connect(self) -> bool:
        return bool(self.data["auto_connect"])

    @property
    def minimize_to_tray(self) -> bool:
        return bool(self.data["minimize_to_tray"])

    @property
    def last_device_address(self) -> str:
        return str(self.data["last_device_address"])

    def set_last_device_address(self, address: str) -> None:
        self.data["last_device_address"] = address
        self.save()

    def set_autostart(self, enabled: bool, executable: str) -> None:
        if not enabled:
            try:
                self.autostart_path.unlink()
            except FileNotFoundError:
                pass
            return
        self.autostart_path.parent.mkdir(parents=True, exist_ok=True)
        command = f"{shlex.quote(str(Path(executable).resolve()))} --start-minimized"
        self.autostart_path.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=小米耳机控制器\n"
            f"Exec={command}\n"
            "Icon=mibuds-controller\n"
            "Terminal=false\n"
            "X-GNOME-Autostart-enabled=true\n"
        )

    @property
    def autostart_enabled(self) -> bool:
        return self.autostart_path.exists()
