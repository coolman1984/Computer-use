@echo off
setlocal
title SmartOps Launcher

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Start-SmartOps.ps1"
set "SMARTOPS_START_EXIT=%ERRORLEVEL%"

if not "%SMARTOPS_START_EXIT%"=="0" (
    echo.
    echo SmartOps could not start. The error and log location are shown above.
    echo.
    pause
)

exit /b %SMARTOPS_START_EXIT%
