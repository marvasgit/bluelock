#!/usr/bin/env python3
"""Tray UI for controlling the Bluetooth lock service on Ubuntu."""

import asyncio
import subprocess
import sys
import threading
import time
from typing import Optional

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

try:
    import gi
    gi.require_version("AyatanaAppIndicator3", "0.1")
    gi.require_version("Gtk", "3.0")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator3
    from gi.repository import Gtk
    from gi.repository import GLib
except Exception as exc:
    print("Missing GTK/AppIndicator deps. Install: sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1", file=sys.stderr)
    raise SystemExit(1) from exc

from bleak import BleakScanner

from bluelock import (
    KnownDevice,
    bind_device,
    is_bound,
    list_known_devices,
    bluetooth_connected,
    bluetooth_connect,
    bluetooth_link_rssi,
    calibrate_distance,
    calibrate_link_rssi,
    default_config_path,
    estimate_distance_m,
    matches_device,
    normalize_bt_address,
    set_config_value,
)

CONFIG_PATH = default_config_path()
SERVICE_NAME = "bluelock.service"
ICON_CANDIDATES = (
    "bluetooth-active",
    "bluetooth",
    "bluetooth-symbolic",
    "network-wireless",
    "network-wireless-signal-excellent-symbolic",
)


def ensure_config() -> None:
    if CONFIG_PATH.exists():
        return
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        "[device]\naddress = \"AA:BB:CC:DD:EE:FF\"\n\n"
        "[lock]\ndistance_m = 3.0\n\n"
        "[bluetooth]\ntx_power = -59\npath_loss_exponent = 2.2\n"
    )


def load_config() -> dict:
    ensure_config()
    return tomllib.loads(CONFIG_PATH.read_text())


def systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], check=False, capture_output=True, text=True)


def update_debug(enabled: bool) -> None:
    ensure_config()
    set_config_value(CONFIG_PATH, "lock", "debug", bool(enabled))
    systemctl("restart", SERVICE_NAME)


def service_active() -> bool:
    result = systemctl("is-active", SERVICE_NAME)
    return result.returncode == 0


def open_config() -> None:
    try:
        subprocess.run(["xdg-open", str(CONFIG_PATH)], check=False)
    except FileNotFoundError:
        try:
            subprocess.run(["gnome-text-editor", str(CONFIG_PATH)], check=False)
        except FileNotFoundError:
            try:
                subprocess.run(["gedit", str(CONFIG_PATH)], check=False)
            except FileNotFoundError:
                pass


def show_info(text: str) -> None:
    dialog = Gtk.MessageDialog(
        transient_for=None,
        flags=0,
        message_type=Gtk.MessageType.INFO,
        buttons=Gtk.ButtonsType.OK,
        text=text,
    )
    dialog.run()
    dialog.destroy()


def link_threshold(data: dict) -> Optional[int]:
    raw = data.get("lock", {}).get("link_rssi_threshold", -10)
    return None if raw is False else int(raw)


def show_error(text: str) -> None:
    dialog = Gtk.MessageDialog(
        transient_for=None,
        flags=0,
        message_type=Gtk.MessageType.ERROR,
        buttons=Gtk.ButtonsType.OK,
        text=text,
    )
    dialog.run()
    dialog.destroy()


def main() -> int:
    icon_name = resolve_indicator_icon()
    indicator = AppIndicator3.Indicator.new(
        "bluelock",
        icon_name,
        AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
    )
    indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
    indicator.set_menu(build_menu(indicator))
    Gtk.main()
    return 0


def resolve_indicator_icon() -> str:
    theme = Gtk.IconTheme.get_default()
    if theme is None:
        return "bluetooth"
    for name in ICON_CANDIDATES:
        if theme.has_icon(name):
            return name
    return "bluetooth"


