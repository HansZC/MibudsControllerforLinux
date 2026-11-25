from __future__ import annotations

import copy
import threading
import time
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from .bluez import BlueZ, Device, RfcommTransport, find_rfcomm_channel
from .client import RcspClient
from .protocol import RcspAncModes


class DemoSession:
    is_connected = True

    def __init__(self) -> None:
        self.state = {
            "errors": [],
            "device_info": {
                "firmware_1": "1.3.8.3",
                "firmware_2": "1.3.8.3",
                "vid": "0x2717",
                "pid": "0x50E3",
                "battery": {
                    "left": [86, True],
                    "right": [84, True],
                    "case": [65, False],
                },
            },
            "run_info": {"anc_mode": int(RcspAncModes.OFF)},
            "configs": {"0x2F": {"reported_id": 0x2F, "value": "00"}},
        }

    def authenticate(self) -> str:
        return "demo"

    def read_state(self) -> dict[str, object]:
        return copy.deepcopy(self.state)

    def set_anc_mode(self, mode: int) -> int:
        self.state["run_info"]["anc_mode"] = mode
        return 0

    def set_low_latency(self, enabled: bool) -> int:
        self.state["configs"]["0x2F"]["value"] = "01" if enabled else "00"
        return 0

    def close(self) -> None:
        return None


