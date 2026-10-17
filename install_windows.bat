@echo off
setlocal
chcp 65001 >nul 2>&1
pushd "%~dp0"
set "LOG_DIR=%LOCALAPPDATA%\O-RAN Energy Lab\install"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" 2>nul
if not exist "%LOG_DIR%" set "LOG_DIR=%TEMP%"
set "LOG_FILE=%LOG_DIR%\install-console-%RANDOM%-%RANDOM%.log"
echo Pelny log uruchomienia: "%LOG_FILE%"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_windows.ps1" %* > "%LOG_FILE%" 2>&1
set "INSTALL_RESULT=%ERRORLEVEL%"
type "%LOG_FILE%"
if not "%INSTALL_RESULT%"=="0" (
    echo.
    if "%INSTALL_RESULT%"=="2" (
        echo Wymagany restart Windows. Po restarcie kliknij install_windows.bat ponownie.
    ) else (
        echo Instalator przerwal prace. Szczegoly zapisano w pliku logu wskazanym powyzej.
    )
    pause
)
popd
exit /b %INSTALL_RESULT%
