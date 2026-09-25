@echo off
REM install.bat - ONE CLICK Windows setup for the Office Hub blueprint.
cd /d "%~dp0"

echo ===============================================
echo   Office Hub - one-click install (Windows)
echo ===============================================

set "PY=python"
where %PY% >nul 2>&1 || (
  echo ERROR: Python required. Install from python.org and check "Add to PATH".
  pause
  exit /b 1
)

%PY% install.py %*
if errorlevel 1 (
  echo Installer failed.
  pause
  exit /b 1
)

echo.
set /p ANS="Start the hub now? [Y/n] "
if /i "%ANS%"=="n" goto skip
call start-hub.bat
timeout /t 3 /nobreak >nul
start http://localhost:8090
:skip
echo.
echo Next: python setup_apps.py    (member portal + forum)
echo       python secure_admin.py  (strong admin credentials)
pause
