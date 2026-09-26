@echo off
REM stop-hub.bat - stops glue API, local AI, Admidio, Flarum.
REM MariaDB is left running (system service); stop via Services.msc if needed.
cd /d "%~dp0"
for %%f in (hub chain llama agent officer-ai admidio flarum) do (
  if exist data\%%f.pid (
    for /f %%p in (data\%%f.pid) do taskkill /pid %%p /f >nul 2>&1
    del data\%%f.pid
  )
)
echo [hub] stopped.
