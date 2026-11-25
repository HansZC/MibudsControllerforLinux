from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mibuds_probe.settings import Settings


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"XDG_CONFIG_HOME": self.temporary.name})
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    def test_settings_round_trip(self) -> None:
        settings = Settings()
        settings.data["auto_reconnect"] = False
        settings.data["minimize_to_tray"] = False
        settings.set_last_device_address("24:B2:31:96:68:50")
        loaded = Settings()
        self.assertFalse(loaded.auto_reconnect)
        self.assertFalse(loaded.minimize_to_tray)
        self.assertEqual(loaded.last_device_address, "24:B2:31:96:68:50")

    def test_autostart_file_created_and_removed(self) -> None:
        settings = Settings()
        settings.set_autostart(True, "/usr/bin/mibuds-gui")
        self.assertTrue(settings.autostart_path.exists())
        text = settings.autostart_path.read_text()
        self.assertIn("--start-minimized", text)
        settings.set_autostart(False, "/usr/bin/mibuds-gui")
        self.assertFalse(settings.autostart_path.exists())


if __name__ == "__main__":
    unittest.main()
