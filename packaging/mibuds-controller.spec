Name:           mibuds-controller
Version:        0.2.0
Release:        1%{?dist}
Summary:        Linux controller for Redmi Buds 8 Pro

License:        MIT
URL:            https://github.com/FallenLeeee/MiBudsController
BuildArch:      noarch
Source0:        %{name}-%{version}.tar.gz

Requires:       python3
Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita
Requires:       python3-dbus
Requires:       bluez-libs
Requires:       bluez

%description
Unofficial Linux controller for Redmi Buds 8 Pro and compatible
Xiaomi / Redmi earbuds. Uses BlueZ and the RCSP protocol over
Bluetooth Classic RFCOMM.

%prep
%setup -q
%build
%install
mkdir -p %{buildroot}%{_bindir}
mkdir -p %{buildroot}%{_libdir}/mibuds-controller
mkdir -p %{buildroot}%{_datadir}/applications
mkdir -p %{buildroot}%{_datadir}/icons/hicolor/scalable/apps
cp -a %{_builddir}/%{name}-%{version}/. %{buildroot}%{_libdir}/mibuds-controller/
install -m 0755 packaging/mibuds-gui %{buildroot}%{_bindir}/mibuds-gui
install -m 0755 packaging/mibuds-probe %{buildroot}%{_bindir}/mibuds-probe
install -m 0644 packaging/mibuds-controller.desktop %{buildroot}%{_datadir}/applications/mibuds-controller.desktop
install -m 0644 packaging/mibuds-controller.svg %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/mibuds-controller.svg

%files
%{_bindir}/mibuds-gui
%{_bindir}/mibuds-probe
%{_libdir}/mibuds-controller
%{_datadir}/applications/mibuds-controller.desktop
%{_datadir}/icons/hicolor/scalable/apps/mibuds-controller.svg

%changelog
* Wed Nov 25 2026 MiBudsController Linux Contributors - 0.2.0-1
- Add automatic reconnect, system tray, mini panel and autostart
- Add PipeWire codec status and EasyEffects integration
