@echo off
setlocal
pushd "%~dp0"
set "PSModulePath="
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0run_windows.ps1" %*
set "RUN_RESULT=%ERRORLEVEL%"
if not "%RUN_RESULT%"=="0" (
    echo.
    pause
)
popd
exit /b %RUN_RESULT%
