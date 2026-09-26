"""
hub/admin.py - the /admin web console.

Password-protected settings for everything the installer decides, changeable
any time without re-running install.py:

  - privacy mode (retrieval_only / local / cloud)
  - Power Mode on/off (+ chat channel tokens, bot name, timezone)
  - local AI model context/threads, chain tiers
  - cloud provider settings + key env var name
  - reindex documents
  - restart services (agent/chain pick up new settings)

Auth: SHA-256 password hash in data/admin_hash.txt (created by
secure_admin.py or on first run), session cookie after login.
"""

import hashlib
import hmac
import json
import secrets
import subprocess
import sys
import time
from html import escape
from pathlib import Path

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from . import engine
from . import agent_sync
from . import audit
from . import backup as hub_backup
from . import admin_ai as officer

ROOT = Path(__file__).resolve().parent.parent
HASH_FILE = ROOT / "data" / "admin_hash.txt"
COOKIE = "hub_admin"
SESSION_TTL = 12 * 3600

router = APIRouter(prefix="/admin", include_in_schema=False)

# in-memory sessions: token -> expiry (per-process; restart logs everyone out)
_sessions: dict[str, float] = {}


# ---------------------------------------------------------------- auth helpers

def _load_hash() -> str | None:
    if HASH_FILE.exists():
        h = HASH_FILE.read_text().strip()
        return h or None
    return None


def _save_hash(password: str):
    HASH_FILE.parent.mkdir(exist_ok=True)
    HASH_FILE.write_text(hashlib.sha256(password.encode()).hexdigest())
    try:
        import os
        import stat
        os.chmod(HASH_FILE, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def _check_password(password: str) -> bool:
    stored = _load_hash()
    if not stored:
        return False
    return hmac.compare_digest(hashlib.sha256(password.encode()).hexdigest(), stored)


def _is_authed(request: Request) -> bool:
    token = request.cookies.get(COOKIE, "")
    exp = _sessions.get(token, 0)
    if not token or exp < time.time():
        _sessions.pop(token, None)
        return False
    return True


def _new_session() -> str:
    token = secrets.token_urlsafe(32)
    _sessions[token] = time.time() + SESSION_TTL
    # opportunistic cleanup
    now = time.time()
    for t in [t for t, e in _sessions.items() if e < now]:
        _sessions.pop(t, None)
    return token


# ---------------------------------------------------------------- page shell

STYLE = """body{font-family:system-ui,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;
background:#fafafa;color:#222}h1{font-size:1.4rem}h2{margin-top:1.6rem;font-size:1.05rem}
label{display:block;margin:.7rem 0 .2rem;font-weight:600;font-size:.92rem}
input,select{padding:.45rem;border:1px solid #ccc;border-radius:6px;width:100%;max-width:420px;font-size:.95rem}
.card{background:#fff;border:1px solid #ddd;border-radius:8px;padding:1rem 1.2rem;margin:.6rem 0}
button{margin-top:.8rem;padding:.5rem 1.1rem;border:0;border-radius:6px;background:#1a5fb4;color:#fff;cursor:pointer}
button.plain{background:#666}button.danger{background:#c01c28}.ok{color:#1a7f37;font-weight:600}.err{color:#c01c28;font-weight:600}
.small{color:#777;font-size:.82rem}a{color:#1a5fb4}code{background:#eee;padding:.05rem .3rem;border-radius:4px}
table{border-collapse:collapse;width:100%;font-size:.85rem;margin:.4rem 0}td,th{border:1px solid #e3e3e3;padding:.35rem .5rem;text-align:left}"""


def _page(title, body, msg="", err=""):
    banner = f'<p class="ok">{escape(msg)}</p>' if msg else ""
    banner += f'<p class="err">{escape(err)}</p>' if err else ""
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)}</title>
<style>{STYLE}</style></head><body>
<h1>&#9881;&#65039; Office Hub Admin</h1>
<p class="small"><a href="/">assistant</a> &middot; <a href="/status">status</a> &middot; admin</p>
{banner}{body}</body></html>"""


def _sel(options, current):
    return "".join(
        f'<option value="{escape(str(v))}"{" selected" if str(v) == str(current) else ""}>'
        f'{escape(label)}</option>'
        for v, label in options)


# ---------------------------------------------------------------- routes

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def admin_home(request: Request):
    if not _load_hash():
        body = """
