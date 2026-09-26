@echo off
REM start-hub.bat - ONE SCRIPT brings up the entire blueprint, in the right order:
REM   0. dependency gate (Python, venv, packages, AI stack for this privacy mode)
REM   1. MariaDB         (databases for the portal + forum)
REM   2. Admidio :8080 + Flarum :8081   (PHP; skipped with --hub-only)
REM   3. llama.cpp       (only when privacy_mode = local)
REM   4. AI glue API :8090
REM Safe to run twice (pidfiles + port checks). --hub-only skips MariaDB/apps.

setlocal enabledelayedexpansion
cd /d "%~dp0"
if not exist data mkdir data

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
set "HUB_ONLY=0"
if "%~1"=="--hub-only" set "HUB_ONLY=1"

set "MODE="
for /f "usebackq delims=" %%i in (`%PY% -c "import json;print(json.load(open('config.json'))['privacy_mode'])" 2^>nul`) do set "MODE=%%i"
if "%MODE%"=="" set "MODE=retrieval_only"

REM --- 0. dependency gate --------------------------------------------------------
%PY% check_deps.py --section hub --quiet
if errorlevel 1 (
  echo [hub] dependency check failed - fix the items above ^(or run: python doctor.py^).
  pause
  exit /b 1
)

REM --- 0b. safety net: incremental backup at every boot (skips if <6h old) -------
start "" /b "%PY%" hub\backup.py --auto > data\backup-boot.log 2>&1

REM --- 1. MariaDB (best effort) ----------------------------------------------------
if %HUB_ONLY%==0 (
  %PY% portcheck.py 3306 >nul 2>&1
  if errorlevel 1 (
    sc query MariaDB >nul 2>&1 && (
      echo [hub] starting MariaDB service ...
      net start MariaDB >nul 2>&1 || echo [hub] WARNING: could not start MariaDB - start it from Services.msc for the portal/forum.
    ) || (
      echo [hub] WARNING: MariaDB not detected on :3306 - start it manually for the portal/forum.
    )
  ) else (
    echo [hub] MariaDB is up ^(:3306^)
  )
)

REM --- 2. Admidio + Flarum (best effort) ---------------------------------------------
if %HUB_ONLY%==0 (
  if exist "apps\admidio\index.php" (
    %PY% portcheck.py 8080 >nul 2>&1
    if errorlevel 1 (
      set PHP_CLI_SERVER_WORKERS=8
      start "Admidio :8080" /min cmd /c "php -S 0.0.0.0:8080 -t apps\admidio > data\admidio.log 2>&1"
      echo [apps] Admidio on http://localhost:8080
    ) else (
      echo [apps] Admidio already running ^(:8080^)
    )
  ) else (
    echo [apps] Admidio skipped ^(needs apps\admidio + php^) - run: python setup_apps.py
  )
  if exist "apps\flarum\public\index.php" (
    %PY% portcheck.py 8081 >nul 2>&1
    if errorlevel 1 (
      set PHP_CLI_SERVER_WORKERS=8
      start "Flarum :8081" /min cmd /c "php -S 0.0.0.0:8081 -t apps\flarum\public > data\flarum.log 2>&1"
      echo [apps] Flarum on http://localhost:8081
    ) else (
      echo [apps] Flarum already running ^(:8081^)
    )
  ) else (
    echo [apps] Flarum skipped ^(needs apps\flarum + php^) - run: python setup_apps.py
  )
)

REM --- 3. local AI engine (optional) --------------------------------------------------
if /i "%MODE%"=="local" if exist "ai\llama.cpp\llama-server.exe" (
  if exist "data\llama.pid" (
    for /f %%p in (data\llama.pid) do tasklist /fi "pid eq %%p" 2>nul | find "%%p" >nul && (
      echo [hub] local AI already running ^(pid %%p^) & goto ai_done
    )
  )
  for /f "usebackq tokens=1-4" %%a in (`%PY% -c "import json;c=json.load(open('config.json'))['local_ai'];print(c['ctx'],c['threads'],c['port'],c['host'])"`) do (
    set "CTX=%%a" & set "THREADS=%%b" & set "MPORT=%%c" & set "MHOST=%%d"
  )
  for /f "usebackq delims=" %%m in (`%PY% -c "import json;print(json.load(open('config.json'))['local_ai']['model_path'])"`) do set "MODEL=%%m"
  start "" /b "ai\llama.cpp\llama-server.exe" -m "%MODEL%" --host %MHOST% --port %MPORT% --ctx-size %CTX% --threads %THREADS% > data\llama.log 2>&1
  echo [hub] local AI starting on :%MPORT% ^(log: data\llama.log^)
)
:ai_done

REM --- 4. Power Mode agent (optional, chat apps + automations) -------------------------
set "POWER=False"
for /f "usebackq delims=" %%i in (`%PY% -c "import json;print(json.load(open('config.json')).get('power_mode', False))" 2^>nul`) do set "POWER=%%i"
if /i "%POWER%"=="True" if exist ".venv-agent\Scripts\nanobot.exe" (
  if exist "data\agent.pid" (
    for /f %%p in (data\agent.pid) do tasklist /fi "pid eq %%p" 2>nul | find "%%p" >nul && (
      echo [agent] already running ^(pid %%p^) & goto agent_done
    )
  )
  start "Agent :18790" /min cmd /c ".venv-agent\Scripts\nanobot.exe gateway --foreground -c data\nanobot\config.json -w data\nanobot\workspace > data\agent.log 2>&1"
  echo [agent] Power Mode ON ^(log: data\agent.log^) - add a Telegram/Discord token in data\nanobot\config.json to go live on phones
)
:agent_done

REM --- 5. glue API ----------------------------------------------------------------------
if exist "data\hub.pid" (
  for /f %%p in (data\hub.pid) do tasklist /fi "pid eq %%p" 2>nul | find "%%p" >nul && (
    echo [hub] AI API already running ^(pid %%p^) & goto chain_start
  )
)
start "" /b "%PY%" -m uvicorn hub.server:app --host 0.0.0.0 --port 8090 > data\hub.log 2>&1
echo [hub] AI API starting on http://localhost:8090 ^(log: data\hub.log^)
:chain_start
if exist "data\chain.pid" (
  for /f %%p in (data\chain.pid) do tasklist /fi "pid eq %%p" 2>nul | find "%%p" >nul && (
    echo [chain] LLM fallback chain already running ^(pid %%p^) & goto done
  )
)
start "" /b "%PY%" hub\llm_proxy.py > data\chain.log 2>&1
echo [chain] LLM fallback chain starting on :8085
:done

REM --- 6. Officer AI gateway (only when switched on in the admin console) -------
set "OFFICER="
if exist "data\officer_ai.json" for /f "usebackq delims=" %%i in (`%PY% -c "import json;print(json.load(open('data/officer_ai.json')).get('enabled', False))" 2^>nul`) do set "OFFICER=%%i"
if /i "%OFFICER%"=="True" (
  %PY% portcheck.py 8766 >nul 2>&1
  if errorlevel 1 (
    start "" /b "%PY%" hub\admin_ai.py > data\officer-ai.log 2>&1
    echo [officer-ai] Officer AI gateway on http://localhost:8766 ^(key-protected, logged^)
  ) else (
    echo [officer-ai] already running ^(:8766^)
  )
)
echo [hub] up.
if %HUB_ONLY%==1 exit /b 0
echo   AI assistant : http://localhost:8090
echo   Member portal: http://localhost:8080   ^(Admidio^)
echo   Forum / chat : http://localhost:8081   ^(Flarum^)
