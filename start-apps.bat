@echo off
REM start-apps.bat - run Admidio (:8080) and Flarum (:8081) on PHP built-in server.
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

REM --- 0. dependency check (PHP + extensions, MariaDB, app folders) -------------
%PY% check_deps.py --section apps --quiet
if errorlevel 1 (
  echo [apps] dependency check failed - fix the items above, then run again.
  pause
  exit /b 1
)
set PHP_CLI_SERVER_WORKERS=8

if exist apps\admidio (
  start "Admidio :8080" /min php -S 0.0.0.0:8080 -t apps\admidio
  echo [apps] Admidio on http://localhost:8080
) else (
  echo [apps] apps\admidio missing - run: python setup_apps.py
)

if exist apps\flarum (
  start "Flarum :8081" /min php -S 0.0.0.0:8081 -t apps\flarum\public
  echo [apps] Flarum on http://localhost:8081
) else (
  echo [apps] apps\flarum missing - run: python setup_apps.py
)