<h2>First run — create the admin password</h2>
<form method="post" action="/admin/first-run">
  <label>New admin password</label><input type="password" name="pw" minlength="8" required>
  <label>Repeat it</label><input type="password" name="pw2" minlength="8" required>
  <button>Set password &amp; open the console</button>
</form>"""
        return HTMLResponse(_page("Setup", body))
    if not _is_authed(request):
        body = """
<h2>Sign in</h2>
<form method="post" action="/admin/login">
  <label>Admin password</label><input type="password" name="pw" autofocus required>
  <button>Sign in</button>
</form>"""
        return HTMLResponse(_page("Sign in", body))

    cfg = engine.load_config()
    pm = cfg.get("privacy_mode", "retrieval_only")
    power = bool(cfg.get("power_mode"))
    la = cfg.get("local_ai", {})
    ch = cfg.get("llm_chain", {})
    cloud = cfg.get("cloud", {})

    tg = dc = {"enabled": False, "token": ""}
    agent_cfg_path = ROOT / "data" / "nanobot" / "config.json"
    agent_exists = agent_cfg_path.exists()
    if agent_exists:
        try:
            ac = json.loads(agent_cfg_path.read_text())
            tg = ac.get("channels", {}).get("telegram", tg)
            dc = ac.get("channels", {}).get("discord", dc)
            bot_name = ac.get("agents", {}).get("defaults", {}).get("botName", "Club Assistant")
            tz = ac.get("agents", {}).get("defaults", {}).get("timezone", "")
        except Exception:
            bot_name, tz = "Club Assistant", ""
    else:
        bot_name, tz = "Club Assistant", ""

    # Backups + Officer AI + audit trail data for the cards below
    last_bk = hub_backup.last_snapshot() or {}
    bk_cfg = cfg.get("backup", {})
    sched = hub_backup.schedule_status()
    oconf = officer.load_conf()
    audit_rows = "".join(
        f"<tr><td>{escape(e['time'])}</td><td>{escape(e['actor'])}</td>"
        f"<td>{escape(e['action'])}</td>"
        f"<td>{escape(e['outcome'])}</td></tr>"
        for e in audit.recent(12)) or \
        '<tr><td colspan="4"><i>Nothing logged yet.</i></td></tr>'

    body = f"""
<h2>AI &amp; privacy</h2>
<div class="card">
<form method="post" action="/admin/save">
  <label>Privacy mode</label>
  <select name="privacy_mode">{_sel([
      ("retrieval_only", "1 - Retrieval-only (100% offline, quotes documents)"),
      ("local", "2 - Local AI (100% offline, Qwen writes answers)"),
      ("cloud", "3 - Cloud AI (free/paid API)")], pm)}</select>

  <label>Cloud base URL (mode 3)</label>
  <input name="cloud_base" value="{escape(cloud.get('base_url',''))}">
  <label>Cloud model (mode 3)</label>
  <input name="cloud_model" value="{escape(cloud.get('model',''))}">

  <h2>Local AI engine (modes 1-2)</h2>
  <label>Context size (tokens)</label>
  <input name="ctx" type="number" value="{escape(str(la.get('ctx', 16384)))}" min="2048" max="65536" step="1024">
  <label>Threads</label>
  <input name="threads" type="number" value="{escape(str(la.get('threads', 4)))}" min="1" max="32">
  <div class="small">Model file is managed by the installer (CPU-sized automatically).
  Changing context/threads applies after a service restart below.</div>

  <h2>Fallback chain (mode 2)</h2>
  <label>Tiers</label>
  <select name="chain_disabled">{_sel([
      ("", "local -> free-no-key -> keyed -> paid (recommended)"),
      ("nokey", "local only (disable free-no-key cloud)"),
      ("nokey,keyedfree,paid", "local only (disable all cloud tiers)")], ",".join(cfg.get("llm_chain", {}).get("disabled", [])))}</select>

  <h2>Power Mode</h2>
  <label>Agent layer (chat bots, automations)</label>
  <select name="power_mode">{_sel([
      ("on", "ON - members can chat with the hub"),
      ("off", "off - simple hub")],
      "on" if power else "off")}</select>
  <label>Assistant name</label><input name="bot_name" value="{escape(bot_name)}">
  <label>Timezone (IANA, e.g. America/Chicago)</label><input name="timezone" value="{escape(tz)}">
  <label>Telegram bot token {('<span class="ok">(set)</span>' if tg.get('token') else '<span class="small">(not set)</span>')}</label>
  <input name="tg_token" placeholder="{'leave blank to keep current' if tg.get('token') else 'paste token from @BotFather'}">
  <label>Discord bot token {('<span class="ok">(set)</span>' if dc.get('token') else '<span class="small">(not set)</span>')}</label>
  <input name="dc_token" placeholder="{'leave blank to keep current' if dc.get('token') else 'paste token from the developer portal'}">
  <button>Save settings</button>
