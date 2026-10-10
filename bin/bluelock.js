#!/usr/bin/env node
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync, spawnSync } = require("child_process");

const CONFIG_DIR = path.join(os.homedir(), ".config", "bluelock");
const CONFIG_PATH = path.join(CONFIG_DIR, "config.toml");
const SHARE_DIR = path.join(os.homedir(), ".local", "share", "bluelock");
const SYSTEMD_DIR = path.join(os.homedir(), ".config", "systemd", "user");
const PKG_ROOT = path.resolve(__dirname, "..");
const VENV_DIR = path.join(SHARE_DIR, "venv");
// The tray needs the distro's python3-gi, which Homebrew/pyenv interpreters can't see.
const SYSTEM_PYTHON = fs.existsSync("/usr/bin/python3") ? "/usr/bin/python3" : "python3";
const VENV_PY = path.join(VENV_DIR, "bin", "python");
const BIN_DIR = path.join(SHARE_DIR, "bin");
const BUILD_DIR = path.join(SHARE_DIR, "build");

function run(cmd, args) {
  const result = spawnSync(cmd, args, { stdio: "inherit" });
  if (result.error) {
    throw result.error;
  }
}

function output(cmd, args) {
  return execFileSync(cmd, args, { encoding: "utf8" }).trim();
}

function ensureConfig() {
  if (fs.existsSync(CONFIG_PATH)) {
    return;
  }
  fs.mkdirSync(CONFIG_DIR, { recursive: true });
  const example = path.join(PKG_ROOT, "config.example.toml");
  if (fs.existsSync(example)) {
    fs.copyFileSync(example, CONFIG_PATH);
  }
}

function installVenv() {
  if (!fs.existsSync(VENV_PY)) {
    run(SYSTEM_PYTHON, ["-m", "venv", "--system-site-packages", VENV_DIR]);
  }
  run(VENV_PY, ["-m", "pip", "install", "-r", path.join(PKG_ROOT, "requirements.txt")]);
}

function buildBinary() {
  fs.mkdirSync(BIN_DIR, { recursive: true });
  fs.mkdirSync(BUILD_DIR, { recursive: true });
  run(VENV_PY, ["-m", "pip", "install", "pyinstaller"]);
  run(VENV_PY, [
    "-m",
    "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    path.join(PKG_ROOT, "bluetooth_lock.py"),
    "--name",
    "bluelock",
    "--distpath",
    BIN_DIR,
    "--workpath",
    BUILD_DIR,
    "--specpath",
    BUILD_DIR,
  ]);
  const binary = path.join(BIN_DIR, "bluelock");
  if (!fs.existsSync(binary)) {
    throw new Error(`Binary build failed: ${binary} not found`);
  }
  return binary;
}

function buildTrayBinary() {
  fs.mkdirSync(BIN_DIR, { recursive: true });
  fs.mkdirSync(BUILD_DIR, { recursive: true });
  run(VENV_PY, ["-m", "pip", "install", "pyinstaller"]);
  run(VENV_PY, [
    "-m",
    "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    path.join(PKG_ROOT, "tray.py"),
    "--name",
    "bluelock-tray",
    "--distpath",
    BIN_DIR,
    "--workpath",
    BUILD_DIR,
    "--specpath",
    BUILD_DIR,
  ]);
  const binary = path.join(BIN_DIR, "bluelock-tray");
  if (!fs.existsSync(binary)) {
    throw new Error(`Tray binary build failed: ${binary} not found`);
  }
  return binary;
}

function writeSystemdUnits() {
  fs.mkdirSync(SYSTEMD_DIR, { recursive: true });
  const binary = buildBinary();
  const trayBinary = buildTrayBinary();
  const execStart = `${binary} ${CONFIG_PATH}`;
  const daemonUnit = `[Unit]
Description=Bluetooth Lock Daemon

[Service]
ExecStart=${execStart}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
`;
  const trayUnit = `[Unit]
Description=Bluetooth Lock Tray
# The tray needs a display; start it with the desktop session, not at user login.
PartOf=graphical-session.target
After=graphical-session.target

[Service]
ExecStart=${trayBinary}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
`;
  fs.writeFileSync(path.join(SYSTEMD_DIR, "bluelock.service"), daemonUnit);
  fs.writeFileSync(path.join(SYSTEMD_DIR, "bluelock-tray.service"), trayUnit);
}

function install() {
  ensureConfig();
  installVenv();
  writeSystemdUnits();
  run("systemctl", ["--user", "daemon-reload"]);
  for (const service of ["bluelock.service", "bluelock-tray.service"]) {
    // reenable drops links from older installs (e.g. tray under default.target).
    run("systemctl", ["--user", "reenable", service]);
    run("systemctl", ["--user", "restart", service]);
  }
}

function status() {
  const daemon = output("systemctl", ["--user", "is-active", "bluelock.service"]);
  const tray = output("systemctl", ["--user", "is-active", "bluelock-tray.service"]);
  console.log(`Daemon: ${daemon}`);
  console.log(`Tray: ${tray}`);
}

function runDaemon() {
  run(VENV_PY, [path.join(PKG_ROOT, "bluetooth_lock.py"), CONFIG_PATH]);
}

function runTray() {
  run(VENV_PY, [path.join(PKG_ROOT, "tray.py")]);
}

const cmd = process.argv[2];
switch (cmd) {
  case "install":
    install();
    break;
  case "start":
    run("systemctl", ["--user", "start", "bluelock.service"]);
    break;
  case "stop":
    run("systemctl", ["--user", "stop", "bluelock.service"]);
    break;
  case "restart":
    run("systemctl", ["--user", "restart", "bluelock.service"]);
    break;
  case "status":
    status();
    break;
  case "config":
    console.log(CONFIG_PATH);
    break;
  case "run":
    runDaemon();
    break;
  case "tray":
    runTray();
    break;
  default:
    console.log(`bluelock <command>
  install       Install system dependencies and services
  start         Start daemon
  stop          Stop daemon
  restart       Restart daemon
  status        Show daemon and tray status
  config        Print config path
  run           Run daemon in foreground
  tray          Run tray UI in foreground`);
    process.exit(1);
}
