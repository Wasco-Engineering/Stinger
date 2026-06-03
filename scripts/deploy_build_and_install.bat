@echo off
REM Build and install to C:\Stinger (bypasses PowerShell execution policy).
setlocal
set "ROOT=%~dp0.."
cd /d "%ROOT%"

set "STAND_ID=%~1"
if "%STAND_ID%"=="" set "STAND_ID=STINGER_01"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\deploy_build_and_install.ps1" ^
  -StandId %STAND_ID% -SetMachineEnv -InstallPyInstaller -DesktopShortcuts

exit /b %ERRORLEVEL%
