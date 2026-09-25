"""
hub/server.py - FastAPI glue for the Office Hub.

Endpoints:
  GET  /          - tiny chat UI (no build step, no CDN)
  GET  /health    - mode, index stats, AI engine reachability
  POST /ask       - {"question": "..."} -> answer + sources
  POST /reindex   - rescan documents/ folder

Run:  .venv/bin/python -m uvicorn hub.server:app --host 0.0.0.0 --port 8090
(the start-hub scripts do this for you)
"""

import json
import socket
import threading
import time
from html import escape
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from . import engine
from . import agent_sync

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title="Office Hub AI", docs_url=None, redoc_url=None)

_lock = threading.Lock()  # SQLite writes are serialized; requests stay snappy


def _cfg():
    return engine.load_config()


class AskIn(BaseModel):
    question: str


@app.on_event("startup")
def startup():
    cfg = _cfg()
    if engine.index_is_stale(cfg):
        print("[hub] documents changed since last index - reindexing ...")
        with _lock:
            docs, chunks = engine.reindex(cfg, verbose=False)
        print(f"[hub] indexed {docs} doc(s), {chunks} chunk(s)")


@app.get("/", response_class=HTMLResponse)
def home():
    return """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Office Hub Assistant</title>
<style>
 body{font-family:system-ui,Segoe UI,sans-serif;max-width:720px;margin:2rem auto;padding:0 1rem;background:#fafafa;color:#222}
 h1{font-size:1.3rem} #log{border:1px solid #ddd;border-radius:8px;padding:1rem;min-height:200px;background:#fff}
 .q{color:#1a5fb4;margin:.6rem 0 .2rem;font-weight:600}.a{margin:.2rem 0 1rem;white-space:pre-wrap}
 .src{color:#777;font-size:.8rem}.meta{color:#999;font-size:.75rem}
 form{display:flex;gap:.5rem;margin-top:1rem}input{flex:1;padding:.6rem;border:1px solid #ccc;border-radius:6px}
 button{padding:.6rem 1.2rem;border:0;border-radius:6px;background:#1a5fb4;color:#fff;cursor:pointer}
</style></head><body>
<h1>&#127968; Office Hub Assistant</h1>
<p style="color:#777">Answers come from your own documents in <code>documents/</code>.</p>
<div id="log"></div>
<form onsubmit="return go()"><input id="q" placeholder="Ask about minutes, dues, bylaws..." autocomplete="off"><button>Ask</button></form>
<script>
async function go(){
  const q=document.getElementById('q');const log=document.getElementById('log');
  const question=q.value.trim();if(!question)return false;q.value='';
  log.insertAdjacentHTML('beforeend',`<div class="q">${question.replace(/</g,'&lt;')}</div><div class="a">thinking...</div>`);
  const els=log.querySelectorAll('.a');const el=els[els.length-1];
  try{
    const r=await fetch('/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question})});
    const d=await r.json();
    el.textContent=d.answer||('Error: '+(d.detail||d.error||'unknown'));
    if(d.sources&&d.sources.length)el.insertAdjacentHTML('beforeend',`<div class="src">Sources: ${d.sources.map(s=>s.replace(/</g,'&lt;')).join(', ')}</div>`);
    if(d.backend)el.insertAdjacentHTML('beforeend',`<div class="meta">${d.backend} &middot; ${d.took_s}s</div>`);
  }catch(e){el.textContent='Hub unreachable: '+e}
  return false;
}
</script></body></html>"""


@app.get("/health")
def health():
    cfg = _cfg()
    out = {"ok": True, "privacy_mode": cfg["privacy_mode"],
           "model": cfg.get("model_label") or cfg["cloud"]["model"] or "documents-only",
           "stale_index": engine.index_is_stale(cfg)}
    db = ROOT / cfg["index"]["db_path"]
    if db.exists():
        import sqlite3
        con = sqlite3.connect(str(db))
        out["documents_indexed"] = con.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        out["chunks"] = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        con.close()
    if cfg["privacy_mode"] == "local":
        import urllib.request
        la = cfg["local_ai"]
        try:
            urllib.request.urlopen(f"http://{la['host']}:{la['port']}/health", timeout=3)
            out["local_ai"] = "up"
        except Exception:
            out["local_ai"] = "down (start-hub should have launched it)"
    return out


