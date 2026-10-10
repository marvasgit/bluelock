import asyncio
import json
import platform
import re
import statistics
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

BT_ADDR_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")


def normalize_bt_address(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    addr = str(value).strip()
    if not addr:
        return None
    if BT_ADDR_RE.match(addr):
        return addr.upper()
    hex_pairs = re.findall(r"[0-9A-Fa-f]{2}", addr)
    if len(hex_pairs) != 6:
        return None
    normalized = ":".join(part.upper() for part in hex_pairs)
    if not BT_ADDR_RE.match(normalized):
        return None
    return normalized


def bluetooth_connected(address: Optional[str]) -> Optional[bool]:
    addr = normalize_bt_address(address)
    if not addr or platform.system().lower() != "linux":
        return None
    result = subprocess.run(
        ["bluetoothctl", "info", addr],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.strip().lower().startswith("connected:"):
            value = line.split(":", 1)[1].strip().lower()
            if value in ("yes", "no"):
                return value == "yes"
    return None


_HCITOOL_RSSI_RE = re.compile(r"RSSI return value:\s*(-?\d+)")


def parse_hcitool_rssi(output: str) -> Optional[int]:
    match = _HCITOOL_RSSI_RE.search(output)
    return int(match.group(1)) if match else None


def bluetooth_link_rssi(address: Optional[str]) -> Optional[int]:
    """Signal of the live connection to ``address`` (Linux), or None if not connected.

    This is the controller's Read RSSI value: dB relative to its ideal receive
    range, so 0 means a strong signal and it goes negative as the device moves
    away. It works for phones that never show up in BLE scans.
    """
    addr = normalize_bt_address(address)
    if not addr or platform.system().lower() != "linux":
        return None
    try:
        result = subprocess.run(
            ["hcitool", "rssi", addr], check=False, capture_output=True, text=True, timeout=5
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return parse_hcitool_rssi(result.stdout)


def calibrate_link_rssi(address: Optional[str], samples: int = 5, interval_s: float = 0.5) -> Optional[int]:
    """Median link signal over a few readings. Blocking; call off the UI thread."""
    readings = []
    for i in range(samples):
        value = bluetooth_link_rssi(address)
        if value is not None:
            readings.append(value)
        if i < samples - 1:
            time.sleep(interval_s)
    return round(statistics.median(readings)) if readings else None


def bluetooth_connect(address: Optional[str]) -> bool:
    addr = normalize_bt_address(address)
    if not addr or platform.system().lower() != "linux":
        return False
    result = subprocess.run(
        ["bluetoothctl", "connect", addr],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


@dataclass(frozen=True)
class KnownDevice:
    address: Optional[str]  # None on macOS, which hides MAC addresses from apps
    name: Optional[str]
    connected: bool

    @property
    def label(self) -> str:
        text = self.name or self.address or "Unknown device"
        if self.name and self.address:
            text = f"{self.name} ({self.address})"
        return f"{text} (connected)" if self.connected else text


def _bluetoothctl_devices(*args: str) -> Optional[list[tuple[str, Optional[str]]]]:
    try:
        result = subprocess.run(
            ["bluetoothctl", *args], check=False, capture_output=True, text=True, timeout=10
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return parse_bluetoothctl_devices(result.stdout)


def parse_bluetoothctl_devices(output: str) -> list[tuple[str, Optional[str]]]:
    devices = []
    for line in output.splitlines():
        parts = line.strip().split(" ", 2)
        if len(parts) < 2 or parts[0] != "Device":
            continue
        addr = normalize_bt_address(parts[1])
        if not addr:
            continue
        name = parts[2].strip() if len(parts) == 3 else None
        # BlueZ uses the dashed address as the name when the real one is unknown.
        if name and normalize_bt_address(name) == addr:
            name = None
        devices.append((addr, name or None))
    return devices


def _list_linux_devices() -> list[KnownDevice]:
    paired = _bluetoothctl_devices("devices", "Paired")
    if paired is None:  # BlueZ < 5.65
        paired = _bluetoothctl_devices("paired-devices") or []
    connected = _bluetoothctl_devices("devices", "Connected")
    if connected is None:
        connected = [(a, n) for a, n in paired if bluetooth_connected(a)]
    connected_addrs = {a for a, _ in connected}
    names: dict[str, Optional[str]] = {}
    for addr, name in [*paired, *connected]:
        names[addr] = name or names.get(addr)
    return [KnownDevice(addr, name, addr in connected_addrs) for addr, name in names.items()]


def parse_system_profiler(data: dict) -> list[KnownDevice]:
    devices = []
    for controller in data.get("SPBluetoothDataType", []):
        for key, connected in (("device_connected", True), ("device_not_connected", False)):
            for entry in controller.get(key, []) or []:
                for name in entry:
                    devices.append(KnownDevice(None, name, connected))
    return devices


def _list_macos_devices() -> list[KnownDevice]:
    try:
        result = subprocess.run(
            ["system_profiler", "SPBluetoothDataType", "-json"],
            check=False, capture_output=True, text=True, timeout=20,
        )
        return parse_system_profiler(json.loads(result.stdout or "{}"))
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return []


def _list_windows_devices() -> list[KnownDevice]:
    from winrt.windows.devices.bluetooth import (
        BluetoothConnectionStatus,
        BluetoothDevice,
        BluetoothLEDevice,
    )
    from winrt.windows.devices.enumeration import DeviceInformation

    async def collect() -> list[KnownDevice]:
        found = []
        for cls in (BluetoothDevice, BluetoothLEDevice):
            selector = cls.get_device_selector_from_pairing_state(True)
            for info in await DeviceInformation.find_all_async_aqs_filter(selector):
                dev = await cls.from_id_async(info.id)
                if dev is None:
                    continue
                raw = dev.bluetooth_address
                addr = ":".join(f"{(raw >> shift) & 0xFF:02X}" for shift in range(40, -1, -8))
                connected = dev.connection_status == BluetoothConnectionStatus.CONNECTED
                found.append(KnownDevice(addr, dev.name or info.name or None, connected))
        return found

    return asyncio.run(collect())


def list_known_devices() -> list[KnownDevice]:
    """Paired Bluetooth devices, connected ones first. Blocking; call off the UI thread."""
    system = platform.system().lower()
    if system == "linux":
        devices = _list_linux_devices()
    elif system == "darwin":
        devices = _list_macos_devices()
    elif system == "windows":
        devices = _list_windows_devices()
    else:
        devices = []
    merged: dict[str, KnownDevice] = {}
    for dev in devices:
        key = dev.address or dev.name or ""
        if key not in merged or (dev.connected and not merged[key].connected):
            merged[key] = dev
    return sorted(merged.values(), key=lambda d: (not d.connected, (d.name or d.address or "").lower()))


def bind_device(path: Path, device: KnownDevice) -> None:
    """Make ``device`` the monitored device: overwrite both address and name.

    Empty strings disable a field, so a stale value from the previous device
    can't keep matching.
    """
    set_config_value(path, "device", "address", device.address or "")
    set_config_value(path, "device", "name", device.name or "")


def is_bound(device: KnownDevice, address: Optional[str], name: Optional[str]) -> bool:
    if device.address and address:
        return normalize_bt_address(device.address) == normalize_bt_address(address)
    return bool(device.name and name and device.name == name)


def estimate_distance_m(rssi: int, tx_power: int, n: float) -> float:
    return 10 ** ((tx_power - rssi) / (10 * n))


def matches_device(
    address: Optional[str],
    name: Optional[str],
    target_addr: Optional[str],
    target_name: Optional[str],
) -> bool:
    if target_addr and address:
        # Compare normalized MACs so "aa-bb-.." in the config matches "AA:BB:.." from BlueZ;
        # fall back to a plain comparison for non-MAC identifiers (macOS UUIDs).
        norm_target = normalize_bt_address(target_addr)
        norm_addr = normalize_bt_address(address)
        if norm_target and norm_addr:
            if norm_target == norm_addr:
                return True
        elif address.lower() == target_addr.lower():
            return True
    if target_name and name and target_name.lower() in name.lower():
        return True
    return False


STARTER_CONFIG = """\
# BlueLock configuration. Pick your device in the tray menu (Bind Device), or set
# device.address / device.name here. Every setting is explained in
# config.example.toml (/usr/share/doc/bluelock/ on Ubuntu).

[device]

[lock]
distance_m = 3.0
debug = false

[bluetooth]
tx_power = -59
path_loss_exponent = 2.2
"""


def write_starter_config(path: Path) -> bool:
    """Create ``path`` with a device-less starter config. Returns False if it exists."""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(STARTER_CONFIG)
    return True


def default_config_path() -> Path:
    system = platform.system().lower()
    if system == "windows":
        import os
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "bluelock" / "config.toml"
    return Path("~/.config/bluelock/config.toml").expanduser()


_TOML_HEADER_RE = re.compile(r"^\s*\[\s*([^\[\]]+?)\s*\]\s*(#.*)?$")


def _toml_literal(value: bool | int | float | str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        # JSON string escapes (\" \\ \n \uXXXX ...) are valid TOML basic-string escapes.
        return json.dumps(value)
    return repr(value)


def set_config_value(path: Path, section: str, key: str, value: bool | int | float | str) -> None:
    """Set ``[section].key`` in a TOML file, leaving comments and layout untouched.

    Rewriting via a TOML serializer would drop every comment in the user's config.
    """
    lines: list[str] = []
    if path.exists():
        # newline="" keeps CRLF files as CRLF instead of normalizing them.
        with path.open(newline="") as f:
            lines = f.read().splitlines(keepends=True)
    new_value = _toml_literal(value)
    key_re = re.compile(rf"^(\s*{re.escape(key)}\s*=\s*)[^#\r\n]*?(\s*#[^\r\n]*)?(\r?\n)?$")
    in_section = False
    section_found = False
    replaced = False
    insert_at = len(lines)
    for i, line in enumerate(lines):
        header = _TOML_HEADER_RE.match(line.rstrip("\r\n"))
        if header:
            if in_section:
                break
            in_section = header.group(1) == section
            if in_section:
                section_found = True
                insert_at = i + 1
            continue
        if not in_section:
            continue
        match = key_re.match(line)
        if match:
            lines[i] = f"{match.group(1)}{new_value}{match.group(2) or ''}{match.group(3) or ''}"
            replaced = True
            break
        if line.strip() and not line.lstrip().startswith("#"):
            insert_at = i + 1
    if not replaced:
        new_line = f"{key} = {new_value}\n"
        if section_found:
            lines.insert(insert_at, new_line)
        else:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines += (["\n"] if lines else []) + [f"[{section}]\n", new_line]
    text = "".join(lines)
    if tomllib.loads(text).get(section, {}).get(key) != value:
        raise ValueError(f"Could not set {section}.{key} in {path}")
    with path.open("w", newline="") as f:
        f.write(text)


@dataclass
class DeviceConfig:
    address: Optional[str]
    name: Optional[str]


@dataclass
class LockConfig:
    distance_m: float
    hysteresis_m: float
    check_interval_s: float
    missing_timeout_s: float
    samples: int
    breach_samples: int
    lock_cooldown_s: float
    command: Optional[str]
    rssi_fallback: Optional[int]
    debug: bool
    # Lock when the connected device's link signal (dB, relative) stays at or
    # below this. Used only when no BLE distance is available. None disables it.
    link_rssi_threshold: Optional[int] = -10


@dataclass
class BluetoothConfig:
    adapter: Optional[str]
    tx_power: int
    path_loss_exponent: float


@dataclass
class AppConfig:
    device: DeviceConfig
    lock: LockConfig
    bluetooth: BluetoothConfig


@dataclass
class MonitorStatus:
    """Live readings the monitor publishes for an in-process UI (e.g. the macOS app).

    Written by the monitor's event loop, read by the UI thread; fields are
    replaced atomically, so no locking is needed.
    """

    last_seen: Optional[float] = None  # wall-clock time.time()
    rssi: Optional[int] = None
    distance_m: Optional[float] = None  # median of recent samples
    name: Optional[str] = None
    address: Optional[str] = None
    link_rssi: Optional[int] = None


# The link signal must climb this far above the threshold to re-arm after a lock.
LINK_REARM_MARGIN_DB = 3


class LockPolicy:
    """Decides when the daemon should lock.

    Locks at most once per "away" episode: after a lock the policy is disarmed
    until the device is seen again within ``distance_m`` (the hysteresis band
    between ``distance_m`` and ``distance_m + hysteresis_m`` neither locks nor
    re-arms). ``lock_cooldown_s`` still applies as a minimum gap between locks.
    """

    def __init__(self, lock: LockConfig) -> None:
        self.cfg = lock
        self.breach_count = 0
        self.armed = True
        self.last_lock_time: Optional[float] = None
        self.locked_while_missing = False

    def _cooldown_ok(self, now: float) -> bool:
        return self.last_lock_time is None or now - self.last_lock_time >= self.cfg.lock_cooldown_s

    def _fire(self, now: float, reason: str) -> str:
        self.armed = False
        self.breach_count = 0
        self.last_lock_time = now
        return reason

    def is_missing(self, now: float, last_seen: Optional[float]) -> bool:
        return last_seen is not None and now - last_seen > self.cfg.missing_timeout_s

    def evaluate(
        self,
        now: float,
        last_seen: Optional[float],
        median_distance: Optional[float],
        link_rssi: Optional[int] = None,
    ) -> Optional[str]:
        """Return a lock reason, or None.

        ``last_seen`` is None until the device has been seen once.
        ``median_distance`` is None when no fresh advertisement arrived this tick.
        ``link_rssi`` is the connection's signal; it decides only when there is
        no BLE distance (e.g. iPhones, which hide their BLE identity).
        """
        if self.is_missing(now, last_seen):
            self.breach_count = 0
            if self.armed and self._cooldown_ok(now):
                self.locked_while_missing = True
                return self._fire(now, "Device missing (no advertisements within timeout).")
            return None
        if self.locked_while_missing:
            self.locked_while_missing = False
            # Back after a missing-lock. With no distance or signal reading to say
            # it's still far, being present again is the only "came back" signal.
            if median_distance is None and link_rssi is None:
                self.armed = True
        if median_distance is None:
            return self._evaluate_link(now, link_rssi)
        if median_distance > self.cfg.distance_m + self.cfg.hysteresis_m:
            self.breach_count += 1
            if self.breach_count >= self.cfg.breach_samples and self.armed and self._cooldown_ok(now):
                return self._fire(now, "Distance exceeded threshold.")
            return None
        self.breach_count = 0
        if median_distance <= self.cfg.distance_m:
            self.armed = True
        return None

    def _evaluate_link(self, now: float, link_rssi: Optional[int]) -> Optional[str]:
        threshold = self.cfg.link_rssi_threshold
        if link_rssi is None or threshold is None:
            return None
        if link_rssi <= threshold:
            self.breach_count += 1
            if self.breach_count >= self.cfg.breach_samples and self.armed and self._cooldown_ok(now):
                return self._fire(now, "Connection signal too weak (device moved away).")
            return None
        self.breach_count = 0
        if link_rssi >= threshold + LINK_REARM_MARGIN_DB:
            self.armed = True
        return None


def load_config(path: Path) -> AppConfig:
    data = tomllib.loads(path.read_text())

    device = data.get("device", {})
    lock = data.get("lock", {})
    bluetooth = data.get("bluetooth", {})

    return AppConfig(
        device=DeviceConfig(
            address=device.get("address"),
            name=device.get("name"),
        ),
        lock=LockConfig(
            distance_m=float(lock.get("distance_m", 3.0)),
            hysteresis_m=float(lock.get("hysteresis_m", 0.8)),
            check_interval_s=float(lock.get("check_interval_s", 2.0)),
            missing_timeout_s=float(lock.get("missing_timeout_s", 8.0)),
            samples=int(lock.get("samples", 5)),
            breach_samples=int(lock.get("breach_samples", 3)),
            lock_cooldown_s=float(lock.get("lock_cooldown_s", 20.0)),
            command=lock.get("command"),
            rssi_fallback=lock.get("rssi_fallback"),
            debug=bool(lock.get("debug", False)),
            # `link_rssi_threshold = false` turns link-signal locking off.
            link_rssi_threshold=(
                None if (raw_link := lock.get("link_rssi_threshold", -10)) is False else int(raw_link)
            ),
        ),
        bluetooth=BluetoothConfig(
            adapter=bluetooth.get("adapter"),
            tx_power=int(bluetooth.get("tx_power", -59)),
            path_loss_exponent=float(bluetooth.get("path_loss_exponent", 2.2)),
        ),
    )


async def calibrate_distance(
    target_addr: Optional[str],
    target_name: Optional[str],
    adapter: Optional[str],
    tx_power: int,
    path_loss_exponent: float,
    samples: int = 5,
    timeout: float = 3.0,
) -> Optional[float]:
    from bleak import BleakScanner

    distances: list[float] = []
    for _ in range(samples):
        devices = await BleakScanner.discover(timeout=timeout, adapter=adapter, return_adv=True)
        for dev, adv in devices.values():
            if matches_device(dev.address, dev.name, target_addr, target_name):
                if adv.rssi is not None:
                    distances.append(estimate_distance_m(adv.rssi, tx_power, path_loss_exponent))
                break
        await asyncio.sleep(0.2)

    if not distances:
        return None
    return statistics.median(distances)
