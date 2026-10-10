#!/usr/bin/env python3
"""Lock the PC when a Bluetooth device is too far away."""

import argparse
import asyncio
import platform
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from bleak import BleakScanner

from bluelock import (
    AppConfig,
    LockPolicy,
    MonitorStatus,
    bluetooth_connected,
    bluetooth_connect,
    bluetooth_link_rssi,
    default_config_path,
    estimate_distance_m,
    load_config,
    matches_device,
    write_starter_config,
)


def default_lock_command() -> Optional[str]:
    system = platform.system().lower()
    if system == "windows":
        return "rundll32.exe user32.dll,LockWorkStation"
    if system == "darwin":
        # Fallback only: needs Accessibility permission. lock_pc() tries mac_lock_screen() first.
        return "osascript -e 'tell application \"System Events\" to keystroke \"q\" using {control down, command down}'"
    if system == "linux":
        return "loginctl lock-session || gnome-screensaver-command -l || dm-tool lock"
    return None


def mac_lock_screen() -> bool:
    """Lock immediately via login.framework (what the Lock Screen menu item uses).

    Needs no Accessibility permission, unlike the keystroke fallback.
    """
    import ctypes

    try:
        login = ctypes.CDLL("/System/Library/PrivateFrameworks/login.framework/Versions/Current/login")
        login.SACLockScreenImmediate()
    except (OSError, AttributeError) as exc:
        print(f"Native macOS lock unavailable ({exc}); falling back to keystroke.", file=sys.stderr)
        return False
    return True


def lock_pc(command_override: Optional[str]) -> None:
    if not command_override and platform.system().lower() == "darwin" and mac_lock_screen():
        return
    command = command_override or default_lock_command()
    if not command:
        print("No lock command configured for this OS.", file=sys.stderr)
        return
    try:
        subprocess.run(command, shell=True, check=False)
    except Exception as exc:  # pragma: no cover
        print(f"Failed to run lock command: {exc}", file=sys.stderr)


def debug_notice(reason: str) -> None:
    print(f"[debug] {reason}")
    system = platform.system().lower()
    if system == "windows":
        try:
            import tkinter
            from tkinter import messagebox

            root = tkinter.Tk()
            root.withdraw()
            messagebox.showinfo("BlueLock Debug", reason)
            root.destroy()
        except Exception:
            pass
        return
    if system == "darwin":
        # Pass the text as an argument so quotes in it can't break the script.
        subprocess.run(
            [
                "osascript",
                "-e", "on run argv",
                "-e", 'display notification (item 1 of argv) with title "BlueLock Debug"',
                "-e", "end run",
                reason,
            ],
            check=False,
        )
        return
    try:
        subprocess.run(
            ["zenity", "--info", "--title", "BlueLock Debug", "--text", reason],
            check=False,
        )
    except FileNotFoundError:
        try:
            subprocess.run(
                ["notify-send", "BlueLock Debug", reason],
                check=False,
            )
        except FileNotFoundError:
            pass


def maybe_lock(config: AppConfig, reason: str) -> None:
    if config.lock.debug:
        debug_notice(reason)
        return
    lock_pc(config.lock.command)


