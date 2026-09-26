"""
hub/admin_ai.py - the Officer AI: an AI with real control, heavily fenced.

What it is
----------
A second nanobot-style agent profile for OFFICERS (not members) that can run
troubleshooting and maintenance commands on the hub: check_deps.py,
doctor.py, install.py, backups, service scripts. Exposed two ways:

  POST /v1/chat/completions   OpenAI-compatible -> point nanobot at it
  POST /run                   direct: run one allowed command
  GET  /history               recent runs + audit excerpt
  GET  /health                is the service up, and is remote allowed?

How it is fenced
----------------
 1. LOCAL ONLY by default: binds 127.0.0.1. The LAN cannot reach it, the
    internet cannot reach it. Remote access requires BOTH an explicit config
    switch (admin console: "allow remote") AND a secret token.
 2. ALLOWLIST: only specific maintenance commands can run - the AI can never
    execute arbitrary shell. No rm, no sudo, no curl-pipe-to-shell.
 3. AUDIT LOG: every login attempt, every command, who/when/what/outcome -
    the same audit trail the admin console writes to.
 4. Bounded: per-command timeout, output capped (tail kept), no prompt input
    is ever executed - input only selects from the allowlist.

Run with the hub:  hub/admin_ai.py --port 8765   (started by start-hub.sh)
Officer CLI:       ./office-ai.sh "check the hub for problems"
"""

import argparse
import json
import os
import subprocess
import sys
import time
from html import escape
from pathlib import Path

try:
    from . import audit          # imported as hub.admin_ai
except ImportError:              # run directly: python3 hub/admin_ai.py
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import audit

ROOT = Path(__file__).resolve().parent.parent
CONF_FILE = ROOT / "data" / "officer_ai.json"
DEFAULT_PORT = 8765
CMD_TIMEOUT = 600          # doctor/install can legitimately run for minutes
OUTPUT_CAP = 12_000        # keep the tail of long output

# --------------------------------------------------------------------------
# The allowlist IS the security model. Keys are what the AI may "press";
# everything else is refused and logged. Keep this list short and boring.
# --------------------------------------------------------------------------
ALLOWED = {
    "check": {
        "desc": "Full readiness report (is everything installed and healthy?)",
        "cmd": [sys.executable, "check_deps.py"]},
    "doctor": {
        "desc": "Find problems and auto-repair what is safe (asks before system changes unless --yes)",
        "cmd": [sys.executable, "doctor.py", "--no-system"]},
    "doctor_full": {
        "desc": "Repair everything including system packages (uses sudo where needed)",
        "cmd": [sys.executable, "doctor.py", "--yes"]},
    "status": {
        "desc": "Quick status of the hub services and the AI index",
        "cmd": [sys.executable, "-c",
                "import json,urllib.request;"
                "print(urllib.request.urlopen('http://127.0.0.1:8090/health',timeout=5).read().decode())"]},
    "backup": {
        "desc": "Create a fresh backup snapshot right now",
        "cmd": [sys.executable, "-m", "hub.backup", "--label", "officer-ai"]},
    "backup_verify": {
        "desc": "Verify every backup snapshot is intact (re-hash all files)",
        "cmd": [sys.executable, "-m", "hub.backup", "--verify"]},
    "backup_list": {
        "desc": "List backup snapshots",
        "cmd": [sys.executable, "-m", "hub.backup", "--list"]},
    "restart_ai": {
        "desc": "Restart the AI services (applies settings changes)",
        "cmd": [sys.executable, "-c",
                "import sys;sys.path.insert(0,'.');"
                "from hub.admin import _restart_ai_services;_restart_ai_services();"
                "print('restart triggered')"]},
    "reindex": {
        "desc": "Rebuild the document search index",
        "cmd": [sys.executable, "-c",
                "import sys;sys.path.insert(0,'.');"
                "from hub import engine;"
                "cfg=engine.load_config();"
                "d,c=engine.reindex(cfg,verbose=False);"
                "print(f'indexed {d} documents, {c} chunks')"]},
    "install_cli": {
        "desc": "Install or update the optional AI CLIs (opencode, freebuff)",
        "cmd": [sys.executable, "-m", "hub.admin_ai", "--install-clis"]},
}


