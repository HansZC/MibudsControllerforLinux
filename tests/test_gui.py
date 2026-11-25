from __future__ import annotations

import importlib.util
import os
import tempfile
import time
import unittest
from unittest.mock import patch


@unittest.skipUnless(
    os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"),
    "GTK tests require a graphical display",
)
@unittest.skipUnless(importlib.util.find_spec("gi") is not None, "PyGObject is required")
class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, GLib
        cls.Adw = Adw
        cls.GLib = GLib
        Adw.init()

    def setUp(self) -> None:
        from mibuds_probe.gui import ControllerWindow
        from mibuds_probe.settings import Settings
        self.temporary = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"XDG_CONFIG_HOME": self.temporary.name})
        self.environment.start()
        self.window = ControllerWindow(demo=True, settings_store=Settings())

    def tearDown(self) -> None:
        self.window.shutdown_session()
        self.window.destroy()
        self.environment.stop()
        self.temporary.cleanup()

    def _wait_idle(self) -> None:
        deadline = time.monotonic() + 3
        context = self.GLib.MainContext.default()
        while self.window.busy and time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            time.sleep(0.01)
        self.assertFalse(self.window.busy)

    def test_demo_state_populates_widgets(self) -> None:
        self.assertEqual(self.window.header_status.get_label(), "已连接")
        self.assertEqual(self.window.battery_widgets["left"][0].get_label(), "86%")
        self.assertFalse(self.window.low_latency_row.get_active())

    def test_anc_control_updates_demo_and_ui(self) -> None:
        from mibuds_probe.protocol import RcspAncModes
        self.window._on_anc_clicked(int(RcspAncModes.NOISE_CANCELLING))
        self._wait_idle()
        self.assertEqual(self.window.last_state["run_info"]["anc_mode"], 1)
        self.assertTrue(self.window.anc_buttons[1].get_active())
        self.assertTrue(self.window.mini_anc_buttons[1].get_active())
        self.assertEqual(self.window.mini_battery_labels["left"].get_label(), "86%")

    def test_low_latency_round_trip(self) -> None:
        self.window.low_latency_row.set_active(True)
        self._wait_idle()
        self.assertTrue(self.window.low_latency)
        self.assertEqual(self.window.last_state["configs"]["0x2F"]["value"], "01")
        self.window.low_latency_row.set_active(False)
        self._wait_idle()
        self.assertFalse(self.window.low_latency)
        self.assertEqual(self.window.last_state["configs"]["0x2F"]["value"], "00")

    def test_audio_page_shows_demo_codec(self) -> None:
        self.assertEqual(self.window.codec_row.get_subtitle(), "AAC")
        self.assertEqual(self.window.profile_row.get_subtitle(), "a2dp-sink")
        self.assertIn("48.0 kHz", self.window.sample_rate_row.get_subtitle())

    def test_retry_state_can_be_cancelled(self) -> None:
        from mibuds_probe.bluez import Device
        self.window.demo = False
        self.window.device = Device("/demo", "24:B2:31:96:68:50", "REDMI Buds 8 Pro", False, True, True, ())
        self.window.settings.data["auto_reconnect"] = True
        self.window._schedule_reconnect("test disconnect")
        self.assertTrue(self.window.retry_pending)
        self.assertEqual(self.window.connect_button.get_label(), "停止重试")
        self.window._cancel_retry()
        self.assertFalse(self.window.retry_pending)

    def test_minimize_to_tray(self) -> None:
        if self.window.tray is None:
            self.skipTest("System tray unavailable")
        self.window.settings.data["minimize_to_tray"] = True
        self.assertTrue(self.window.minimize_on_close())
        self.assertFalse(self.window.get_visible())
        self.window.show_main()

    def test_page_navigation(self) -> None:
        self.window.stack.set_visible_child_name("sound")
        self.assertEqual(self.window.stack.get_visible_child_name(), "sound")
        self.window.stack.set_visible_child_name("log")
        self.assertEqual(self.window.stack.get_visible_child_name(), "log")


if __name__ == "__main__":
    unittest.main()