</form>
</div>

<h2>Maintenance</h2>
<div class="card">
<form method="post" action="/admin/reindex" style="display:inline">
  <button>Reindex documents</button></form>
<form method="post" action="/admin/restart" style="display:inline">
  <button>Restart AI services</button></form>
  <div class="small">Restart applies privacy/model/chain/agent changes.
  Member chat bots re-read their tokens at restart; pairing approvals are kept.</div>
</div>

<h2>Backups</h2>
<div class="card">
  <p>Last backup: <b>{escape(str(last_bk.get('when', 'never')))}</b>
  &middot; {escape(str(last_bk.get('files', '-')))} files
  &middot; {escape(str(last_bk.get('mb', '?')))} MB
  &middot; member databases: {escape(str(last_bk.get('databases', 'none')))}</p>
  <p>Nightly schedule: <b>{'ON' if sched['installed'] else 'OFF'}</b>
  <span class="small">({escape(sched['detail'])})</span></p>
  <div class="small">Protects your documents, settings, bot memory and the member
  database from a dead drive. Keeps the newest 20 snapshots (30 days), takes one
  at every boot, and can take one every night at 2 AM.</div>
  <form method="post" action="/admin/backup" style="display:inline">
    <input type="hidden" name="action" value="run"><button>Back up now</button></form>
  <form method="post" action="/admin/backup" style="display:inline">
    <input type="hidden" name="action" value="verify"><button class="plain">Check backups</button></form>
  {'<form method="post" action="/admin/backup" style="display:inline">'
   '<input type="hidden" name="action" value="unschedule">'
   '<button class="plain">Turn off nightly backup</button></form>' if sched['installed'] else
   '<form method="post" action="/admin/backup" style="display:inline">'
   '<input type="hidden" name="action" value="schedule">'
   '<button>Turn on nightly backup (02:00)</button></form>'}
  <form method="post" action="/admin/backup">
    <label>Also copy backups to a cloud-synced folder (OneDrive / Dropbox / Google Drive)</label>
    <input name="cloud_dir" value="{escape(bk_cfg.get('cloud_dir', ''))}" placeholder="e.g. C:\\Users\\you\\OneDrive\\Backups">
    <label>Advanced: rclone remote name (optional)</label>
    <input name="rclone_remote" value="{escape(bk_cfg.get('rclone_remote', ''))}" placeholder="e.g. gdrive">
    <input type="hidden" name="action" value="save"><button class="plain">Save backup destinations</button>
  </form>
</div>

