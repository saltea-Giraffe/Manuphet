@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem ==============================
rem Manuphet Web stopper
rem Usage:
rem   stop_manuphet.bat
rem   stop_manuphet.bat 8001
rem ==============================

set "APP_NAME=Manuphet Web"
set "PORT=8000"
if not "%~1"=="" set "PORT=%~1"

echo [%DATE% %TIME%] Stopping %APP_NAME% on port %PORT%...

set "FOUND=0"
set "PIDS= "

for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
    set "FOUND=1"
    if "!PIDS: %%P =!"=="!PIDS!" (
        set "PIDS=!PIDS!%%P "
        echo [INFO] Stopping PID %%P ...
        taskkill /PID %%P /F >nul 2>&1
        if errorlevel 1 (
            echo [WARN] Failed to stop PID %%P.
        ) else (
            echo [OK] Stopped PID %%P.
        )
    )
)

if "%FOUND%"=="0" (
    echo [INFO] %APP_NAME% is not running on port %PORT%.
    exit /b 0
)

ping -n 2 127.0.0.1 >nul
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
    echo [WARN] Port %PORT% is still in use.
    exit /b 1
)

echo [OK] %APP_NAME% stopped on port %PORT%.
exit /b 0
