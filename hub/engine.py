"""
hub/engine.py - the AI brain of the Office Hub.

Three privacy modes, one interface:
  retrieval_only - answers come ONLY from documents in documents/ (offline)
  local          - Qwen GGUF via llama.cpp server, fed retrieved documents
  cloud          - OpenAI-compatible free-tier API, fed retrieved documents

Documents are indexed with SQLite FTS5 (keyword search - fast, zero extra
models). PDFs are read with pypdf. Rebuild the index any time with reindex().
"""

import json
import re
import sqlite3
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"


def load_config():
    if not CONFIG_PATH.exists():
        raise SystemExit("config.json not found - run install.py first.")
    return json.loads(CONFIG_PATH.read_text())


# ----------------------------------------------------------------------------
# Document reading
# ----------------------------------------------------------------------------

def read_document(path: Path) -> str:
    """Extract plain text from .txt/.md/.csv or .pdf files."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            try:
                from pypdf import PdfReader
            except ImportError:
                return ""  # pypdf not installed; PDFs skipped silently
            reader = PdfReader(str(path))
            return "\n".join((page.extract_text() or "") for page in reader.pages)
        if suffix in (".txt", ".md", ".csv", ".html", ".htm", ".log", ".rtf"):
            return path.read_text(errors="ignore")
    except Exception as e:
        print(f"  [index] could not read {path.name}: {e}")
    return ""


def iter_documents(docs_dir: Path):
    for path in sorted(docs_dir.rglob("*")):
        if path.is_file() and not path.name.startswith(".") and not path.name.startswith("~"):
            yield path


# ----------------------------------------------------------------------------
# Indexer (SQLite FTS5 - no embedding model needed at club scale)
# ----------------------------------------------------------------------------

def _db(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.execute("CREATE TABLE IF NOT EXISTS docs ("
                "path TEXT PRIMARY KEY, mtime REAL, chars INTEGER)")
    con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5("
                "doc_path, text, tokenize='porter unicode61')")
    con.execute("CREATE TABLE IF NOT EXISTS questions ("
                "ts REAL, question TEXT, backend TEXT, took_s REAL, "
                "sources TEXT, ok INTEGER)")
    return con


# ----------------------------------------------------------------------------
# Question history (powers the /status page)
# ----------------------------------------------------------------------------

def log_question(cfg, question, backend, took_s, sources, ok=True):
    con = _db(ROOT / cfg["index"]["db_path"])
    try:
        con.execute("INSERT INTO questions VALUES (?,?,?,?,?,?)",
                    (time.time(), question[:500], backend, took_s,
                     ", ".join(sources)[:300], int(ok)))
        # keep the table bounded
        con.execute("DELETE FROM questions WHERE ts NOT IN "
                    "(SELECT ts FROM questions ORDER BY ts DESC LIMIT 500)")
        con.commit()
    finally:
        con.close()


def recent_questions(cfg, limit=20):
    con = _db(ROOT / cfg["index"]["db_path"])
    try:
        rows = con.execute("SELECT ts, question, backend, took_s, sources, ok "
                           "FROM questions ORDER BY ts DESC LIMIT ?",
                           (limit,)).fetchall()
        return [{"ts": r[0], "question": r[1], "backend": r[2],
                 "took_s": r[3], "sources": r[4], "ok": bool(r[5])}
                for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def index_document(con, path: Path, cfg):
    text = read_document(path)
    rel = str(path.relative_to(ROOT))
    con.execute("DELETE FROM chunks WHERE doc_path = ?", (rel,))
    con.execute("DELETE FROM docs WHERE path = ?", (rel,))
    if not text.strip():
        con.execute("INSERT INTO docs VALUES (?,?,0)", (rel, path.stat().st_mtime))
        return 0
    size = cfg["index"]["chunk_chars"]
    overlap = cfg["index"]["chunk_overlap"]
    chunks, step = 0, max(1, size - overlap)
    for i in range(0, len(text), step):
        piece = text[i:i + size].strip()
        if piece:
            con.execute("INSERT INTO chunks (doc_path, text) VALUES (?,?)", (rel, piece))
            chunks += 1
    con.execute("INSERT INTO docs VALUES (?,?,?)", (rel, path.stat().st_mtime, len(text)))
    return chunks


def reindex(cfg, verbose=True):
    docs_dir = ROOT / cfg["index"]["documents_dir"]
    docs_dir.mkdir(exist_ok=True)
    con = _db(ROOT / cfg["index"]["db_path"])
    n_docs, n_chunks = 0, 0
    for path in iter_documents(docs_dir):
        c = index_document(con, path, cfg)
        if verbose and c:
            print(f"  [index] {path.relative_to(ROOT)}: {c} chunk(s)")
        n_docs += 1
        n_chunks += c
    con.commit()
    con.close()
    return n_docs, n_chunks


def index_is_stale(cfg) -> bool:
    docs_dir = ROOT / cfg["index"]["documents_dir"]
    con = _db(ROOT / cfg["index"]["db_path"])
    try:
        for path in iter_documents(docs_dir):
            row = con.execute("SELECT mtime FROM docs WHERE path = ?",
                              (str(path.relative_to(ROOT)),)).fetchone()
            if row is None or row[0] != path.stat().st_mtime:
                return True
        return False
    finally:
        con.close()


# ----------------------------------------------------------------------------
# Retrieval
# ----------------------------------------------------------------------------

def retrieve(query: str, cfg, top_k=None):
    """Return list of (doc_path, snippet) most relevant to the query."""
    con = _db(ROOT / cfg["index"]["db_path"])
    try:
        # Sanitize FTS5 query syntax (quotes) and OR the words together.
        words = [w for w in re.findall(r"[\w']+", query) if len(w) > 1][:12]
        if not words:
            return []
        fts_query = " OR ".join(f'"{w}"' for w in words)
        k = top_k or cfg["index"]["top_k"]
        rows = con.execute(
            "SELECT doc_path, snippet(chunks, 1, '>>', '<<', '...', 18), "
            "bm25(chunks) FROM chunks WHERE chunks MATCH ? ORDER BY bm25(chunks) LIMIT ?",
            (fts_query, k)).fetchall()
        return [(r[0], r[1]) for r in rows]
    finally:
        con.close()


# ----------------------------------------------------------------------------
# Backends
# ----------------------------------------------------------------------------

def _local_answer(cfg, system_prompt, user_prompt):
    la = cfg["local_ai"]
    base = f"http://{la['host']}:{la['port']}/v1/chat/completions"
    payload = json.dumps({
        "messages": [{"role": "system", "content": system_prompt},
                     {"role": "user", "content": user_prompt}],
        "temperature": 0.3, "max_tokens": 700,
    }).encode()
    req = urllib.request.Request(base, data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"].strip()
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        raise RuntimeError(
            "Local AI engine is not reachable. Start it with ./start-hub.sh "
            "(it launches ai/llama.cpp/llama-server), then try again.")


def _cloud_answer(cfg, system_prompt, user_prompt):
    import os
    c = cfg["cloud"]
    key = os.environ.get(c["api_key_env"], "")
    if not key:
        raise RuntimeError(
            f"Cloud mode is selected but the API key is missing. "
            f"Set the environment variable {c['api_key_env']} and restart the hub.")
    base = c["base_url"].rstrip("/") + "/chat/completions"
    payload = json.dumps({
        "model": c["model"],
        "messages": [{"role": "system", "content": system_prompt},
                     {"role": "user", "content": user_prompt}],
        "temperature": 0.3, "max_tokens": 700,
    }).encode()
    req = urllib.request.Request(base, data=payload, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")[:300]
        raise RuntimeError(f"Cloud API error {e.code}: {body}")


GUARD = ("You are the office assistant for a community organization. "
         "Answer ONLY from the provided context documents. If the answer is not "
         "in them, say you do not know and suggest asking a board member. "
         "Be concise.")


def ask(question: str, cfg):
    """One entry point: retrieve, then answer with the configured backend."""
    t0 = time.time()
    hits = retrieve(question, cfg)
    context = ""
    if hits:
        context = "\n\n".join(f"[{i+1}] {p}\n{snip}" for i, (p, snip) in enumerate(hits))
    mode = cfg["privacy_mode"]

    if mode == "retrieval_only":
        if not hits:
            answer = ("No matching documents found. Drop text or PDF files into "
                      "the documents/ folder and run Reindex, then try again.")
        else:
            answer = "Here is what your documents say:\n\n" + context
        sources = [p for p, _ in hits]
        backend = "retrieval_only"

    elif mode == "local":
        if not hits:
            answer = _local_answer(cfg, GUARD, "No documents matched. Politely say "
                                                "you cannot find this in the club documents.\n\nQuestion: " + question)
        else:
            answer = _local_answer(cfg, GUARD, f"Context documents:\n\n{context}\n\nQuestion: {question}")
        sources = [p for p, _ in hits]
        backend = f"local ({cfg['model_label']})"

    elif mode == "cloud":
        if not hits:
            answer = _cloud_answer(cfg, GUARD, question)
            sources = []
        else:
            answer = _cloud_answer(cfg, GUARD, f"Context documents:\n\n{context}\n\nQuestion: {question}")
            sources = [p for p, _ in hits]
        backend = f"cloud ({cfg['cloud']['model']})"

    else:
        raise RuntimeError(f"Unknown privacy_mode: {mode}")

    return {"answer": answer, "sources": sources, "backend": backend,
            "took_s": round(time.time() - t0, 2)}