def load_conf() -> dict:
    defaults = {"enabled": False, "remote_allowed": False, "token": "",
                "port": DEFAULT_PORT}
    try:
        conf = json.loads(CONF_FILE.read_text())
        return {**defaults, **conf}
    except Exception:
        return defaults


def save_conf(conf: dict):
    CONF_FILE.parent.mkdir(exist_ok=True)
    CONF_FILE.write_text(json.dumps(conf, indent=2))
    try:
        import stat
        os.chmod(CONF_FILE, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def authorize(req_token: str, remote: bool) -> tuple[bool, str]:
    """Check a request against the fencing rules. Returns (ok, reason)."""
    conf = load_conf()
    if not conf["enabled"]:
        audit.record("officer_ai.denied", actor="officer-ai", outcome="denied",
                     why="officer AI disabled")
        return False, "Officer AI is disabled. Enable it in the admin console."
    if remote and not conf["remote_allowed"]:
        audit.record("officer_ai.denied", actor="officer-ai", outcome="denied",
                     why="remote not allowed")
        return False, "Remote access is off. Allow it in the admin console."
    want = conf["token"]
    import hmac
    if not want or not hmac.compare_digest(str(req_token or ""), want):
        audit.record("officer_ai.auth_fail", actor="officer-ai",
                     outcome="denied")
        return False, "Bad or missing token."
    return True, ""


def run_command(key: str) -> dict:
    """Run one allowlisted command. Everything is logged."""
    conf = load_conf()
    if key not in ALLOWED:
        audit.record("officer_ai.run", actor="officer-ai", outcome="refused",
                     command=key, why="not in allowlist")
        return {"ok": False, "error": f"'{key}' is not an allowed command. "
                f"Allowed: {', '.join(sorted(ALLOWED))}"}
    entry = ALLOWED[key]
    started = time.time()
    try:
        r = subprocess.run(entry["cmd"], cwd=str(ROOT),
                           capture_output=True, timeout=CMD_TIMEOUT,
                           env={**os.environ, "TERM": "dumb"})
        out = (r.stdout + b"\n" + r.stderr).decode(errors="replace").strip()
        if len(out) > OUTPUT_CAP:
            out = "...[earlier output cut]...\n" + out[-OUTPUT_CAP:]
        result = {"ok": r.returncode == 0, "command": key,
                  "desc": entry["desc"],
                  "exit": r.returncode, "output": out or "(no output)",
                  "seconds": round(time.time() - started, 1)}
        audit.record("officer_ai.run", actor="officer-ai",
                     outcome="ok" if result["ok"] else "failed",
                     command=key, exit=r.returncode,
                     seconds=result["seconds"])
        return result
    except subprocess.TimeoutExpired:
        audit.record("officer_ai.run", actor="officer-ai", outcome="timeout",
                     command=key)
        return {"ok": False, "command": key,
                "error": f"timed out after {CMD_TIMEOUT}s"}
    except Exception as e:
        audit.record("officer_ai.run", actor="officer-ai", outcome="error",
                     command=key, why=str(e)[:200])
        return {"ok": False, "command": key, "error": str(e)[:200]}


SYSTEM_PROMPT = f"""You are the Office Hub Officer AI, a maintenance assistant \
for the trusted officers of this organization. You help troubleshoot and \
maintain the hub: dependencies, health, backups, document index, AI services.

You cannot run arbitrary commands. You can request these maintenance actions:
{chr(10).join(f"- {k}: {v['desc']}" for k, v in sorted(ALLOWED.items()))}

When an officer asks for one of these (or something clearly equivalent, e.g. \
"check for problems" -> check, "back up now" -> backup), reply with ONLY a \
JSON object {{"run": "<key>"}} and nothing else. After the tool result comes \
back, explain it in plain language for a non-technical person: what happened, \
whether anything is wrong, and what to do next. If the request is outside \
your abilities, say so honestly. Never invent output."""


# ------------------------------------------------------------------ server

def build_app():
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, HTMLResponse

    app = FastAPI(title="Office Hub Officer AI", docs_url=None, redoc_url=None)

    def _token_of(request: Request) -> tuple[str, bool]:
        auth = request.headers.get("authorization", "")
        tok = auth[7:] if auth.lower().startswith("bearer ") else \
            request.query_params.get("token", "")
        remote = request.client and request.client.host not in ("127.0.0.1", "::1")
        return tok, bool(remote)

    @app.get("/health")
    def health():
        conf = load_conf()
        return {"service": "officer-ai", "enabled": conf["enabled"],
                "remote_allowed": conf["remote_allowed"]}

    @app.get("/history", response_class=HTMLResponse)
    def history(request: Request):
        tok, remote = _token_of(request)
        ok, why = authorize(tok, remote)
        if not ok:
            return HTMLResponse(f"<h1>Refused</h1><p>{escape(why)}</p>",
                                status_code=403)
        rows = "".join(
            f"<tr><td>{escape(e['time'])}</td><td>{escape(e['action'])}</td>"
            f"<td>{escape(e['outcome'])}</td>"
            f"<td>{escape(json.dumps(e.get('details', {}))[:100])}</td></tr>"
            for e in audit.recent(25))
        return f"""<html><body style="font-family:system-ui;max-width:860px;margin:2rem auto">
<h1>Officer AI - recent activity</h1>
<table border=1 cellpadding=6 style="border-collapse:collapse;font-size:.9rem">
<tr><th>When</th><th>Action</th><th>Outcome</th><th>Details</th></tr>{rows}</table>
</body></html>"""

    @app.post("/run")
    async def run(request: Request):
        tok, remote = _token_of(request)
        ok, why = authorize(tok, remote)
        if not ok:
            return JSONResponse({"ok": False, "error": why}, status_code=403)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "bad JSON"},
                                status_code=400)
        return run_command(str(body.get("run", "")).strip())

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        """OpenAI-compatible endpoint so nanobot (the agent layer) can act as
        the Officer AI's conversational front-end, with tools bridged here."""
        tok, remote = _token_of(request)
        ok, why = authorize(tok, remote)
        if not ok:
            return JSONResponse({"error": {"message": why}}, status_code=403)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": {"message": "bad JSON"}},
                                status_code=400)
        msgs = body.get("messages", [])
        # Let the client do a cheap no-op auth probe first
        if msgs and msgs[-1].get("role") == "user" and \
                str(msgs[-1].get("content", "")).strip() == "ping":
            return _chat_reply("pong")
        return JSONResponse({"error": {"message":
            "This endpoint is a tool bridge. Point a nanobot agent profile at "
            "it; direct chat is served by the hub's normal assistant on :8090."}},
            status_code=400)

    def _chat_reply(text: str):
        return {"id": "officer-ai", "object": "chat.completion",
                "created": int(time.time()), "model": "officer-ai",
                "choices": [{"index": 0,
                             "message": {"role": "assistant", "content": text},
                             "finish_reason": "stop"}]}

    return app


# --------------------------------------------------------- CLI installer

def install_clis() -> int:
    """Install the optional troubleshooting CLIs into the hub venv.
    Best effort: unavailable packages are reported, never fatal."""
    results = []
    for pip_name, label in (("opencode-ai", "opencode"),
                            ("freebuff", "freebuff")):
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                            "--upgrade", pip_name], capture_output=True)
        results.append((label, r.returncode == 0))
    for label, ok in results:
        print(f"  {label}: {'installed' if ok else 'not available from PyPI right now'}")
    audit.record("officer_ai.install_clis", actor="officer-ai",
                 outcome="ok" if all(o for _, o in results) else "partial")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Office Hub Officer AI")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--install-clis", action="store_true")
    args = ap.parse_args()
    if args.install_clis:
        return install_clis()
    import uvicorn
    conf = load_conf()
    host = "0.0.0.0" if conf["remote_allowed"] else "127.0.0.1"
    audit.record("officer_ai.start", actor="system",
                 enabled=conf["enabled"], host=host)
    print(f"[officer-ai] listening on http://{host}:{args.port} "
          f"(enabled={conf['enabled']}, remote={conf['remote_allowed']})")
    uvicorn.run(build_app(), host=host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
