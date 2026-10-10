param(
  [string]$Version = "0.0.0"
)

$ErrorActionPreference = "Stop"

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-windows.txt pyinstaller

.\.venv\Scripts\pyinstaller.exe --noconfirm --clean --onefile bluetooth_lock.py --name bluelock_daemon
.\.venv\Scripts\pyinstaller.exe --noconfirm --clean --onefile tray_win.py --name bluelock_tray
.\.venv\Scripts\pyinstaller.exe --noconfirm --clean --onefile tray_win.py --name bluelock

if (!(Test-Path dist)) { New-Item -ItemType Directory -Path dist | Out-Null }

Compress-Archive -Force -Path .\dist\bluelock_daemon.exe,.\dist\bluelock_tray.exe,.\config.example.toml -DestinationPath ".\dist\bluelock-$Version-win64.zip"
Compress-Archive -Force -Path .\dist\bluelock.exe,.\config.example.toml -DestinationPath ".\dist\bluelock-$Version-win64-single.zip"
