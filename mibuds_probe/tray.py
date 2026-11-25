from __future__ import annotations

import os
import time
from typing import Callable

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib


SNI_XML = """
<node>
  <interface name=\"org.kde.StatusNotifierItem\">
    <method name=\"Activate\"/>
    <method name=\"SecondaryActivate\"/>
    <method name=\"Scroll\"><arg type=\"i\" direction=\"in\"/><arg type=\"s\" direction=\"in\"/></method>
    <method name=\"ProvideMenu\"><arg type=\"o\" direction=\"out\"/><arg type=\"a{sv}\" direction=\"out\"/></method>
    <property name=\"Category\" type=\"s\" access=\"read\"/>
    <property name=\"Id\" type=\"s\" access=\"read\"/>
    <property name=\"Title\" type=\"s\" access=\"read\"/>
    <property name=\"Status\" type=\"s\" access=\"read\"/>
    <property name=\"IconName\" type=\"s\" access=\"read\"/>
    <property name=\"AttentionIconName\" type=\"s\" access=\"read\"/>
    <property name=\"OverlayIconName\" type=\"s\" access=\"read\"/>
    <property name=\"ToolTip\" type=\"(sa{sv})\" access=\"read\"/>
    <property name=\"ItemIsMenu\" type=\"b\" access=\"read\"/>
    <signal name=\"PropertiesChanged\"><arg type=\"s\"/><arg type=\"a{sv}\"/><arg type=\"as\"/></signal>
  </interface>
</node>
"""

MENU_XML = """
<node>
  <interface name=\"com.canonical.dbusmenu\">
    <method name=\"GetVersion\"><arg type=\"u\" direction=\"out\"/></method>
    <method name=\"GetTextDirection\"><arg type=\"s\" direction=\"out\"/></method>
    <method name=\"GetStatus\"><arg type=\"s\" direction=\"out\"/></method>
    <method name=\"GetLayout\">
      <arg type=\"i\" direction=\"in\"/><arg type=\"i\" direction=\"in\"/><arg type=\"as\" direction=\"in\"/>
      <arg type=\"u\" direction=\"out\"/><arg type=\"(ia{sv})\" direction=\"out\"/>
      <arg type=\"a(ia{sv})\" direction=\"out\"/><arg type=\"a{sv}\" direction=\"out\"/>
    </method>
    <method name=\"Event\">
      <arg type=\"i\" direction=\"in\"/><arg type=\"s\" direction=\"in\"/><arg type=\"v\" direction=\"in\"/><arg type=\"u\" direction=\"in\"/>
    </method>
    <method name=\"AboutToShow\"><arg type=\"i\" direction=\"in\"/><arg type=\"b\" direction=\"out\"/></method>
    <method name=\"GetGroupProperties\"><arg type=\"ai\" direction=\"in\"/><arg type=\"as\" direction=\"in\"/><arg type=\"a(i a{sv})\" direction=\"out\"/></method>
    <signal name=\"ItemActivated\"><arg type=\"i\"/></signal>
  </interface>
</node>
"""


