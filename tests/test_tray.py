from __future__ import annotations

import os
import threading
import unittest

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib


@unittest.skipUnless(os.environ.get("DBUS_SESSION_BUS_ADDRESS"), "Session D-Bus is required")
class TrayTests(unittest.TestCase):
    def setUp(self) -> None:
        from mibuds_probe.tray import TrayService
        self.tray = TrayService(lambda action: None)
        self.loop = GLib.MainLoop()

    def tearDown(self) -> None:
        self.tray.close()

    def _call(self, method: str, parameters: GLib.Variant):
        outcome = []

        def invoke() -> None:
            try:
                outcome.append(self.tray._connection.call_sync(
                    self.tray._name,
                    "/MenuBar" if method == "GetLayout" else "/StatusNotifierItem",
                    "com.canonical.dbusmenu" if method == "GetLayout" else "org.freedesktop.DBus.Properties",
                    method,
                    parameters,
                    None,
                    Gio.DBusCallFlags.NONE,
                    3000,
                    None,
                ).unpack())
            except Exception as exc:
                outcome.append(exc)
            finally:
                self.loop.quit()

        threading.Thread(target=invoke, daemon=True).start()
        GLib.timeout_add(5000, self.loop.quit)
        self.loop.run()
        self.assertEqual(len(outcome), 1)
        if isinstance(outcome[0], Exception):
            raise outcome[0]
        return outcome[0]

    def test_properties_round_trip(self) -> None:
        result = self._call("GetAll", GLib.Variant("(s)", ("org.kde.StatusNotifierItem",)))
        properties = result[0]
        self.assertEqual(properties["Id"], "mibuds-controller")
        self.assertEqual(properties["Status"], "Passive")

    def test_menu_layout_round_trip(self) -> None:
        result = self._call("GetLayout", GLib.Variant("(iias)", (0, 2, [])))
        labels = [item[1].get("label") for item in result[2]]
        self.assertIn("打开主界面", labels)
        self.assertIn("迷你面板", labels)
        self.assertIn("退出", labels)


if __name__ == "__main__":
    unittest.main()