<h2>Officer AI (advanced)</h2>
<div class="card">
  <p>An AI helper with real maintenance powers: runs the doctor, checks backups,
  rebuilds the index, installs helper CLIs. It is <b>switched off</b> until you
  turn it on, it only accepts requests with the access key below, and everything
  it does is written to the activity trail at the bottom of this page.</p>
  <form method="post" action="/admin/officer">
    <label>Officer AI</label>
    <select name="enabled">{_sel([("on", "ON - officers can use it (passworded, logged)"),
                                   ("off", "OFF - completely disabled (recommended when not needed)")],
                                  "on" if oconf["enabled"] else "off")}</select>
    <label>Allow use from other computers (over a VPN)</label>
    <select name="remote_allowed">{_sel([("off", "NO - this computer only (safest)"),
                                          ("on", "yes - VPN users with the key may use it")],
                                         "on" if oconf["remote_allowed"] else "off")}</select>
    <input type="hidden" name="action" value="save"><button>Save Officer AI settings</button>
  </form>
  <p class="small">Officer access key: <code>{escape(oconf['token']) or '<i>(created when first enabled)</i>'}</code>
  &mdash; give this only to trusted officers.</p>
  <form method="post" action="/admin/officer" style="display:inline">
    <input type="hidden" name="action" value="new_token"><button class="plain">Make a new key</button></form>
  <form method="post" action="/admin/officer" style="display:inline">
    <input type="hidden" name="action" value="install_clis"><button class="plain">Install helper CLIs (opencode, freebuff)</button></form>
</div>

<h2>Security</h2>
<div class="card">
<form method="post" action="/admin/password">
  <label>Change admin password</label>
  <input type="password" name="pw" minlength="8" required>
  <button>Change password</button></form>
<form method="post" action="/admin/logout" style="display:inline">
  <button class="danger">Sign out</button></form>
</div>

