#!/usr/bin/env python3
"""CLI for the BlueLock deb: controls the packaged daemon and tray."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from subprocess import run

LIB_DIR = Path("/usr/lib/bluelock")
DAEMON = LIB_DIR / "bluelock"
TRAY = LIB_DIR / "bluelock-tray"
CONFIG_PATH = Path("~/.config/bluelock/config.toml").expanduser()
USER_UNIT_DIR = Path("~/.config/systemd/user").expanduser()
LEGACY_SHARE_DIR = Path("~/.local/share/bluelock").expanduser()
SERVICES = ("bluelock.service", "bluelock-tray.service")


def systemctl_user(*args: str) -> None:
    run(["systemctl", "--user", *args], check=False)


def remove_legacy_units() -> None:
    """Drop per-user units from older installs that ran self-built binaries.

    Units in ~/.config/systemd/user override the packaged ones in
    /usr/lib/systemd/user, so they would keep running the old binaries.
    """
    for service in SERVICES:
        unit = USER_UNIT_DIR / service
        if unit.is_file() and str(LEGACY_SHARE_DIR) in unit.read_text():
            unit.unlink()
            print(f"Removed old per-user unit {unit}")


def install() -> None:
    remove_legacy_units()
    systemctl_user("daemon-reload")
    for service in SERVICES:
        systemctl_user("enable", service)
        systemctl_user("restart", service)


def status() -> None:
    for service, label in zip(SERVICES, ("Daemon", "Tray"), strict=True):
        result = run(["systemctl", "--user", "is-active", service], check=False, capture_output=True, text=True)
        print(f"{label}: {result.stdout.strip() or 'unknown'}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="bluelock", description="Lock your PC when your Bluetooth device walks away")
    sub = parser.add_subparsers(dest="cmd")
    install_parser = sub.add_parser("install", help="enable and start the services for this user")
    install_parser.add_argument("--skip-apt", action="store_true", help=argparse.SUPPRESS)
    sub.add_parser("start", help="start the daemon")
    sub.add_parser("stop", help="stop the daemon")
    sub.add_parser("restart", help="restart the daemon")
    sub.add_parser("status", help="show daemon and tray status")
    sub.add_parser("config", help="print the config path")
    sub.add_parser("run", help="run the daemon in this terminal")
    sub.add_parser("tray", help="run the tray in this terminal")
    args = parser.parse_args()

    if args.cmd == "install":
        install()
    elif args.cmd in ("start", "stop", "restart"):
        systemctl_user(args.cmd, "bluelock.service")
    elif args.cmd == "status":
        status()
    elif args.cmd == "config":
        print(CONFIG_PATH)
    elif args.cmd == "run":
        os.execv(DAEMON, [str(DAEMON), str(CONFIG_PATH)])
    elif args.cmd == "tray":
        os.execv(TRAY, [str(TRAY)])
    else:
        parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