class TrayService:
    MENU_ITEMS = {
        1: ("打开主界面", "show"),
        2: ("迷你面板", "mini"),
        4: ("重新连接", "reconnect"),
        5: ("断开连接", "disconnect"),
        7: ("退出", "quit"),
    }

    def __init__(self, callback: Callable[[str], None]) -> None:
        self._callback = callback
        self._connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._name = f"org.kde.StatusNotifierItem-{os.getpid()}-{int(time.time())}"
        self._sni = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
        self._menu_info = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
        self._properties: dict[str, GLib.Variant] = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", "mibuds-controller"),
            "Title": GLib.Variant("s", "小米耳机控制器"),
            "Status": GLib.Variant("s", "Passive"),
            "IconName": GLib.Variant("s", "network-offline-symbolic"),
            "AttentionIconName": GLib.Variant("s", "dialog-warning-symbolic"),
            "OverlayIconName": GLib.Variant("s", ""),
            "ToolTip": GLib.Variant("(sa{sv})", ("audio-headphones-symbolic", {"title": GLib.Variant("s", "小米耳机控制器"), "text": GLib.Variant("s", "未连接")})),
            "ItemIsMenu": GLib.Variant("b", False),
        }
        self._sni_registration = self._connection.register_object("/StatusNotifierItem", self._sni, self._sni_method, self._sni_property, None)
        self._menu_registration = self._connection.register_object("/MenuBar", self._menu_info, self._menu_method, None, None)
        self._name_id = Gio.bus_own_name_on_connection(
            self._connection,
            self._name,
            Gio.BusNameOwnerFlags.NONE,
            None,
            None,
        )

    def update(self, title: str, text: str, connected: bool) -> None:
        self._properties["Title"] = GLib.Variant("s", title)
        self._properties["Status"] = GLib.Variant("s", "Active" if connected else "Passive")
        self._properties["IconName"] = GLib.Variant("s", "audio-headphones-symbolic" if connected else "network-offline-symbolic")
        self._properties["ToolTip"] = GLib.Variant("(sa{sv})", ("audio-headphones-symbolic", {"title": GLib.Variant("s", title), "text": GLib.Variant("s", text)}))
        changed = {key: self._properties[key] for key in ("Title", "Status", "IconName", "ToolTip")}
        self._connection.emit_signal(
            None,
            "/StatusNotifierItem",
            "org.kde.StatusNotifierItem",
            "PropertiesChanged",
            GLib.Variant("(sa{sv}as)", ("org.kde.StatusNotifierItem", changed, [])),
        )

    def _invoke(self, action: str) -> None:
        self._callback(action)

    def _sni_method(self, connection, sender, path, interface, method, parameters, invocation) -> None:
        if method == "Activate":
            self._invoke("show")
            invocation.return_value(None)
        elif method == "SecondaryActivate":
            self._invoke("mini")
            invocation.return_value(None)
        elif method == "Scroll":
            invocation.return_value(None)
        elif method == "ProvideMenu":
            invocation.return_value(GLib.Variant("(oa{sv})", ("/MenuBar", {})))

    def _sni_property(self, connection, sender, path, interface, property_name):
        return self._properties.get(property_name, GLib.Variant("s", ""))

    def _menu_method(self, connection, sender, path, interface, method, parameters, invocation) -> None:
        if method == "GetVersion":
            invocation.return_value(GLib.Variant("(u)", (1,)))
        elif method == "GetTextDirection":
            invocation.return_value(GLib.Variant("(s)", ("ltr",)))
        elif method == "GetStatus":
            invocation.return_value(GLib.Variant("(s)", ("normal",)))
        elif method == "GetLayout":
            entries = [
                (item_id, {"label": GLib.Variant("s", self.MENU_ITEMS[item_id][0]), "visible": GLib.Variant("b", True)})
                for item_id in sorted(self.MENU_ITEMS)
            ]
            entries.insert(2, (3, {"type": GLib.Variant("s", "separator"), "visible": GLib.Variant("b", True)}))
            entries.insert(5, (6, {"type": GLib.Variant("s", "separator"), "visible": GLib.Variant("b", True)}))
            items = [
                GLib.Variant.new_tuple(GLib.Variant("i", item_id), GLib.Variant("a{sv}", properties))
                for item_id, properties in entries
            ]
            children = GLib.Variant.new_array(items[0].get_type(), items)
            root = GLib.Variant.new_tuple(
                GLib.Variant("i", 0),
                GLib.Variant("a{sv}", {"children-display": GLib.Variant("s", "submenu")}),
            )
            invocation.return_value(GLib.Variant.new_tuple(
                GLib.Variant("u", 1),
                root,
                children,
                GLib.Variant("a{sv}", {}),
            ))
        elif method == "Event":
            item_id, event_id, _data, _timestamp = parameters.unpack()
            if event_id == "clicked" and item_id in self.MENU_ITEMS:
                connection.emit_signal(None, "/MenuBar", "com.canonical.dbusmenu", "ItemActivated", GLib.Variant("(i)", (item_id,)))
                self._invoke(self.MENU_ITEMS[item_id][1])
            invocation.return_value(None)
        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
        elif method == "GetGroupProperties":
            invocation.return_value(GLib.Variant("(a(i a{sv}))", ([],)))

    def close(self) -> None:
        Gio.bus_unown_name(self._name_id)
        self._connection.unregister_object(self._sni_registration)
        self._connection.unregister_object(self._menu_registration)
