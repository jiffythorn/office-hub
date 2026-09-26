#!/usr/bin/env bash
# office-ai.sh - the officer's door into the protected Officer AI.
#
#   ./office-ai.sh                    interactive menu
#   ./office-ai.sh check              run one maintenance action directly
#   ./office-ai.sh "is anything broken?"   ask in plain English; the hub AI
#                                          picks the action (answers stay local)
#
# Everything runs through the Officer AI gateway: allowlist only, key-checked,
# every action written to the audit trail.
cd "$(dirname "$0")"
PY=".venv/bin/python"; [ -x "$PY" ] || PY=python3
CONF=data/officer_ai.json
BASE=http://127.0.0.1:8766

if [ ! -f "$CONF" ]; then
  echo "Officer AI is not configured yet."
  echo "Open http://localhost:8090/admin -> 'Officer AI (advanced)' and switch it on."
  exit 1
fi
TOKEN=$("$PY" -c "import json;print(json.load(open('$CONF')).get('token',''))")

menu() {
  echo
  echo "Office Hub - Officer AI (every action is logged)"
  echo "------------------------------------------------"
  "$PY" - <<'EOF'
import sys; sys.path.insert(0, '.')
from hub.admin_ai import ALLOWED
for i, (k, v) in enumerate(sorted(ALLOWED.items()), 1):
    print(f"  {i:2}. {k:14} {v['desc']}")
EOF
  echo "   q. quit"
}

pick_action() {  # $1 = free text -> hub AI suggests an allowlisted action
  "$PY" - "$1" <<'EOF'
import sys, json, re, urllib.request
sys.path.insert(0, '.')
from hub.admin_ai import ALLOWED, SYSTEM_PROMPT
q = sys.argv[1]
body = json.dumps({"messages": [
    {"role": "system", "content": SYSTEM_PROMPT},
    {"role": "user", "content": q}],
    "max_tokens": 60}).encode()
try:
    r = urllib.request.urlopen(urllib.request.Request(
        "http://127.0.0.1:8085/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json"}), timeout=180)
    text = json.loads(r.read())["choices"][0]["message"]["content"]
except Exception as e:
    print(f"::error::hub AI unreachable ({e})"); sys.exit(1)
m = re.search(r'\{[^}]*"run"\s*:\s*"([\w]+)"[^}]*\}', text)
if m and m.group(1) in ALLOWED:
    print(m.group(1))
else:
    print("")
EOF
}

run_one() {  # $1 = allowlisted key
  "$PY" - "$1" "$TOKEN" <<'EOF'
import sys, json, urllib.request
key, token = sys.argv[1], sys.argv[2]
req = urllib.request.Request(
    "http://127.0.0.1:8766/run",
    data=json.dumps({"run": key}).encode(),
    headers={"Content-Type": "application/json",
             "Authorization": f"Bearer {token}"})
try:
    r = urllib.request.urlopen(req, timeout=660)
    out = json.loads(r.read())
except urllib.error.HTTPError as e:
    out = {"ok": False, "error": json.loads(e.read()).get("error", e.reason)}
except Exception as e:
    out = {"ok": False, "error":
           f"cannot reach the Officer AI gateway on :8766 - restart the hub "
           f"(./start-hub.sh) with the Officer AI enabled in the admin console. ({e})"}
print(("=== " + key + " ==="))
if out.get("error"):
    print("REFUSED:", out["error"])
else:
    print(out.get("output", "(no output)"))
    print(f"--- exit {out.get('exit')}, {out.get('seconds')}s ---")
EOF
}

# ---- argument handling ----
if [ $# -gt 0 ]; then
  case "$1" in
    -h|--help) sed -n '2,10p' "$0"; exit 0;;
    history) "$PY" -c "import sys;sys.path.insert(0,'.');from hub import audit;[print(e['time'],e['actor'],e['action'],e['outcome']) for e in audit.recent(40)]"; exit 0;;
    *)
      if "$PY" -c "import sys;sys.path.insert(0,'.');from hub.admin_ai import ALLOWED;sys.exit(0 if sys.argv[1] in ALLOWED else 1)" "$1" 2>/dev/null; then
        run_one "$1"; exit 0
      fi
      ACTION=$(pick_action "$*")
      if [ -z "$ACTION" ]; then echo "The AI could not map that to a maintenance action. Try: ./office-ai.sh (menu)"; exit 1; fi
      echo "-> action: $ACTION"; run_one "$ACTION"; exit 0;;
  esac
fi

# ---- interactive menu ----
while true; do
  menu
  printf "Pick a number (or type a question): "
  read -r choice
  case "$choice" in
    q|Q|"") exit 0;;
    *[!0-9]*)
      ACTION=$(pick_action "$choice")
      [ -n "$ACTION" ] && run_one "$ACTION" || echo "Could not map that to an action.";;
    *)
      ACTION=$("$PY" -c "import sys;sys.path.insert(0,'.');ks=sorted(__import__('hub.admin_ai',fromlist=['ALLOWED']).ALLOWED);i=int(sys.argv[1])-1;print(ks[i] if 0<=i<len(ks) else '')" "$choice" 2>/dev/null)
      [ -n "$ACTION" ] && run_one "$ACTION";;
  esac
done
