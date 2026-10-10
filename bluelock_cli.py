#!/usr/bin/env python3
"""CLI for installing and controlling BlueLock on Ubuntu."""

from __future__ import annotations

import argparse
from pathlib import Path
from subprocess import run


PKG_ROOT = Path(__file__).resolve().parent
CONFIG_DIR = Path("~/.config/bluelock").expanduser()
CONFIG_PATH = CONFIG_DIR / "config.toml"
SHARE_DIR = Path("~/.local/share/bluelock").expanduser()
SYSTEMD_DIR = Path("~/.config/systemd/user").expanduser()
VENV_DIR = SHARE_DIR / "venv"
VENV_PY = VENV_DIR / "bin" / "python"
BIN_DIR = SHARE_DIR / "bin"
BUILD_DIR = SHARE_DIR / "build"
# The tray needs the distro's python3-gi, which Homebrew/pyenv interpreters can't see.
SYSTEM_PYTHON = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else "python3"


def systemctl_user(action: str, service: str) -> None:
    run(["systemctl", "--user", action, service], check=False)


def ensure_config() -> None:
    if CONFIG_PATH.exists():
        return
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    example = PKG_ROOT / "config.example.toml"
    if example.exists():
        CONFIG_PATH.write_text(example.read_text())


def install_venv() -> None:
    if not VENV_PY.exists():
        run([SYSTEM_PYTHON, "-m", "venv", "--system-site-packages", str(VENV_DIR)], check=False)
    run([str(VENV_PY), "-m", "pip", "install", "-r", str(PKG_ROOT / "requirements.txt")], check=False)


def build_binary() -> Path:
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    binary = BIN_DIR / "bluelock"
    run([str(VENV_PY), "-m", "pip", "install", "pyinstaller"], check=True)
    run(
        [
            str(VENV_PY),
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onefile",
            str(PKG_ROOT / "bluetooth_lock.py"),
            "--name",
            "bluelock",
            "--distpath",
            str(BIN_DIR),
            "--workpath",
            str(BUILD_DIR),
            "--specpath",
            str(BUILD_DIR),
        ],
        check=True,
    )
    if not binary.exists():
        raise RuntimeError(f"Binary build failed: {binary} not found")
    return binary


def build_tray_binary() -> Path:
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    binary = BIN_DIR / "bluelock-tray"
    run([str(VENV_PY), "-m", "pip", "install", "pyinstaller"], check=True)
    run(
        [
            str(VENV_PY),
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onefile",
            str(PKG_ROOT / "tray.py"),
            "--name",
            "bluelock-tray",
            "--distpath",
            str(BIN_DIR),
            "--workpath",
            str(BUILD_DIR),
            "--specpath",
            str(BUILD_DIR),
        ],
        check=True,
    )
    if not binary.exists():
        raise RuntimeError(f"Tray binary build failed: {binary} not found")
    return binary


def write_systemd_units(binary_path: Path, tray_binary_path: Path) -> None:
    SYSTEMD_DIR.mkdir(parents=True, exist_ok=True)
    exec_start = f"{binary_path} {CONFIG_PATH}"
    daemon_unit = f"""[Unit]
Description=Bluetooth Lock Daemon

[Service]
ExecStart={exec_start}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""
    tray_unit = f"""[Unit]
Description=Bluetooth Lock Tray
# The tray needs a display; start it with the desktop session, not at user login.
PartOf=graphical-session.target
After=graphical-session.target

[Service]
ExecStart={tray_binary_path}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
"""
    (SYSTEMD_DIR / "bluelock.service").write_text(daemon_unit)
    (SYSTEMD_DIR / "bluelock-tray.service").write_text(tray_unit)


def install() -> None:
    ensure_config()
    install_venv()
    binary = build_binary()
    tray_binary = build_tray_binary()
    write_systemd_units(binary, tray_binary)
    run(["systemctl", "--user", "daemon-reload"], check=False)
    for service in ("bluelock.service", "bluelock-tray.service"):
        # reenable drops links from older installs (e.g. tray under default.target).
        run(["systemctl", "--user", "reenable", service], check=False)
        run(["systemctl", "--user", "restart", service], check=False)


def status() -> None:
    for svc, label in (("bluelock.service", "Daemon"), ("bluelock-tray.service", "Tray")):
        result = run(["systemctl", "--user", "is-active", svc], check=False, capture_output=True, text=True)
        state = result.stdout.strip() if result.stdout else "unknown"
        print(f"{label}: {state}")


def run_daemon() -> None:
    run([str(VENV_PY), str(PKG_ROOT / "bluetooth_lock.py"), str(CONFIG_PATH)])


def run_tray() -> None:
    run([str(VENV_PY), str(PKG_ROOT / "tray.py")])


def main() -> int:
    parser = argparse.ArgumentParser(prog="bluelock", description="Bluetooth lock daemon")
    sub = parser.add_subparsers(dest="cmd")
    install_parser = sub.add_parser("install")
    install_parser.add_argument("--skip-apt", action="store_true")
    sub.add_parser("start")
    sub.add_parser("stop")
    sub.add_parser("restart")
    sub.add_parser("status")
    sub.add_parser("config")
    sub.add_parser("run")
    sub.add_parser("tray")
    args = parser.parse_args()

    if args.cmd == "install":
        install()
        return 0
    if args.cmd == "start":
        systemctl_user("start", "bluelock.service")
        return 0
    if args.cmd == "stop":
        systemctl_user("stop", "bluelock.service")
        return 0
    if args.cmd == "restart":
        systemctl_user("restart", "bluelock.service")
        return 0
    if args.cmd == "status":
        status()
        return 0
    if args.cmd == "config":
        print(CONFIG_PATH)
        return 0
    if args.cmd == "run":
        run_daemon()
        return 0
    if args.cmd == "tray":
        run_tray()
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
