.PHONY: all build deb deb-version install-deb clean-deb build-linux build-windows build-macos

all: build

build:
	@true

DEB_VERSION ?= 1.0.0-1
VERSION ?= 1.0.0
# The tray needs the distro's python3-gi, so prefer the system interpreter over
# Homebrew/pyenv shims that may come first on PATH.
SYSTEM_PYTHON ?= $(shell test -x /usr/bin/python3 && echo /usr/bin/python3 || echo python3)

deb:
	dpkg-buildpackage -us -uc

deb-version:
	./scripts/build_deb.sh $(VERSION)

install-deb:
	sudo apt install $$(ls -1t ../bluelock_*_all.deb 2>/dev/null | head -1)

clean-deb:
	dh_clean

build-linux:
	$(SYSTEM_PYTHON) -m venv --system-site-packages .venv
	. .venv/bin/activate && pip install -r requirements.txt pyinstaller
	. .venv/bin/activate && pyinstaller --noconfirm --clean --onefile bluetooth_lock.py --name bluelock
	. .venv/bin/activate && pyinstaller --noconfirm --clean --onefile tray.py --name bluelock-tray

build-windows:
	@if command -v pwsh >/dev/null 2>&1; then \
		pwsh -File scripts/build_windows.ps1 -Version $(VERSION); \
	elif command -v powershell >/dev/null 2>&1; then \
		powershell -File scripts/build_windows.ps1 -Version $(VERSION); \
	else \
		echo "PowerShell not found. Run scripts/build_windows.bat from Windows." >&2; \
		exit 1; \
	fi

build-macos:
	./scripts/build_macos.sh $(VERSION)
