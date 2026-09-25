"""
hub/agent_sync.py - keep the Power Mode agent's copy of documents/ fresh.

The member bot runs with restrictToWorkspace=true (it can only touch its own
workspace), so the hub syncs a one-way copy of documents/ into
data/nanobot/workspace/documents/ whenever the real documents change.
Officers who need direct access disable the restriction on the officer bot
(see POWER_MODE_GUIDE.md section 5) - that one reads documents/ live.
"""

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def sync_docs_to_agent(cfg, verbose=False) -> int:
    """Mirror documents/ into the agent workspace. Returns files copied."""
    if not cfg.get("power_mode"):
        return 0
    src = ROOT / cfg["index"]["documents_dir"]
    ws = ROOT / "data" / "nanobot" / "workspace" / "documents"
    if not src.exists():
        return 0
    ws.mkdir(parents=True, exist_ok=True)
    copied = 0
    allowed = {".txt", ".md", ".csv", ".pdf", ".html", ".htm", ".log", ".rtf"}
    for f in src.rglob("*"):
        if not f.is_file() or f.name.startswith((".", "~")):
            continue
        if f.suffix.lower() not in allowed:
            continue
        dest = ws / f.relative_to(src)
        if not dest.exists() or dest.stat().st_mtime != f.stat().st_mtime \
                or dest.stat().st_size != f.stat().st_size:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
            copied += 1
            if verbose:
                print(f"  [agent-sync] {f.relative_to(src)}")
    # drop files that vanished from documents/
    for f in ws.rglob("*"):
        if f.is_file():
            rel = f.relative_to(ws)
            if not (src / rel).exists():
                f.unlink()
                if verbose:
                    print(f"  [agent-sync] removed {rel}")
    return copied


if __name__ == "__main__":
    cfg = json.loads((ROOT / "config.json").read_text())
    n = sync_docs_to_agent(cfg, verbose=True)
    print(f"[agent-sync] {n} file(s) synced to the agent workspace")
