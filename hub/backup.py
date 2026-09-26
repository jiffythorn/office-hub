"""
hub/backup.py - self-contained backup for the Office Hub.

What is protected:
  - documents/            the club's actual knowledge
  - config.json           the whole system's configuration
  - data/                 index, admin hash, audit trail, bot config + memory,
                          pairing - everything EXCEPT logs, pidfiles and the
                          backups themselves
  - ADMIN_CREDENTIALS.txt if present (so a dead drive can't lock you out)
  - MariaDB dumps         Admidio members + Flarum forum databases, via
                          mysqldump (skipped with a clear warning if MariaDB
                          is down or credentials can't be found)

What is deliberately NOT backed up: apps/, ai/, .venv*/ - all re-downloadable,
which keeps snapshots small enough for a synced cloud folder.

Model: every snapshot is SELF-CONTAINED - all protected files, every time
(the whole set is only a few MB). Hash manifests let --verify prove integrity
and --restore skip files that are already identical. Snapshots are plain
files: portable to any USB drive or cloud folder, no special tools to read.
Retention: newest 20 snapshots, nothing older than 30 days.

Cloud: any folder a desktop sync app (OneDrive/Dropbox/Google Drive) watches,
and/or any rclone remote. Zero subscriptions, no vendor lock-in: snapshots
are plain files.

Restore:  python3 hub/backup.py --list
          python3 hub/backup.py --verify
          python3 hub/backup.py --restore snapshot-20260101-120000-auto
          python3 hub/backup.py --restore <name> --restore-databases  # + member DBs
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    from . import audit          # imported as hub.backup
except ImportError:              # run directly: python3 hub/backup.py
    import audit

ROOT = Path(__file__).resolve().parent.parent
BACKUP_ROOT = ROOT / "data" / "backups"
LATEST_MANIFEST = BACKUP_ROOT / "latest_manifest.json"
KEEP_SNAPSHOTS = 20
KEEP_DAYS = 30

DB_OVERRIDE_FILE = ROOT / "data" / "backup_db.json"

# data/ entries that never belong in a backup (the set is for readability;
# the membership test below is on individual path-part strings)
DATA_EXCLUDE_NAMES = {"backups"}
DATA_EXCLUDE_SUFFIXES = (".log", ".pid")


# ----------------------------------------------------------------- helpers

def _load_cfg() -> dict:
    try:
        return json.loads((ROOT / "config.json").read_text())
    except Exception:
        return {}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _rel_walk(base: Path) -> list[Path]:
    return [p for p in base.rglob("*") if p.is_file()]


def collect_files() -> list[Path]:
    """Absolute paths of every regular file that belongs in a backup."""
    files: list[Path] = []
    docs = ROOT / "documents"
    if docs.exists():
        files += _rel_walk(docs)
    if (ROOT / "config.json").exists():
        files.append(ROOT / "config.json")
    creds = ROOT / "ADMIN_CREDENTIALS.txt"
    if creds.exists():
        files.append(creds)
    data = ROOT / "data"
    if data.exists():
        files += [p for p in _rel_walk(data)
                  if p.suffix not in DATA_EXCLUDE_SUFFIXES
                  and not (set(p.relative_to(data).parts) & DATA_EXCLUDE_NAMES)]
    return files


# ------------------------------------------------------- database discovery

def _pairs_from_php(text: str) -> dict:
    """Tolerant scrape of PHP config files: 'key' => 'value' AND
    define('KEY', 'value') styles cover Admidio and Flarum configs."""
    import re
    pairs = {}
    for k, v in re.findall(r"['\"]([\w]+)['\"]\s*=>\s*['\"]([^'\"]*)['\"]", text):
        pairs[k.lower()] = v
    for k, v in re.findall(
            r"define\(\s*['\"](\w+)['\"]\s*,\s*['\"]([^'\"]*)['\"]", text):
        pairs[k.lower()] = v
    return pairs


def _pick(pairs: dict, *keys) -> str:
    for k in keys:
        if pairs.get(k):
            return pairs[k]
    return ""


def database_credentials() -> list[dict]:
    """Best-effort credentials for the member databases.

    Priority: explicit data/backup_db.json override, then scraping each
    app's config.php. The officer can always write data/backup_db.json:
      {"databases": [{"name": "admidio", "user": "root", "password": "..."}]}
    """
    creds: list[dict] = []
    if DB_OVERRIDE_FILE.exists():
        try:
            for d in json.loads(DB_OVERRIDE_FILE.read_text()).get("databases", []):
                if d.get("name"):
                    creds.append({"name": d["name"],
                                  "user": d.get("user", "root"),
                                  "password": d.get("password", ""),
                                  "host": d.get("host", "127.0.0.1")})
            if creds:
                return creds
        except Exception:
            pass
    for app in ("admidio", "flarum"):
        cfg_php = ROOT / "apps" / app / "config.php"
        entry = {"name": app, "user": "", "password": "", "host": ""}
        if cfg_php.exists():
            p = _pairs_from_php(cfg_php.read_text(errors="replace"))
            entry["host"] = _pick(p, "host", "hostname", "dbhost")
            entry["user"] = _pick(p, "user", "username", "dbuser", "login")
            entry["password"] = _pick(p, "password", "pass", "passwd", "pwd",
                                      "dbpassword")        # Never trust a scraped 'database' key that may be something else
        # entirely - the folder-based default name is the safe choice.
        entry["host"] = entry["host"] or "127.0.0.1"
        creds.append(entry)
    return creds


def _mysql_tool(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    if sys.platform == "win32":  # common Windows MariaDB/XAMPP locations
        import glob as _glob
        for pattern in ("C:/Program Files/MariaDB */bin",
                        "C:/xampp/mysql/bin"):
            for d in _glob.glob(pattern):
                p = Path(d) / f"{name}.exe"
                if p.exists():
                    return str(p)
    return None


def dump_databases(dest: Path) -> list[dict]:
    """mysqldump each member database into dest/<db>.sql. Never raises."""
    results = []
    mysqldump = _mysql_tool("mysqldump")
    if not mysqldump:
        return [{"db": "ALL", "ok": False,
                 "why": "mysqldump not found (MariaDB client tools)"}]
    import socket as _s
    maria_up = False
    try:
        s = _s.socket()
        s.settimeout(0.8)
        maria_up = s.connect_ex(("127.0.0.1", 3306)) == 0
        s.close()
    except OSError:
        pass
    if not maria_up:
        return [{"db": "ALL", "ok": False,
                 "why": "MariaDB not running on :3306 - start it and re-run"}]
    dest.mkdir(exist_ok=True)
    for cred in database_credentials():
        env = dict(os.environ)
        if cred["password"]:
            env["MYSQL_PWD"] = cred["password"]
        out = dest / f"{cred['name']}.sql"
        cmd = [mysqldump, "--single-transaction", "--routines",
               "--host", cred["host"] or "127.0.0.1",
               "-u", cred["user"] or "root", cred["name"]]
        try:
            r = subprocess.run(cmd, env=env, capture_output=True, timeout=300)
            if r.returncode == 0:
                out.write_bytes(r.stdout)
                results.append({"db": cred["name"], "ok": True,
                                "bytes": out.stat().st_size})
            else:
                results.append({"db": cred["name"], "ok": False,
                                "why": r.stderr.decode(errors="replace")
                                       .strip().splitlines()[-1][:120]
                                if r.stderr else "mysqldump failed"})
        except Exception as e:
            results.append({"db": cred["name"], "ok": False, "why": str(e)[:120]})
    return results


# ---------------------------------------------------------------- snapshots

def _snapshot_names() -> list[str]:
    return sorted(s.name for s in BACKUP_ROOT.glob("snapshot-*"))


def _physical_copy(target: str, rel: str) -> Path | None:
    """Locate a file's physical copy for snapshot `target`.

    Incremental snapshots only store files that CHANGED since the previous
    one, so a file listed in `target`'s manifest may physically live in an
    older snapshot. Walk the chain backward from the target; the first
    physical copy found is the right one (newer snapshots would only hold it
    again if it changed again, which the manifest hash would betray).
    """
    names = _snapshot_names()
    if target not in names:
        return None
    for name in reversed(names[:names.index(target) + 1]):
        f = BACKUP_ROOT / name / "files" / rel
        if f.exists():
            return f
    return None


def snapshot(label: str = "auto", quiet: bool = False) -> dict:
    """Create one incremental snapshot. Returns a summary dict."""
    cfg = _load_cfg()
    bcfg = cfg.get("backup", {})
    stamp = time.strftime("%Y%m%d-%H%M%S")
    snap = BACKUP_ROOT / f"snapshot-{stamp}-{label}"
    (snap / "files").mkdir(parents=True, exist_ok=True)

    # Every snapshot is self-contained: all files, every time. The whole hub's
    # precious data is only a few MB, so dedup would save pennies while making
    # restores and verification fragile - self-contained wins.
    manifest = {"created": time.strftime("%Y-%m-%d %H:%M:%S"),
                "label": label, "files": {}, "databases": []}
    copied = 0
    total_bytes = 0
    for path in collect_files():
        rel = path.relative_to(ROOT).as_posix()
        digest = _sha256(path)
        size = path.stat().st_size
        total_bytes += size
        target = snap / "files" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied += 1
        manifest["files"][rel] = {"sha256": digest, "size": size,
                                  "mtime": path.stat().st_mtime}

    manifest["databases"] = dump_databases(snap / "databases")
    (snap / "manifest.json").write_text(json.dumps(manifest, indent=1))
    LATEST_MANIFEST.write_text(json.dumps(manifest, indent=1))
    _apply_retention()
    summary = {"snapshot": snap.name, "files": len(manifest["files"]),
               "mb": round(total_bytes / 1e6, 1), "databases": manifest["databases"]}
    if not quiet:
        print(f"[backup] snapshot {snap.name}: {summary['files']} files, "
              f"{summary['mb']} MB (self-contained)")
        for d in manifest["databases"]:
            print(f"[backup]   database {d['db']}: "
                  f"{'dumped ' + str(d.get('bytes', 0)) + ' bytes' if d['ok'] else 'SKIPPED - ' + d.get('why', '')}")
        if bcfg.get("cloud_dir") or bcfg.get("rclone_remote"):
            print("[backup] syncing to cloud ...")
    audit.record("backup.snapshot", actor="system", label=label,
                 files=summary["files"])
    _cloud_sync(snap, bcfg, quiet)
    return summary


def _cloud_sync(snap: Path, bcfg: dict, quiet: bool):
    """Copy recent snapshots to a synced folder and/or an rclone remote."""
    keep = int(bcfg.get("keep_in_cloud", 3))
    snaps = sorted(BACKUP_ROOT.glob("snapshot-*"))
    if bcfg.get("cloud_dir"):
        try:
            dest = Path(bcfg["cloud_dir"]).expanduser() / "office-hub-backups"
            dest.mkdir(parents=True, exist_ok=True)
            for s in snaps[-keep:]:
                if s.name in {p.name for p in dest.iterdir()}:
                    continue
                shutil.copytree(s, dest / s.name, dirs_exist_ok=True)
            if not quiet:
                print(f"[backup]   folder sync: {dest}")
        except Exception as e:
            if not quiet:
                print(f"[backup]   folder sync FAILED: {e}")
    if bcfg.get("rclone_remote") and shutil.which("rclone"):
        try:
            subprocess.run(["rclone", "copy", str(BACKUP_ROOT),
                            f"{bcfg['rclone_remote']}:office-hub-backups",
                            "--transfers", "2", "--quiet"], timeout=1800)
            if not quiet:
                print(f"[backup]   rclone: {bcfg['rclone_remote']}")
        except Exception as e:
            if not quiet:
                print(f"[backup]   rclone FAILED: {e}")


def _apply_retention():
    snaps = [s for s in BACKUP_ROOT.glob("snapshot-*") if s.is_dir()]
    if not snaps:
        return
    # Order by actual age (mtime), never by name: labels differ, and a name
    # sort would happily delete today's snapshot to keep one called "old".
    snaps.sort(key=lambda s: s.stat().st_mtime)
    cutoff = time.time() - KEEP_DAYS * 86400
    victims = []
    if len(snaps) > KEEP_SNAPSHOTS:
        victims += snaps[:-KEEP_SNAPSHOTS]
    victims += [s for s in snaps if s.stat().st_mtime < cutoff]
    victims = [v for v in set(victims) if v.exists()]
    if not victims:
        return
    newest = snaps[-1]
    newest_manifest = newest / "manifest.json"
    if not newest_manifest.exists():
        return
    try:
        nm = json.loads(newest_manifest.read_text())
        nm_files = set(nm.get("files", {}))
    except Exception:
        return
    # Before deleting old snapshots, carry forward any file they physically
    # store that the newest snapshot's chain still needs.
    for victim in victims:
        if victim == newest:
            continue
        try:
            vm = json.loads((victim / "manifest.json").read_text())
        except Exception:
            continue
        for rel in vm.get("files", {}):
            if rel in nm_files and not (newest / "files" / rel).exists():
                src = victim / "files" / rel
                if src.exists():
                    dst = newest / "files" / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
    for s in victims:
        shutil.rmtree(s, ignore_errors=True)


def last_snapshot() -> dict | None:
    snaps = sorted(BACKUP_ROOT.glob("snapshot-*"))
    if not snaps:
        return None
    s = snaps[-1]
    try:
        m = json.loads((s / "manifest.json").read_text())
        size = sum(f["size"] for f in m["files"].values())
        dbs = "; ".join(d["db"] for d in m.get("databases", []) if d["ok"])
        return {"name": s.name, "when": m["created"], "files": len(m["files"]),
                "mb": round(size / 1e6, 1), "databases": dbs or "none"}
    except Exception:
        return {"name": s.name, "when": "?", "files": "?", "mb": "?",
                "databases": "?"}


def list_snapshots() -> list[dict]:
    out = []
    for s in sorted(BACKUP_ROOT.glob("snapshot-*")):
        try:
            m = json.loads((s / "manifest.json").read_text())
            out.append({"name": s.name, "when": m["created"],
                        "label": m.get("label", ""),
                        "files": len(m["files"]),
                        "dbs_ok": sum(1 for d in m.get("databases", []) if d["ok"])})
        except Exception:
            out.append({"name": s.name, "when": "?", "label": "?",
                        "files": "?", "dbs_ok": 0})
    return out


def verify(name: str | None = None) -> tuple[int, list[str]]:
    """Re-hash snapshot contents against their manifests, following the
    incremental chain. Returns (checked, bad)."""
    targets = ([name] if name else _snapshot_names())
    checked, bad = 0, []
    for target in targets:
        mfile = BACKUP_ROOT / target / "manifest.json"
        if not mfile.exists():
            continue
        m = json.loads(mfile.read_text())
        for rel, meta in m["files"].items():
            checked += 1
            src = _physical_copy(target, rel)
            if src is None or _sha256(src) != meta["sha256"]:
                bad.append(f"{target}/{rel}")
    return checked, bad


def restore(name: str, databases: bool = False) -> tuple[int, list[str]]:
    """Copy a snapshot's files back over the live installation (following the
    incremental chain for files stored in older snapshots)."""
    snap = BACKUP_ROOT / name
    mfile = snap / "manifest.json"
    if not mfile.exists():
        raise SystemExit(f"No snapshot named '{name}'. Use --list to see them.")
    m = json.loads(mfile.read_text())
    restored, skipped = [], []
    for rel, meta in m["files"].items():
        dst = ROOT / rel
        if dst.exists() and _sha256(dst) == meta["sha256"]:
            skipped.append(rel)
            continue
        src = _physical_copy(name, rel)
        if src is None:
            skipped.append(rel + " (no copy in chain)")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        restored.append(rel)
    dbmsg = ""
    if databases:
        mysql = _mysql_tool("mysql")
        by_name = {c["name"]: c for c in database_credentials()}
        for d in m.get("databases", []):
            dump = snap / "databases" / f"{d['db']}.sql"
            if not (mysql and dump.exists()):
                continue
            cred = by_name.get(d["db"], {})
            env = dict(os.environ)
            if cred.get("password"):
                env["MYSQL_PWD"] = cred["password"]
            r = subprocess.run([mysql, "-u", cred.get("user") or "root",
                                d["db"]], env=env,
                               stdin=dump.open("rb"), capture_output=True)
            dbmsg += f" {d['db']}:{'ok' if r.returncode == 0 else 'FAILED'}"
    audit.record("backup.restore", actor="admin", snapshot=name,
                 restored=len(restored), databases=dbmsg or "no")
    return len(restored), dbmsg


# ------------------------------------------------------------- scheduling

# Nightly at 2 AM: the office PC is normally on and idle, and the boot
# auto-backup still catches up if the machine was off at 2 AM.
CRON_MARK_BEGIN = "# OFFICE-HUB-BACKUP BEGIN"
CRON_MARK_END = "# OFFICE-HUB-BACKUP END"


def _cron_block() -> str:
    py = shutil.which("python3") or shutil.which("python") or "python3"
    venv_py = ROOT / ".venv" / "bin" / "python"
    if venv_py.exists():
        py = str(venv_py)
    # Quote both paths: install dirs with spaces ("C:\Program Files\...",
    # "/home/jeff/Project Web/...") would otherwise split in the cron shell.
    return (f"{CRON_MARK_BEGIN}\n"
            f"0 2 * * * cd '{ROOT}' && '{py}' hub/backup.py --label nightly "
            f">> data/backup-cron.log 2>&1\n"
            f"{CRON_MARK_END}\n")


def schedule_status() -> dict:
    """Is the nightly backup scheduled on this OS? Never raises."""
    try:
        if sys.platform == "win32":
            r = subprocess.run(["schtasks", "/Query", "/TN", "OfficeHubBackup"],
                               capture_output=True)
            return {"installed": r.returncode == 0,
                    "detail": "Windows Task Scheduler, daily at 02:00"
                              if r.returncode == 0 else "not scheduled"}
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        installed = CRON_MARK_BEGIN in (r.stdout or "")
        return {"installed": installed,
                "detail": "cron, daily at 02:00" if installed else "not scheduled"}
    except Exception as e:
        return {"installed": False, "detail": f"unknown ({e})"}


def schedule_install() -> dict:
    """Schedule the nightly backup (cron on Linux/macOS, Task Scheduler on
    Windows). Idempotent: installing twice replaces the old entry."""
    audit_ok = True
    try:
        if sys.platform == "win32":
            py = ROOT / ".venv" / "Scripts" / "python.exe"
            if not py.exists():
                import shutil as _s
                py = Path(_s.which("python") or "python")
            cmd = (f'"{py}" "{ROOT / "hub" / "backup.py"}" --label nightly')
            r = subprocess.run(["schtasks", "/Create", "/TN", "OfficeHubBackup",
                                "/SC", "DAILY", "/ST", "02:00", "/TR", cmd,
                                "/F"], capture_output=True, text=True)
            ok = r.returncode == 0
            detail = (r.stdout or r.stderr or "").strip().splitlines()[-1][:120] \
                if (r.stdout or r.stderr) else ""
        else:
            r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
            lines = (r.stdout or "").splitlines()
            kept = []
            skipping = False
            for ln in lines:
                if ln.strip() == CRON_MARK_BEGIN:
                    skipping = True
                    continue
                if ln.strip() == CRON_MARK_END:
                    skipping = False
                    continue
                if not skipping:
                    kept.append(ln)
            new_crontab = "\n".join(kept).strip()
            new_crontab += "\n\n" + _cron_block()
            w = subprocess.run(["crontab", "-"], input=new_crontab,
                               text=True, capture_output=True)
            ok = w.returncode == 0
            detail = "cron entry installed (daily 02:00)" if ok else \
                (w.stderr or "crontab write failed")[:120]
        try:
            from . import audit
        except ImportError:
            import audit
        audit.record("backup.schedule", actor="system",
                     outcome="ok" if ok else "failed", detail=detail)
        return {"installed": ok, "detail": detail or ("scheduled" if ok else "failed")}
    except Exception as e:
        return {"installed": False, "detail": str(e)[:140]}


def schedule_remove() -> dict:
    try:
        if sys.platform == "win32":
            r = subprocess.run(["schtasks", "/Delete", "/TN", "OfficeHubBackup",
                                "/F"], capture_output=True, text=True)
            ok = r.returncode == 0
            detail = "scheduled task removed" if ok else "was not scheduled"
        else:
            r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
            lines = (r.stdout or "").splitlines()
            kept, skipping = [], False
            for ln in lines:
                if ln.strip() == CRON_MARK_BEGIN:
                    skipping = True
                    continue
                if ln.strip() == CRON_MARK_END:
                    skipping = False
                    continue
                if not skipping:
                    kept.append(ln)
            w = subprocess.run(["crontab", "-"], input="\n".join(kept) + "\n",
                               text=True, capture_output=True)
            ok = w.returncode == 0
            detail = "cron entry removed" if ok else "crontab write failed"
        try:
            from . import audit
        except ImportError:
            import audit
        audit.record("backup.schedule", actor="system", outcome="removed")
        return {"installed": False, "detail": detail}
    except Exception as e:
        return {"installed": schedule_status()["installed"],
                "detail": str(e)[:140]}


# --------------------------------------------------------------------- CLI

def main() -> int:
    ap = argparse.ArgumentParser(description="Office Hub backup")
    ap.add_argument("--auto", action="store_true",
                    help="run at boot; skip if a snapshot exists from the last 6h")
    ap.add_argument("--label", default="auto")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--restore", metavar="SNAPSHOT")
    ap.add_argument("--restore-databases", action="store_true")
    ap.add_argument("--schedule", action="store_true",
                    help="schedule the nightly backup (cron / Task Scheduler)")
    ap.add_argument("--unschedule", action="store_true",
                    help="remove the nightly backup schedule")
    ap.add_argument("--schedule-status", action="store_true")
    args = ap.parse_args()

    if args.schedule:
        res = schedule_install()
        print(f"[schedule] {'installed' if res['installed'] else 'FAILED'}: "
              f"{res['detail']}")
        return 0 if res["installed"] else 1
    if args.unschedule:
        res = schedule_remove()
        print(f"[schedule] {res['detail']}")
        return 0
    if args.schedule_status:
        res = schedule_status()
        print(f"[schedule] {'ON' if res['installed'] else 'OFF'} - {res['detail']}")
        return 0

    if args.list:
        snaps = list_snapshots()
        if not snaps:
            print("No backups yet. Run: python3 hub/backup.py")
        for s in snaps:
            print(f"  {s['name']}  {s['when']}  {s['files']} files  "
                  f"dbs ok: {s['dbs_ok']}")
        return 0
    if args.verify:
        checked, bad = verify()
        print(f"[verify] {checked} files checked, {len(bad)} bad")
        for b in bad[:20]:
            print(f"  CORRUPT: {b}")
        return 1 if bad else 0
    if args.restore:
        n, dbmsg = restore(args.restore, args.restore_databases)
        print(f"[restore] {n} file(s) restored from {args.restore}{dbmsg}")
        return 0
    if args.auto:
        last = last_snapshot()
        if last:
            try:
                import datetime
                t = time.mktime(time.strptime(last["when"], "%Y-%m-%d %H:%M:%S"))
                if time.time() - t < 6 * 3600:
                    print(f"[backup] last snapshot is fresh ({last['when']}) - "
                          "nothing to do")
                    return 0
            except Exception:
                pass
        snapshot(label="boot", quiet=False)
        return 0
    snapshot(label=args.label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
