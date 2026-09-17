@echo off
rem ==============================
rem Manuphet Web launcher
rem   Python is resolved in this order:
rem     1. start_manuphet.local.bat (machine specific, not tracked by git)
rem     2. MANUPHET_PYTHON_EXE environment variable
rem     3. .venv\Scripts\python.exe
rem     4. python on PATH
rem ==============================
setlocal

if exist "%~dp0start_manuphet.local.bat" (
    call "%~dp0start_manuphet.local.bat" %*
    exit /b %errorlevel%
)

pushd "%~dp0" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Cannot open script directory: "%~dp0"
    exit /b 1
)

if defined MANUPHET_PYTHON_EXE (
    set "PYTHON=%MANUPHET_PYTHON_EXE%"
) else if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    set "PYTHON=python"
)

echo [Manuphet] Starting Web service with %PYTHON% ...
"%PYTHON%" run_web.py
set "EXITCODE=%errorlevel%"
popd >nul 2>&1
exit /b %EXITCODE%