class ControllerWindow(Adw.ApplicationWindow):
    def __init__(self, demo: bool = False) -> None:
        super().__init__()
        self.demo = demo
        self.client: RcspClient | DemoSession | None = None
        self.device: Device | None = None
        self.channel: int | None = None
        self.devices: list[Device] = []
        self.last_state: dict[str, object] = {}
        self.busy = False
        self.syncing = False
        self.low_latency = False
        self._log_lines = 0

        self.set_title("小米耳机控制器")
        self.set_default_size(980, 720)
        self.set_size_request(820, 600)
        self.set_icon_name("audio-headphones-symbolic")
        self._install_css()

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(root)
        header = Adw.HeaderBar()
        header.set_title_widget(self._build_header_title())
        root.append(header)

        self.banner = Adw.Banner()
        self.banner.set_revealed(False)
        root.append(self.banner)

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, vexpand=True)
        root.append(content)
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_transition_duration(180)
        sidebar = Gtk.StackSidebar()
        sidebar.set_stack(self.stack)
        sidebar.set_size_request(176, -1)
        content.append(sidebar)
        content.append(self.stack)

        self._build_device_page()
        self._build_sound_page()
        self._build_log_page()
        self._set_connected(False)
        self._append_log("MiBuds Controller for Linux")
        if demo:
            self._start_demo()
        else:
            self.refresh_devices()

    def _install_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_string("""
            .battery-level { font-size: 28px; font-weight: 700; }
            .device-title { font-size: 18px; font-weight: 700; }
            .status-connected { color: @success_color; }
            .status-disconnected { color: @error_color; }
            .log-view { font-family: monospace; font-size: 12px; }
        """)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _build_header_title(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(label="小米耳机控制器", xalign=0.0)
        title.add_css_class("device-title")
        self.header_status = Gtk.Label(label="未连接", xalign=0.0)
        self.header_status.add_css_class("caption")
        box.append(title)
        box.append(self.header_status)
        return box

    @staticmethod
    def _page(title: str, name: str, icon: str) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage()
        page.set_title(title)
        page.set_icon_name(icon)
        return page

    def _build_device_page(self) -> None:
        page = self._page("设备", "device", "audio-headphones-symbolic")
        connection = Adw.PreferencesGroup(
            title="设备连接",
            description="通过 BlueZ 发现已配对的小米 / Redmi 耳机。应用连接不会更改系统蓝牙配对。",
        )
        device_row = Adw.ActionRow(title="耳机")
        self.device_model = Gtk.StringList()
        self.device_dropdown = Gtk.DropDown(model=self.device_model)
        self.device_dropdown.set_size_request(250, -1)
        self.device_dropdown.connect("notify::selected", self._on_device_selected)
        device_row.add_suffix(self.device_dropdown)
        connection.add(device_row)

        action_row = Adw.ActionRow(title="会话状态", subtitle="请选择耳机后连接")
        self.connection_status_row = action_row
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.refresh_button = Gtk.Button(label="重新扫描")
        self.refresh_button.connect("clicked", lambda _button: self.refresh_devices())
        self.connect_button = Gtk.Button(label="连接")
        self.connect_button.add_css_class("suggested-action")
        self.connect_button.connect("clicked", self._on_connect_clicked)
        actions.append(self.refresh_button)
        actions.append(self.connect_button)
        action_row.add_suffix(actions)
        connection.add(action_row)
        page.add(connection)

        battery_group = Adw.PreferencesGroup(title="电量", description="左右耳机与充电盒的实时电量")
        self.battery_widgets: dict[str, tuple[Gtk.Label, Gtk.Label, Gtk.LevelBar]] = {}
        for key, title, icon in (
            ("left", "左耳机", "headphones-symbolic"),
            ("right", "右耳机", "headphones-symbolic"),
            ("case", "充电盒", "battery-symbolic"),
        ):
            row = Adw.ActionRow(title=title, subtitle="实时读数")
            row.add_prefix(Gtk.Image.new_from_icon_name(icon))
            suffix = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            level = Gtk.Label(label="--")
            level.add_css_class("battery-level")
            state = Gtk.Label(label="")
            state.add_css_class("caption")
            bar = Gtk.LevelBar()
            bar.set_min_value(0)
            bar.set_max_value(100)
            bar.set_value(0)
            bar.set_size_request(92, 8)
            bar.set_mode(Gtk.LevelBarMode.CONTINUOUS)
            values = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            values.append(level)
            values.append(bar)
            suffix.append(values)
            suffix.append(state)
            row.add_suffix(suffix)
            battery_group.add(row)
            self.battery_widgets[key] = (level, state, bar)
        page.add(battery_group)

        noise_group = Adw.PreferencesGroup(
            title="降噪状态",
            description="关闭、降噪与通透为同级三选一。修改后会回读耳机状态。",
        )
        mode_row = Adw.ActionRow(title="模式")
        modes = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        modes.add_css_class("linked")
        self.anc_buttons: dict[int, Gtk.ToggleButton] = {}
        first_button = None
        for mode, label in (
            (int(RcspAncModes.OFF), "关闭"),
            (int(RcspAncModes.NOISE_CANCELLING), "降噪"),
            (int(RcspAncModes.TRANSPARENCY), "通透"),
        ):
            button = Gtk.ToggleButton(label=label)
            if first_button is not None:
                button.set_group(first_button)
            else:
                first_button = button
            button.connect("clicked", lambda _widget, value=mode: self._on_anc_clicked(value))
            modes.append(button)
            self.anc_buttons[mode] = button
        self.anc_buttons[int(RcspAncModes.OFF)].set_active(True)
        mode_row.add_suffix(modes)
        noise_group.add(mode_row)
        self.noise_summary = Adw.ActionRow(title="当前状态", subtitle="环境声已关闭")
        noise_group.add(self.noise_summary)
        page.add(noise_group)

        info_group = Adw.PreferencesGroup(title="设备信息")
        self.firmware_row = Adw.ActionRow(title="固件版本", subtitle="--")
        self.vidpid_row = Adw.ActionRow(title="VID / PID", subtitle="--")
        self.channel_row = Adw.ActionRow(title="RFCOMM 通道", subtitle="--")
        info_group.add(self.firmware_row)
        info_group.add(self.vidpid_row)
        info_group.add(self.channel_row)
        page.add(info_group)
        self.stack.add_named(page, "device")
        stack_page = self.stack.get_page(page)
        stack_page.set_title("设备")
        stack_page.set_icon_name("audio-headphones-symbolic")

    def _build_sound_page(self) -> None:
        page = self._page("音效设置", "sound", "multimedia-volume-control-symbolic")
        group = Adw.PreferencesGroup(title="声音体验")
        self.low_latency_row = Adw.SwitchRow(
            title="低延迟",
            subtitle="在游戏音视频同步上提供低延迟体验",
        )
        self.low_latency_row.connect("notify::active", self._on_low_latency_changed)
        group.add(self.low_latency_row)
        page.add(group)

        unavailable = Adw.PreferencesGroup(
            title="后续版本",
            description="空间音频、音效模式和自适应听感尚未加入 Linux 首版。",
        )
        placeholder = Adw.ActionRow(title="空间音频", subtitle="计划支持")
        placeholder.set_sensitive(False)
        unavailable.add(placeholder)
        page.add(unavailable)
        self.stack.add_named(page, "sound")
        stack_page = self.stack.get_page(page)
        stack_page.set_title("音效设置")
        stack_page.set_icon_name("multimedia-volume-control-symbolic")

    def _build_log_page(self) -> None:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="协议日志", xalign=0.0, hexpand=True)
        title.add_css_class("title-2")
        clear = Gtk.Button(label="清空")
        clear.connect("clicked", lambda _button: self._clear_log())
        toolbar.append(title)
        toolbar.append(clear)
        page.append(toolbar)
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_cursor_visible(False)
        self.log_view.set_monospace(True)
        self.log_view.add_css_class("log-view")
        self.log_buffer = self.log_view.get_buffer()
        scroll.set_child(self.log_view)
        page.append(scroll)
        self.stack.add_named(page, "log")
        stack_page = self.stack.get_page(page)
        stack_page.set_title("日志")
        stack_page.set_icon_name("document-open-symbolic")

    def _start_demo(self) -> None:
        self.client = DemoSession()
        self.device = Device(
            path="/demo/redmi-buds-8-pro",
            address="24:B2:31:96:68:50",
            name="REDMI Buds 8 Pro（演示）",
            connected=True,
            paired=True,
            trusted=True,
            service_uuids=(),
        )
        self.devices = [self.device]
        self.channel = 28
        self._populate_devices(self.devices)
        self.last_state = self.client.read_state()
        self._apply_state(self.last_state)
        self._set_connected(True)
        self.connect_button.set_label("演示已连接")
        self.connect_button.set_sensitive(False)
        self.refresh_button.set_sensitive(False)
        self.device_dropdown.set_sensitive(False)
        self._show_banner("演示模式：所有设置修改仅在本地模拟，不会发送到耳机。")
        self._append_log("DEMO session ready")

    def refresh_devices(self) -> None:
        if self.demo or self.busy:
            return
        self._run_worker(
            "正在扫描已配对设备",
            self._scan_devices,
            self._populate_devices,
        )

    def _scan_devices(self) -> list[Device]:
        return [
            device for device in BlueZ().list_devices()
            if device.paired and any(
                word in device.name.casefold() for word in ("redmi", "xiaomi", "buds")
            )
        ]

    def _populate_devices(self, devices: list[Device]) -> None:
        self.devices = devices
        self.syncing = True
        self.device_model = Gtk.StringList()
        self.device_dropdown.set_model(self.device_model)
        for device in devices:
            state = "已连接" if device.connected else "未连接"
            self.device_model.append(f"{device.name} · {state} · {device.address}")
        self.device_dropdown.set_selected(0 if devices else Gtk.INVALID_LIST_POSITION)
        self.syncing = False
        if not devices:
            self._show_banner("没有找到已配对的小米 / Redmi 耳机，请先在系统蓝牙中配对。")

    def _on_device_selected(self, _dropdown, _param) -> None:
        if self.syncing or self.busy:
            return

    def _selected_device(self) -> Device | None:
        index = self.device_dropdown.get_selected()
        if 0 <= index < len(self.devices):
            return self.devices[index]
        return None

    def _on_connect_clicked(self, _button) -> None:
        if self.demo:
            return
        if self.client is not None:
            self._run_worker("正在关闭会话", self._disconnect, lambda: self._after_disconnect())
            return
        device = self._selected_device()
        if device is None:
            self._show_banner("请先选择一款已配对耳机。")
            return
        self._run_worker(
            f"正在连接 {device.name}",
            lambda: self._connect(device),
            self._after_connect,
        )

    def _connect(self, selected: Device) -> tuple[Device, int, RcspClient, dict[str, object]]:
        bluez = BlueZ()
        device = bluez.ensure_connected(selected)
        channel = find_rfcomm_channel(device.address)
        transport = RfcommTransport(device.address, channel, timeout=1.0)
        client = RcspClient(transport, timeout=8.0, logger=self._log_from_worker)
        try:
            client.start()
            auth = client.authenticate()
            state = client.read_state()
        except BaseException:
            client.close()
            raise
        self._log_from_worker(f"AUTH confirmed via {auth}; RFCOMM channel={channel}")
        return device, channel, client, state

    def _after_connect(self, result: tuple[Device, int, RcspClient, dict[str, object]]) -> None:
        self.device, self.channel, self.client, self.last_state = result
        self._apply_state(self.last_state)
        self._set_connected(True)
        self.device_dropdown.set_sensitive(False)
        self._show_banner("")
        if self.last_state.get("errors"):
            self._show_banner("连接成功，但部分状态读取失败；详情请查看协议日志。")

    def _disconnect(self) -> None:
        if self.client is not None:
            self.client.close()
        return None

    def _after_disconnect(self) -> None:
        self.client = None
        self.device = None
        self.channel = None
        self.last_state = {}
        self._set_connected(False)
        self.device_dropdown.set_sensitive(True)
        self._apply_state({})
        self._append_log("SESSION closed; system Bluetooth audio was not disconnected")

    def _on_anc_clicked(self, mode: int) -> None:
        if self.syncing or self.busy or self.client is None:
            self._restore_anc_button()
            return
        self._run_worker(
            "正在设置降噪模式",
            lambda: self._write_anc(mode),
            self._apply_state,
            lambda _error: self._apply_state(self.last_state),
        )

    def _write_anc(self, mode: int) -> dict[str, object]:
        assert self.client is not None
        self.client.set_anc_mode(mode)
        time.sleep(0.3)
        return self.client.read_state()

    def _on_low_latency_changed(self, _row, _param) -> None:
        if self.syncing or self.busy or self.client is None:
            return
        enabled = self.low_latency_row.get_active()
        self._run_worker(
            "正在设置低延迟",
            lambda: self._write_low_latency(enabled),
            self._apply_state,
            lambda _error: self._apply_state(self.last_state),
        )

    def _write_low_latency(self, enabled: bool) -> dict[str, object]:
        assert self.client is not None
        self.client.set_low_latency(enabled)
        time.sleep(0.3)
        return self.client.read_state()

    def _apply_state(self, state: dict[str, object]) -> None:
        self.last_state = state
        self.syncing = True
        try:
            info = state.get("device_info", {})
            if isinstance(info, dict):
                batteries = info.get("battery", {})
                if isinstance(batteries, dict):
                    for key, (level_label, state_label, bar) in self.battery_widgets.items():
                        reading = batteries.get(key)
                        if isinstance(reading, (list, tuple)) and reading:
                            level = int(reading[0])
                            charging = bool(reading[1]) if len(reading) > 1 else False
                            level_label.set_label(f"{level}%")
                            state_label.set_label("充电中" if charging else "未充电")
                            bar.set_value(level)
                        else:
                            level_label.set_label("--")
                            state_label.set_label("无读数")
                            bar.set_value(0)
                firmware = info.get("firmware_1")
                self.firmware_row.set_subtitle(str(firmware or "--"))
                vid = info.get("vid")
                pid = info.get("pid")
                self.vidpid_row.set_subtitle(f"{vid or '--'} / {pid or '--'}")
            run_info = state.get("run_info", {})
            mode = run_info.get("anc_mode") if isinstance(run_info, dict) else None
            if mode in self.anc_buttons:
                self.anc_buttons[int(mode)].set_active(True)
                self.noise_summary.set_subtitle({
                    RcspAncModes.OFF: "环境声已关闭",
                    RcspAncModes.NOISE_CANCELLING: "降噪已开启",
                    RcspAncModes.TRANSPARENCY: "通透已开启",
                }[RcspAncModes(mode)])
            configs = state.get("configs", {})
            low_latency = None
            if isinstance(configs, dict):
                entry = configs.get("0x2F")
                if isinstance(entry, dict):
                    value = str(entry.get("value", ""))
                    if value:
                        low_latency = int(value[:2], 16) != 0
            if low_latency is not None:
                self.low_latency = low_latency
                self.low_latency_row.set_active(low_latency)
            self.channel_row.set_subtitle(str(self.channel or "--"))
        finally:
            self.syncing = False

    def _restore_anc_button(self) -> None:
        mode = self.last_state.get("run_info", {}).get("anc_mode", 0)
        self.syncing = True
        try:
            self.anc_buttons[int(mode)].set_active(True)
        finally:
            self.syncing = False

    def _set_connected(self, connected: bool) -> None:
        control_sensitive = connected and not self.busy
        self.header_status.set_label("已连接" if connected else "未连接")
        self.header_status.remove_css_class("status-connected")
        self.header_status.remove_css_class("status-disconnected")
        self.header_status.add_css_class("status-connected" if connected else "status-disconnected")
        self.connection_status_row.set_subtitle("RCSP 会话已连接" if connected else "请选择耳机后连接")
        self.connect_button.set_label("断开连接" if connected else "连接")
        for button in self.anc_buttons.values():
            button.set_sensitive(control_sensitive)
        self.low_latency_row.set_sensitive(control_sensitive)

    def _run_worker(
        self,
        description: str,
        work: Callable[[], object],
        on_success: Callable[[object], None],
        on_failure: Callable[[BaseException], None] | None = None,
    ) -> None:
        if self.busy:
            return
        self.busy = True
        self.refresh_button.set_sensitive(False)
        self.connect_button.set_sensitive(False)
        self.device_dropdown.set_sensitive(False)
        self._set_connected(self.client is not None)
        self._append_log(description)

        def target() -> None:
            result = None
            error: BaseException | None = None
            try:
                result = work()
            except BaseException as exc:
                error = exc
            GLib.idle_add(self._worker_done, description, result, error, on_success, on_failure)

        threading.Thread(target=target, name="mibuds-gui-worker", daemon=True).start()

    def _worker_done(
        self,
        description: str,
        result: object,
        error: BaseException | None,
        on_success: Callable[[object], None],
        on_failure: Callable[[BaseException], None] | None,
    ) -> bool:
        self.busy = False
        self.refresh_button.set_sensitive(not self.demo)
        self.connect_button.set_sensitive(not self.demo)
        self.device_dropdown.set_sensitive(not self.demo and self.client is None)
        self._set_connected(self.client is not None)
        if error is not None:
            message = str(error) or error.__class__.__name__
            self._show_banner(f"{description}失败：{message}")
            self._append_log(f"ERROR {description}: {message}")
            if on_failure is not None:
                on_failure(error)
        else:
            try:
                on_success(result)
            except Exception as exc:
                self._show_banner(f"界面更新失败：{exc}")
        return False

    def _show_banner(self, message: str) -> None:
        self.banner.set_title(message)
        self.banner.set_revealed(bool(message))

    def _log_from_worker(self, message: str) -> None:
        GLib.idle_add(self._append_log, message)

    def _append_log(self, message: str) -> bool:
        end = self.log_buffer.get_end_iter()
        self.log_buffer.insert(end, message + "\n")
        self._log_lines += 1
        if self._log_lines > 2000:
            start = self.log_buffer.get_start_iter()
            remove_end = self.log_buffer.get_iter_at_line_offset(self._log_lines - 2000, 0)
            self.log_buffer.delete(start, remove_end)
            self._log_lines = 2000
        return False

    def _clear_log(self) -> None:
        self.log_buffer.set_text("")
        self._log_lines = 0

    def shutdown_session(self) -> None:
        if self.client is not None:
            self.client.close()
        self.client = None


class MiBudsApplication(Adw.Application):
    def __init__(self, demo: bool = False) -> None:
        super().__init__(application_id="org.mibudsprobe.Controller")
        self.demo = demo

    def do_activate(self) -> None:
        window = self.get_active_window()
        if window is None:
            window = ControllerWindow(demo=self.demo)
            window.set_application(self)
            window.connect("close-request", self._on_close)
        window.present()

    def _on_close(self, window: ControllerWindow) -> bool:
        window.shutdown_session()
        return False


def run_gui(demo: bool = False) -> int:
    app = MiBudsApplication(demo=demo)
    return app.run([])
