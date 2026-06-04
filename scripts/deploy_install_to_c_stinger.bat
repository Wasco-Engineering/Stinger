@echo off
REM Install Stinger to C:\Stinger (bypasses PowerShell execution policy for this run).
REM Run from an elevated Command Prompt or PowerShell:
REM   cd /d C:\Stinger
REM   scripts\deploy_install_to_c_stinger.bat STINGER_01

setlocal
set "ROOT=%~dp0.."
cd /d "%ROOT%"

set "STAND_ID=%~1"
if "%STAND_ID%"=="" set "STAND_ID=STINGER_01"

REM Run this window as Administrator for machine env + CalibrationUser shortcuts.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\deploy_install_to_c_stinger.ps1" ^
  -Build -InstallPyInstaller -SkipTests -StandId %STAND_ID% -SetMachineEnv -DesktopShortcuts -TargetUser CalibrationUser

exit /b %ERRORLEVEL%