def build_menu(indicator: AppIndicator3.Indicator) -> Gtk.Menu:
    menu = Gtk.Menu()
    status_item = Gtk.MenuItem(label="Status: Active" if service_active() else "Status: Inactive")
    status_item.set_sensitive(False)
    menu.append(status_item)
    distance_value = load_config().get("lock", {}).get("distance_m", 3.0)
    distance_item = Gtk.MenuItem(label=f"Lock Distance: {distance_value:.2f} m")
    distance_item.set_sensitive(False)
    menu.append(distance_item)
    current_distance_item = Gtk.MenuItem(label="Current Distance: --")
    current_distance_item.set_sensitive(False)
    menu.append(current_distance_item)
    last_seen_item = Gtk.MenuItem(label="Last Seen: --")
    last_seen_item.set_sensitive(False)
    menu.append(last_seen_item)
    device_info_item = Gtk.MenuItem(label="Device: --")
    device_info_item.set_sensitive(False)
    menu.append(device_info_item)
    connected_item = Gtk.MenuItem(label="Connected: --")
    connected_item.set_sensitive(False)
    menu.append(connected_item)

    def refresh_status() -> None:
        status_item.set_label("Status: Active" if service_active() else "Status: Inactive")

    def refresh_status_timer() -> bool:
        refresh_status()
        return True

    async def scan_device_info(data: dict) -> tuple[Optional[int], Optional[str], Optional[str], Optional[float]]:
        device = data.get("device", {})
        target_addr = device.get("address")
        target_name = device.get("name")
        bluetooth = data.get("bluetooth", {})
        adapter = bluetooth.get("adapter")
        tx_power = int(bluetooth.get("tx_power", -59))
        path_loss_exponent = float(bluetooth.get("path_loss_exponent", 2.2))

        devices = await BleakScanner.discover(timeout=1.5, adapter=adapter, return_adv=True)
        for dev, adv in devices.values():
            if matches_device(dev.address, dev.name, target_addr, target_name):
                if adv.rssi is None:
                    return None, dev.address, dev.name, None
                distance = estimate_distance_m(adv.rssi, tx_power, path_loss_exponent)
                return adv.rssi, dev.address, dev.name, distance
        return None, None, None, None

    last_seen_ts: Optional[float] = None
    scan_in_flight = False

    # BLE scans and bluetoothctl calls take seconds; run them on a worker thread
    # and apply results on the GTK main loop so the menu never freezes.
    def scan_worker() -> None:
        data = load_config()
        address = data.get("device", {}).get("address")
        connected = bluetooth_connected(address)
        link_rssi = bluetooth_link_rssi(address) if connected else None
        try:
            result = asyncio.run(scan_device_info(data))
        except Exception as exc:
            print(f"Device scan failed: {exc}", file=sys.stderr)
            result = (None, None, None, None)
        GLib.idle_add(apply_device_info, data, connected, result, link_rssi)

    def refresh_device_info() -> bool:
        nonlocal scan_in_flight
        if not scan_in_flight:
            scan_in_flight = True
            threading.Thread(target=scan_worker, daemon=True).start()
        return True

    def apply_device_info(
        data: dict,
        connected: Optional[bool],
        result: tuple[Optional[int], Optional[str], Optional[str], Optional[float]],
        link_rssi: Optional[int] = None,
    ) -> bool:
        nonlocal last_seen_ts, scan_in_flight
        scan_in_flight = False
        rssi, addr, name, distance = result
        device_cfg = data.get("device", {})
        if addr or name:
            last_seen_ts = time.time()
            parts = []
            if name:
                parts.append(name)
            if addr:
                parts.append(addr)
            if rssi is not None:
                parts.append(f"{rssi} dBm")
            device_info_item.set_label(f"Device: {' | '.join(parts)}")
        else:
            configured_name = device_cfg.get("name")
            configured_addr = device_cfg.get("address")
            if configured_name or configured_addr:
                parts = []
                if configured_name:
                    parts.append(str(configured_name))
                if configured_addr:
                    parts.append(str(configured_addr))
                device_info_item.set_label(f"Device: {' | '.join(parts)}")
            else:
                device_info_item.set_label("Device: --")

            lock_cfg = data.get("lock", {})
            stale_after = float(lock_cfg.get("missing_timeout_s", 8.0)) * 2.0
            stale_after = max(stale_after, 15.0)
            if connected is not True and last_seen_ts is not None and (time.time() - last_seen_ts) > stale_after:
                last_seen_ts = None
        if distance is not None:
            current_distance_item.set_label(f"Current Distance: {distance:.2f} m")
        elif link_rssi is not None:
            # No BLE distance (e.g. iPhone): show the connection signal the daemon locks on.
            threshold = link_threshold(data)
            lock_at = f", locks at {threshold} dB" if threshold is not None else ""
            current_distance_item.set_label(f"Connection Signal: {link_rssi} dB{lock_at}")
        elif connected is True:
            current_distance_item.set_label("Current Distance: N/A (BLE idle)")
        else:
            current_distance_item.set_label("Current Distance: --")
        if last_seen_ts is None and connected is True:
            last_seen_item.set_label("Last Seen: connected (no BLE adv)")
        elif last_seen_ts is None:
            last_seen_item.set_label("Last Seen: --")
        else:
            age_s = time.time() - last_seen_ts
            last_seen_item.set_label(f"Last Seen: {age_s:.1f}s ago")

        if connected is None:
            connected_item.set_label("Connected: --")
        else:
            connected_item.set_label("Connected: Yes" if connected else "Connected: No")
        return False

    start_item = Gtk.MenuItem(label="Start")
    start_item.connect("activate", lambda *_: (systemctl("start", SERVICE_NAME), refresh_status()))
    menu.append(start_item)
    stop_item = Gtk.MenuItem(label="Stop")
    stop_item.connect("activate", lambda *_: (systemctl("stop", SERVICE_NAME), refresh_status()))
    menu.append(stop_item)
    restart_item = Gtk.MenuItem(label="Restart")
    restart_item.connect("activate", lambda *_: (systemctl("restart", SERVICE_NAME), refresh_status()))
    menu.append(restart_item)
    reconnect_item = Gtk.MenuItem(label="Reconnect Device")

    def on_reconnect(_: Gtk.MenuItem) -> None:
        data = load_config()
        device_cfg = data.get("device", {})
        raw_addr = device_cfg.get("address")
        addr = normalize_bt_address(raw_addr)
        if addr is None:
            show_error(f"Reconnect failed: invalid device.address ({raw_addr!r}).")
            return
        reconnect_item.set_sensitive(False)

        def worker() -> None:
            ok = bluetooth_connect(addr)
            GLib.idle_add(done, ok)

        def done(ok: bool) -> bool:
            reconnect_item.set_sensitive(True)
            if not ok:
                show_error(f"Reconnect failed for {addr}. Ensure the phone is paired, trusted, and nearby.")
            return False

        threading.Thread(target=worker, daemon=True).start()

    reconnect_item.connect("activate", on_reconnect)
    menu.append(reconnect_item)

    device_menu = Gtk.Menu()
    bind_item = Gtk.MenuItem(label="Bind Device")
    bind_item.set_submenu(device_menu)
    known_devices: list[KnownDevice] = []
    listing_in_flight = False

    def on_bind(_item: Gtk.MenuItem, dev: KnownDevice) -> None:
        ensure_config()
        try:
            bind_device(CONFIG_PATH, dev)
        except (OSError, ValueError) as exc:
            show_error(f"Could not bind {dev.label}: {exc}")
            return
        systemctl("restart", SERVICE_NAME)
        refresh_status()
        refresh_device_info()
        # Re-render after the menu closes so the radio mark moves to the new device.
        GLib.idle_add(populate_device_menu, known_devices, True)

    def populate_device_menu(devices: list[KnownDevice], force: bool = False) -> bool:
        nonlocal known_devices, listing_in_flight
        listing_in_flight = False
        # Rebuilding an open menu makes it flicker or close; only redraw on change.
        if not force and devices == known_devices and device_menu.get_children():
            return False
        known_devices = devices
        for child in device_menu.get_children():
            device_menu.remove(child)
        device_cfg = load_config().get("device", {})
        if not devices:
            empty = Gtk.MenuItem(label="No paired devices found")
            empty.set_sensitive(False)
            device_menu.append(empty)
        for dev in devices:
            item = Gtk.CheckMenuItem(label=dev.label)
            item.set_draw_as_radio(True)
            item.set_active(is_bound(dev, device_cfg.get("address"), device_cfg.get("name")))
            item.connect("activate", on_bind, dev)
            device_menu.append(item)
        device_menu.append(Gtk.SeparatorMenuItem())
        refresh_list_item = Gtk.MenuItem(label="Refresh List")
        refresh_list_item.connect("activate", lambda *_: refresh_device_list())
        device_menu.append(refresh_list_item)
        device_menu.show_all()
        return False

    def list_worker() -> None:
        try:
            devices = list_known_devices()
        except Exception as exc:
            print(f"Listing Bluetooth devices failed: {exc}", file=sys.stderr)
            devices = []
        GLib.idle_add(populate_device_menu, devices)

    def refresh_device_list() -> bool:
        nonlocal listing_in_flight
        if not listing_in_flight:
            listing_in_flight = True
            threading.Thread(target=list_worker, daemon=True).start()
        return True

    menu.append(bind_item)
    set_distance_item = Gtk.MenuItem(label="Set Distance…")

    def on_set_distance(_: Gtk.MenuItem) -> None:
        dialog = Gtk.Dialog(title="Set Lock Distance", flags=0)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Save", Gtk.ResponseType.OK)
        box = dialog.get_content_area()

        entry = Gtk.Entry()
        entry.set_text(str(load_config().get("lock", {}).get("distance_m", 3.0)))
        entry.set_activates_default(True)
        box.add(Gtk.Label(label="Distance in meters:"))
        box.add(entry)
        dialog.set_default_response(Gtk.ResponseType.OK)
        dialog.show_all()

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            try:
                value = float(entry.get_text())
                if value > 0:
                    ensure_config()
                    set_config_value(CONFIG_PATH, "lock", "distance_m", float(value))
                    systemctl("restart", SERVICE_NAME)
                    distance_item.set_label(f"Lock Distance: {value:.2f} m")
            except ValueError:
                pass
        dialog.destroy()

    set_distance_item.connect("activate", on_set_distance)
    menu.append(set_distance_item)
    calibrate_item = Gtk.MenuItem(label="Calibrate Distance…")

    def on_calibrate(_: Gtk.MenuItem) -> None:
        data = load_config()
        device = data.get("device", {})
        target_addr = device.get("address")
        target_name = device.get("name")
        if not target_addr and not target_name:
            show_error("Set device.address or device.name in config before calibrating.")
            return

        bluetooth_cfg = data.get("bluetooth", {})
        adapter = bluetooth_cfg.get("adapter")
        tx_power = int(bluetooth_cfg.get("tx_power", -59))
        path_loss_exponent = float(bluetooth_cfg.get("path_loss_exponent", 2.2))

        # Calibration scans for ~15s; keep the tray responsive meanwhile.
        calibrate_item.set_sensitive(False)
        calibrate_item.set_label("Calibrating…")

        def worker() -> None:
            try:
                distance = asyncio.run(
                    calibrate_distance(target_addr, target_name, adapter, tx_power, path_loss_exponent)
                )
            except Exception as exc:
                print(f"Calibration failed: {exc}", file=sys.stderr)
                distance = None
            # Devices that never show up in BLE scans (iPhones) can still be
            # calibrated on the signal of their live connection.
            link = calibrate_link_rssi(target_addr) if distance is None else None
            GLib.idle_add(done, distance, link)

        def done(distance: Optional[float], link: Optional[int]) -> bool:
            calibrate_item.set_sensitive(True)
            calibrate_item.set_label("Calibrate Distance…")
            if distance is None and link is not None and link >= -1:
                show_error(
                    f"Connection signal is still strong ({link} dB), so it can't mark a lock boundary.\n\n"
                    "Leave the phone where you want BlueLock to lock (e.g. the doorway), "
                    "then click Calibrate again from the computer."
                )
                return False
            if distance is None and link is not None:
                ensure_config()
                set_config_value(CONFIG_PATH, "lock", "link_rssi_threshold", link)
                systemctl("restart", SERVICE_NAME)
                show_info(
                    f"Lock signal set to {link} dB.\n\n"
                    "BlueLock locks when the connection signal stays at or below this."
                )
                return False
            if distance is None:
                show_error("Calibration failed: device not found and not connected.")
                return False
            ensure_config()
            set_config_value(CONFIG_PATH, "lock", "distance_m", float(round(distance, 2)))
            systemctl("restart", SERVICE_NAME)
            distance_item.set_label(f"Lock Distance: {distance:.2f} m")
            return False

        threading.Thread(target=worker, daemon=True).start()

    calibrate_item.connect("activate", on_calibrate)
    menu.append(calibrate_item)
    debug_enabled = bool(load_config().get("lock", {}).get("debug", False))
    debug_item = Gtk.CheckMenuItem(label="Debug Mode")
    debug_item.set_active(debug_enabled)

    def on_toggle_debug(item: Gtk.CheckMenuItem) -> None:
        update_debug(item.get_active())

    debug_item.connect("activate", on_toggle_debug)
    menu.append(debug_item)
    open_item = Gtk.MenuItem(label="Open Config")
    open_item.connect("activate", lambda *_: open_config())
    menu.append(open_item)
    quit_item = Gtk.MenuItem(label="Quit")
    quit_item.connect("activate", lambda *_: Gtk.main_quit())
    menu.append(quit_item)
    refresh_status()
    GLib.timeout_add_seconds(2, refresh_status_timer)
    refresh_device_info()
    GLib.timeout_add_seconds(5, refresh_device_info)
    refresh_device_list()
    GLib.timeout_add_seconds(15, refresh_device_list)
    menu.show_all()
    return menu


if __name__ == "__main__":
    raise SystemExit(main())