async def monitor(config: AppConfig, status: Optional[MonitorStatus] = None) -> None:
    distances: list[float] = []
    policy = LockPolicy(config.lock)
    last_seen: Optional[float] = None
    last_rssi: Optional[int] = None
    last_addr: Optional[str] = None
    last_name: Optional[str] = None
    last_reconnect_attempt: Optional[float] = None
    reconnect_cooldown_s = 30.0
    device_event = asyncio.Event()

    def on_detect(device, advertisement_data) -> None:
        nonlocal last_seen, last_rssi, last_addr, last_name
        if not matches_device(device.address, device.name, config.device.address, config.device.name):
            return
        # Any matching advertisement proves the device is present, even without RSSI.
        last_seen = time.monotonic()
        rssi = advertisement_data.rssi
        if rssi is None and config.lock.rssi_fallback is not None:
            rssi = int(config.lock.rssi_fallback)
        if rssi is not None:
            distance = estimate_distance_m(
                rssi,
                config.bluetooth.tx_power,
                config.bluetooth.path_loss_exponent,
            )
            distances.append(distance)
            if len(distances) > config.lock.samples:
                distances.pop(0)
            last_rssi = rssi
        last_addr = device.address
        last_name = device.name
        device_event.set()

    def describe(reason: str, connected: Optional[bool], link_rssi: Optional[int]) -> str:
        details = [reason]
        if connected is not None:
            details.append(f"Connected: {'Yes' if connected else 'No'}.")
        if link_rssi is not None:
            details.append(
                f"Connection signal: {link_rssi} dB (lock at {config.lock.link_rssi_threshold} dB)."
            )
        if last_rssi is not None:
            details.append(f"Last RSSI: {last_rssi} dBm.")
        if last_name:
            details.append(f"Device name: {last_name}.")
        if last_addr:
            details.append(f"Device address: {last_addr}.")
        return "\n".join(details)

    scanner_instance = BleakScanner(
        detection_callback=on_detect,
        adapter=config.bluetooth.adapter,
    )
    await scanner_instance.start()
    try:
        while True:
            device_event.clear()
            received_adv = False
            try:
                await asyncio.wait_for(device_event.wait(), timeout=config.lock.check_interval_s)
                received_adv = True
            except asyncio.TimeoutError:
                pass
            now = time.monotonic()
            connected = None
            if config.device.address:
                # bluetoothctl can take seconds; keep it off the event loop so the
                # scanner callback keeps receiving advertisements.
                connected = await asyncio.to_thread(bluetooth_connected, config.device.address)
                if connected is False and (
                    last_reconnect_attempt is None or now - last_reconnect_attempt >= reconnect_cooldown_s
                ):
                    last_reconnect_attempt = now
                    await asyncio.to_thread(bluetooth_connect, config.device.address)
                if connected is True:
                    last_seen = now
            link_rssi = None
            if connected is True and config.lock.link_rssi_threshold is not None:
                link_rssi = await asyncio.to_thread(bluetooth_link_rssi, config.device.address)
            if policy.is_missing(now, last_seen):
                # Old samples must not decide the distance once the device returns.
                distances.clear()
            median_distance = statistics.median(distances) if distances and received_adv else None
            reason = policy.evaluate(now, last_seen, median_distance, link_rssi)
            if status is not None:
                status.last_seen = None if last_seen is None else time.time() - (now - last_seen)
                status.rssi = last_rssi
                status.distance_m = statistics.median(distances) if distances else None
                status.name = last_name
                status.address = last_addr
                status.link_rssi = link_rssi
            if reason:
                await asyncio.to_thread(maybe_lock, config, describe(reason, connected, link_rssi))
    finally:
        await scanner_instance.stop()


def main() -> int:
    # Under systemd stdout is a pipe; without this, log lines sit in the buffer
    # instead of reaching the journal (PyInstaller ignores PYTHONUNBUFFERED).
    sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description="Lock the PC when a BLE device is too far away.")
    parser.add_argument(
        "config",
        nargs="?",
        default=str(default_config_path()),
        help="Path to config TOML (default: ~/.config/bluelock/config.toml)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Debug mode: never lock, only print reasons.",
    )
    args = parser.parse_args()

    config_path = Path(args.config).expanduser()
    if write_starter_config(config_path):
        print(f"Created starter config: {config_path}")

    config = load_config(config_path)
    if args.debug:
        config.lock.debug = True
    if not config.device.address and not config.device.name:
        # Exiting would make systemd restart us every few seconds. Stay idle until
        # the tray binds a device, which restarts this service.
        print("No device configured. Pick one in the tray (Bind Device) or set "
              f"device.address / device.name in {config_path}. Waiting.")
        threading.Event().wait()
        return 0

    print("Starting Bluetooth lock monitor...")
    print(f"Lock distance: {config.lock.distance_m}m (hysteresis {config.lock.hysteresis_m}m)")
    print(f"Debug mode: {'on' if config.lock.debug else 'off'}")

    asyncio.run(monitor(config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
