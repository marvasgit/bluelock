@echo off
set VERSION=%1
if "%VERSION%"=="" set VERSION=0.0.0

python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements-windows.txt pyinstaller

pyinstaller --noconfirm --clean --onefile bluetooth_lock.py --name bluelock_daemon
pyinstaller --noconfirm --clean --onefile tray_win.py --name bluelock_tray
pyinstaller --noconfirm --clean --onefile tray_win.py --name bluelock

if not exist dist mkdir dist
powershell -Command "Compress-Archive -Force -Path .\dist\bluelock_daemon.exe,.\dist\bluelock_tray.exe,.\config.example.toml -DestinationPath .\dist\bluelock-%VERSION%-win64.zip"
powershell -Command "Compress-Archive -Force -Path .\dist\bluelock.exe,.\config.example.toml -DestinationPath .\dist\bluelock-%VERSION%-win64-single.zip"