@app.post("/ask")
def ask_endpoint(body: AskIn):
    cfg = _cfg()
    if engine.index_is_stale(cfg):
        with _lock:
            engine.reindex(cfg, verbose=False)  # pick up new files on the fly
    if cfg.get("power_mode"):
        agent_sync.sync_docs_to_agent(cfg)  # keep the member bot's copy fresh
    try:
        with _lock:
            result = engine.ask(body.question.strip(), cfg)
            engine.log_question(cfg, body.question.strip(), result["backend"],
                                result["took_s"], result["sources"])
        return result
    except RuntimeError as e:
        return JSONResponse(status_code=503, content={"error": str(e)})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/status", response_class=HTMLResponse)
def status_page():
    cfg = _cfg()

    def port_open(port):
        s = socket.socket()
        s.settimeout(0.6)
        try:
            return s.connect_ex(("127.0.0.1", port)) == 0
        finally:
            s.close()

    services = [
        ("MariaDB (database)", 3306, "for the portal + forum"),
        ("Admidio (member portal)", 8080, "http://localhost:8080"),
        ("Flarum (forum / chat)", 8081, "http://localhost:8081"),
        ("llama.cpp (local AI)", cfg["local_ai"]["port"]
         if cfg["privacy_mode"] == "local" else None,
         "only used in local privacy mode"),
        ("Office Hub API (this page)", cfg["server"]["port"], "http://localhost:8090"),
    ]
    if cfg.get("power_mode"):
        services.append(("Agent gateway (Power Mode)", 18790,
                         "chat apps + automations (nanobot)"))
    rows = ""
    for name, port, where in services:
        if port is None:
            state = "<i>not used in this privacy mode</i>"
        else:
            up = port_open(port)
            state = ('<b style="color:#1a7f37">UP</b>' if up
                     else '<b style="color:#c01c28">DOWN</b>')
        rows += f"<tr><td>{escape(name)}</td><td>{state}</td><td>{escape(str(where))}</td></tr>"

    db = ROOT / cfg["index"]["db_path"]
    docs = chunks = "?"
    if db.exists():
        import sqlite3
        con = sqlite3.connect(str(db))
        docs = con.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        chunks = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        con.close()

    qs = engine.recent_questions(cfg, 20)
    qrows = "".join(
        f"<tr><td>{time.strftime('%Y-%m-%d %H:%M', time.localtime(q['ts']))}</td>"
        f"<td>{escape(q['question'])}</td><td>{escape(q['backend'])}</td>"
        f"<td>{q['took_s']}s</td><td>{'ok' if q['ok'] else 'error'}</td></tr>"
        for q in qs) or "<tr><td colspan=5><i>No questions asked yet.</i></td></tr>"

    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="30"><title>Hub Status</title>
<style>body{{font-family:system-ui,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem}}
table{{border-collapse:collapse;width:100%;margin:.5rem 0 1.5rem}}
td,th{{border:1px solid #ddd;padding:.4rem .6rem;text-align:left;font-size:.9rem}}
h2{{margin-top:1.5rem}}a{{color:#1a5fb4}}</style></head><body>
<h1>&#128200; Office Hub Status</h1>
<p>Privacy mode: <b>{escape(cfg['privacy_mode'])}</b> &middot; refreshes every 30s &middot;
<a href="/">&larr; back to the assistant</a></p>
<h2>Services</h2><table><tr><th>Component</th><th>Status</th><th>Where</th></tr>{rows}</table>
<h2>Document index</h2><p>{docs} document(s) indexed, {chunks} searchable chunk(s).
Files live in <code>documents/</code> &middot; <form style="display:inline"
method="post" action="/reindex"><button>Reindex now</button></form> (returns JSON).</p>
<h2>Recent questions</h2>
<table><tr><th>When</th><th>Question</th><th>Backend</th><th>Time</th><th>Result</th></tr>{qrows}</table>
</body></html>"""
def reindex_endpoint():
    cfg = _cfg()
    with _lock:
        docs, chunks = engine.reindex(cfg, verbose=False)
    return {"ok": True, "documents": docs, "chunks": chunks}
