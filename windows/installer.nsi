; NSIS installer for BlueLock (Windows 10/11)
!include "MUI2.nsh"

Name "BlueLock"
OutFile "bluelock-setup.exe"
InstallDir "$PROGRAMFILES\\BlueLock"
RequestExecutionLevel user

!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "English"

Section "Install"
  SetOutPath "$INSTDIR"
  File "..\\dist\\bluelock_tray.exe"
  File "..\\dist\\bluelock_daemon.exe"
  File "..\\config.example.toml"

  CreateShortCut "$SMPROGRAMS\\BlueLock.lnk" "$INSTDIR\\bluelock_tray.exe"
  CreateShortCut "$DESKTOP\\BlueLock.lnk" "$INSTDIR\\bluelock_tray.exe"

  ; Run at user logon
  WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Run" "BlueLock" "$INSTDIR\\bluelock_tray.exe"
SectionEnd
