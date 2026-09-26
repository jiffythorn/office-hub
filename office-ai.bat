@echo off
REM office-ai.bat - the officer's door into the protected Officer AI.
REM
REM   office-ai.bat                     interactive menu
REM   office-ai.bat check               run one maintenance action directly
REM   office-ai.bat "is anything broken?"    ask in plain English; the hub AI
REM                                          picks the action (answers stay local)
REM
REM Everything runs through the Officer AI gateway: allowlist only, key-checked,
REM every action written to the audit trail.
setlocal enabledelayedexpansion
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
set "CONF=data\officer_ai.json"
set "BASE=http://127.0.0.1:8766"

if not exist "%CONF%" (
  echo Officer AI is not configured yet.
  echo Open http://localhost:8090/admin -^> 'Officer AI ^(advanced^)' and switch it on.
  exit /b 1
)
for /f "usebackq delims=" %%t in (`%PY% -c "import json;print(json.load(open('data/officer_ai.json')).get('token',''))"`) do set "TOKEN=%%t"

if "%~1"=="" goto menu
if "%~1"=="history" (
  %PY% -c "import sys;sys.path.insert(0,'.');from hub import audit;[print(e['time'],e['actor'],e['action'],e['outcome']) for e in audit.recent(40)]"
  exit /b 0
)
%PY% -c "import sys;sys.path.insert(0,'.');from hub.admin_ai import ALLOWED;sys.exit(0 if sys.argv[1] in ALLOWED else 1)" "%~1" >nul 2>&1
if not errorlevel 1 (
  call :run_one "%~1"
  exit /b 0
)
call :pick_action %*
if defined ACTION (
  echo -^> action: !ACTION!
  call :run_one "!ACTION!"
) else (
  echo The AI could not map that to a maintenance action. Try: office-ai.bat ^(menu^)
)
exit /b 0

:menu
echo.
echo Office Hub - Officer AI ^(every action is logged^)
echo ------------------------------------------------
%PY% -c "import sys;sys.path.insert(0,'.');from hub.admin_ai import ALLOWED;[print('  ',k,'-',v['desc']) for k,v in sorted(ALLOWED.items())]"
echo    q. quit
set /p CHOICE="Pick a number (or type a question): "
if "!CHOICE!"=="" exit /b 0
if /i "!CHOICE!"=="q" exit /b 0
echo !CHOICE!| findstr /r "^[0-9][0-9]*$" >nul
if errorlevel 1 (
  call :pick_action !CHOICE!
  if defined ACTION call :run_one "!ACTION!" || echo Could not map that to an action.
) else (
  for /f %%k in ('%PY% -c "import sys;sys.path.insert(0,'.');ks=sorted(__import__('hub.admin_ai',fromlist=['ALLOWED']).ALLOWED);i=int(sys.argv[1])-1;print(ks[i] if 0<=i<len(ks) else '')" !CHOICE!') do set "ACTION=%%k"
  if defined ACTION call :run_one "!ACTION!"
)
goto menu

:pick_action
REM %* = free text -> hub AI suggests an allowlisted action
set "ACTION="
set "PROMPT=%*"
for /f "usebackq delims=" %%a in (`%PY% -c "import sys,json,re,urllib.request;sys.path.insert(0,'.');from hub.admin_ai import ALLOWED,SYSTEM_PROMPT;q=' '.join(sys.argv[1:]);body=json.dumps({'messages':[{'role':'system','content':SYSTEM_PROMPT},{'role':'user','content':q}],'max_tokens':60}).encode();r=urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8085/v1/chat/completions',data=body,headers={'Content-Type':'application/json'}),timeout=180);text=json.loads(r.read())['choices'][0]['message']['content'];t2=text.replace(chr(34),'');m=re.search(r'\{[^{}]*\brun\b[^{}:]*:\s*([\w]+)',t2);print(m.group(1) if m and m.group(1) in ALLOWED else '')" "%PROMPT%"`) do set "ACTION=%%a"
exit /b 0

:run_one
for /f "usebackq delims=" %%j in (`%PY% -c "import sys,json,urllib.request;key,token=sys.argv[1],sys.argv[2];req=urllib.request.Request('http://127.0.0.1:8766/run',data=json.dumps({'run':key}).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+token});r=urllib.request.urlopen(req,timeout=660);out=json.loads(r.read());print(json.dumps(out))" "%~1" "%TOKEN%"`) do set "JSONOUT=%%j"
%PY% -c "import sys,json;out=json.loads(sys.argv[1]);print('===',out.get('command'),'===');print('REFUSED:',out['error']) if out.get('error') else (print(out.get('output','(no output)')),print('--- exit',out.get('exit'),out.get('seconds'),'s ---'))" "%JSONOUT%"
exit /b 0
