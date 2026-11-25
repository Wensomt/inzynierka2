@echo off
setlocal
chcp 65001 >nul 2>&1
set "PSModulePath="
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0install_windows.ps1" %*
exit /b %ERRORLEVEL%
