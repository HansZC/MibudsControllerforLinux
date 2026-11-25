from __future__ import annotations

import importlib.util
import os
import time
import unittest


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
        self.window = ControllerWindow(demo=True)

    def tearDown(self) -> None:
        self.window.shutdown_session()
        self.window.destroy()

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

    def test_low_latency_round_trip(self) -> None:
        self.window.low_latency_row.set_active(True)
        self._wait_idle()
        self.assertTrue(self.window.low_latency)
        self.assertEqual(self.window.last_state["configs"]["0x2F"]["value"], "01")
        self.window.low_latency_row.set_active(False)
        self._wait_idle()
        self.assertFalse(self.window.low_latency)
        self.assertEqual(self.window.last_state["configs"]["0x2F"]["value"], "00")

    def test_page_navigation(self) -> None:
        self.window.stack.set_visible_child_name("sound")
        self.assertEqual(self.window.stack.get_visible_child_name(), "sound")
        self.window.stack.set_visible_child_name("log")
        self.assertEqual(self.window.stack.get_visible_child_name(), "log")


if __name__ == "__main__":
    unittest.main()