<h2>Activity trail</h2>
<div class="card">
<table><tr><th>When</th><th>Who</th><th>What</th><th>Result</th></tr>{audit_rows}</table>
<div class="small">Every admin and Officer AI action is recorded in
<code>data/audit.jsonl</code> and included in every backup.</div>
</div>"""
    return HTMLResponse(_page("Console", body))


@router.post("/first-run")
def first_run(pw: str = Form(""), pw2: str = Form("")):
    if _load_hash():
        return RedirectResponse("/admin", status_code=303)
    if pw != pw2 or len(pw) < 8:
        return RedirectResponse("/admin?e=" + escape("Passwords must match and be 8+ characters"), status_code=303)
    _save_hash(pw)
    audit.record("admin.first_run", actor="admin")
    return RedirectResponse("/admin", status_code=303)


@router.post("/login")
def login(pw: str = Form("")):
    if _check_password(pw):
        audit.record("admin.login", actor="admin")
        resp = RedirectResponse("/admin", status_code=303)
        resp.set_cookie(COOKIE, _new_session(), httponly=True, samesite="lax")
        return resp
    audit.record("admin.login", actor="unknown", outcome="failed")
    return RedirectResponse("/admin?e=Wrong%20password", status_code=303)


@router.post("/logout")
def logout():
    audit.record("admin.logout", actor="admin")
    resp = RedirectResponse("/admin", status_code=303)
    resp.delete_cookie(COOKIE)
    return resp


@router.post("/password")
async def change_password(request: Request, pw: str = Form("")):
    if not _is_authed(request) or len(pw) < 8:
        return RedirectResponse("/admin", status_code=303)
    _save_hash(pw)
    audit.record("admin.password_changed", actor="admin")
    return RedirectResponse("/admin?m=Password%20changed", status_code=303)


def _restart_ai_services():
    """Restart llama-server, chain, and agent so saved settings take effect."""
    root = ROOT
    is_win = sys.platform == "win32"
    runner = [] if is_win else ["bash"]
    ext = ".bat" if is_win else ".sh"
    for pidfile in ("llama", "chain", "agent"):
        p = root / "data" / f"{pidfile}.pid"
        if p.exists():
            try:
                pid = int(p.read_text().strip())
                if is_win:
                    subprocess.run(["taskkill", "/pid", str(pid), "/f"],
                                   capture_output=True)
                else:
                    subprocess.run(["kill", str(pid)], capture_output=True)
            except (ValueError, ProcessLookupError):
                pass
            p.unlink(missing_ok=True)
    subprocess.Popen(runner + [str(root / f"start-hub{ext}")] if is_win else
                     runner + [str(root / "start-hub.sh")],
                     cwd=str(root),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


@router.post("/save")
async def save(request: Request,
               privacy_mode: str = Form("retrieval_only"),
               cloud_base: str = Form(""), cloud_model: str = Form(""),
               ctx: int = Form(16384), threads: int = Form(4),
               chain_disabled: str = Form(""),
               power_mode: str = Form("off"),
               bot_name: str = Form("Club Assistant"),
               timezone: str = Form(""),
               tg_token: str = Form(""), dc_token: str = Form("")):
    if not _is_authed(request):
        return RedirectResponse("/admin", status_code=303)

    cfg = engine.load_config()
    if privacy_mode in ("retrieval_only", "local", "cloud"):
        cfg["privacy_mode"] = privacy_mode
    if privacy_mode == "cloud":
        if cloud_base:
            cfg["cloud"]["base_url"] = cloud_base.strip()
        if cloud_model:
            cfg["cloud"]["model"] = cloud_model.strip()
    la = cfg.setdefault("local_ai", {})
    la["ctx"] = max(2048, min(65536, ctx))
    la["threads"] = max(1, min(32, threads))

    disabled = [t for t in chain_disabled.split(",") if t]
    cfg.setdefault("llm_chain", {})["disabled"] = disabled

    power = power_mode == "on"
    cfg["power_mode"] = power

    # agent-side settings (bot name, timezone, tokens)
    if power:
        acfg_path = ROOT / "data" / "nanobot" / "config.json"
        if acfg_path.exists():
            ac = json.loads(acfg_path.read_text())
            d = ac.setdefault("agents", {}).setdefault("defaults", {})
            if bot_name.strip():
                d["botName"] = bot_name.strip()
            if timezone.strip():
                d["timezone"] = timezone.strip()
            if tg_token.strip():
                ac.setdefault("channels", {}).setdefault("telegram", {})
                ac["channels"]["telegram"]["enabled"] = True
                ac["channels"]["telegram"]["token"] = tg_token.strip()
            if dc_token.strip():
                ac.setdefault("channels", {}).setdefault("discord", {})
                ac["channels"]["discord"]["enabled"] = True
                ac["channels"]["discord"]["token"] = dc_token.strip()
            # agent follows the chain endpoint in local/retrieval modes
            if privacy_mode in ("local", "retrieval_only"):
                ac.setdefault("providers", {})["custom"] = {
                    "apiKey": "local", "apiBase": "http://127.0.0.1:8085/v1"}
                d["model"] = "local/hub-chain"
                # keep the agent's window inside llama.cpp's ctx or big agent
                # turns 400 with exceed_context_size again
                d["contextWindowTokens"] = max(4096, la["ctx"] - 2048)
            else:
                ac["providers"]["custom"] = {
                    "apiKey": None, "apiBase": cfg["cloud"]["base_url"]}
                d["model"] = f"custom/{cfg['cloud']['model']}"
            acfg_path.write_text(json.dumps(ac, indent=2))
            agent_sync.sync_docs_to_agent(cfg)

    audit.record("admin.save", actor="admin", privacy_mode=privacy_mode,
                 power_mode=power, tg_token_set=bool(tg_token.strip()),
                 dc_token_set=bool(dc_token.strip()),
                 chain_disabled=chain_disabled or "none",
                 ctx=la["ctx"], threads=la["threads"])
    (ROOT / "config.json").write_text(json.dumps(cfg, indent=2))
    _restart_ai_services()
    return RedirectResponse("/admin?m=Saved%20-%20services%20restarting", status_code=303)


@router.post("/reindex")
async def reindex(request: Request):
    if not _is_authed(request):
        return RedirectResponse("/admin", status_code=303)
    cfg = engine.load_config()
    docs, chunks = engine.reindex(cfg, verbose=False)
    agent_sync.sync_docs_to_agent(cfg)
    audit.record("admin.reindex", actor="admin", documents=docs, chunks=chunks)
    return RedirectResponse(
        f"/admin?m=Indexed%20{docs}%20documents%2C%20{chunks}%20chunks",
        status_code=303)


@router.post("/restart")
async def restart(request: Request):
    if not _is_authed(request):
        return RedirectResponse("/admin", status_code=303)
    audit.record("admin.restart", actor="admin")
    _restart_ai_services()
    return RedirectResponse("/admin?m=AI%20services%20restarting", status_code=303)


# ------------------------------------------------------------- backups

@router.post("/backup")
async def backup_action(request: Request, action: str = Form(""),
                        cloud_dir: str = Form(""),
                        rclone_remote: str = Form("")):
    if not _is_authed(request):
        return RedirectResponse("/admin", status_code=303)
    cfg = engine.load_config()
    if action == "run":
        import threading
        threading.Thread(target=hub_backup.snapshot,
                         args=("manual",), daemon=True).start()
        audit.record("admin.backup_now", actor="admin")
        return RedirectResponse(
            "/admin?m=Backup%20started%20-%20refresh%20in%20a%20moment",
            status_code=303)
    if action == "verify":
        checked, bad = hub_backup.verify()
        audit.record("admin.backup_verify", actor="admin",
                     files=checked, bad=len(bad))
        msg = f"Backup check: {checked} files verified OK" if not bad else \
            f"Backup check: {len(bad)} of {checked} files CORRUPT - see data/backups"
        return RedirectResponse("/admin?m=" + _q(msg), status_code=303)
    if action == "save":
        b = cfg.setdefault("backup", {})
        b["cloud_dir"] = cloud_dir.strip()
        b["rclone_remote"] = rclone_remote.strip()
        audit.record("admin.backup_settings", actor="admin",
                     cloud_dir=b["cloud_dir"] or "none",
                     rclone=b["rclone_remote"] or "none")
        (ROOT / "config.json").write_text(json.dumps(cfg, indent=2))
        return RedirectResponse("/admin?m=Backup%20settings%20saved", status_code=303)
    if action == "schedule":
        res = hub_backup.schedule_install()
        audit.record("admin.backup_schedule", actor="admin", **res)
        msg = "Nightly backup scheduled (02:00)" if res["installed"] \
            else f"Could not schedule: {res.get('detail', 'unknown')}"
        return RedirectResponse("/admin?m=" + _q(msg), status_code=303)
    if action == "unschedule":
        res = hub_backup.schedule_remove()
        audit.record("admin.backup_schedule", actor="admin", removed=True)
        return RedirectResponse("/admin?m=Nightly%20backup%20schedule%20removed",
                                status_code=303)
    return RedirectResponse("/admin", status_code=303)


# ------------------------------------------------- officer AI (advanced)

@router.post("/officer")
async def officer_action(request: Request, action: str = Form(""),
                         enabled: str = Form("off"),
                         remote_allowed: str = Form("off")):
    if not _is_authed(request):
        return RedirectResponse("/admin", status_code=303)
    conf = officer.load_conf()
    if action == "save":
        conf["enabled"] = enabled == "on"
        conf["remote_allowed"] = remote_allowed == "on"
        if not conf["token"]:
            conf["token"] = secrets.token_urlsafe(24)
        officer.save_conf(conf)
        audit.record("admin.officer_ai", actor="admin",
                     enabled=conf["enabled"],
                     remote=conf["remote_allowed"])
        return RedirectResponse("/admin?m=Officer%20AI%20settings%20saved",
                                status_code=303)
    if action == "new_token":
        conf["token"] = secrets.token_urlsafe(24)
        officer.save_conf(conf)
        audit.record("admin.officer_token", actor="admin", outcome="rotated")
        return RedirectResponse("/admin?m=New%20Officer%20AI%20key%20generated",
                                status_code=303)
    if action == "install_clis":
        import threading
        threading.Thread(target=officer.install_clis, daemon=True).start()
        audit.record("admin.officer_clis", actor="admin")
        return RedirectResponse(
            "/admin?m=CLI%20install%20started%20-%20check%20data%2Fhub.log",
            status_code=303)
    return RedirectResponse("/admin", status_code=303)


def _q(text: str) -> str:
    from urllib.parse import quote
    return quote(text)
