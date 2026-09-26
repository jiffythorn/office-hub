#!/usr/bin/env python3
"""
doctor.py - check AND repair the Office Hub installation.

Runs the same checks as check_deps.py, then automatically fixes what it can:

  missing config.json        -> re-runs the installer (privacy is a human choice)
  missing .venv / packages   -> creates venv, pip-installs fastapi uvicorn pypdf
  missing llama-server       -> downloads it via gh (or tells you where to get it)
  missing Qwen model         -> re-downloads the configured GGUF
  PHP missing/broken         -> package-manager install (asks; needs sudo on Linux)
  MariaDB missing/not running-> package-manager install + service start (asks)
  apps/admidio missing       -> fetches the release tarball
  apps/flarum missing        -> composer create-project (or prints the steps)
  services down              -> restarts hub and/or apps via the start scripts

Usage:
  python3 doctor.py                    # check, repair (asks for sudo/big steps), restart
  python3 doctor.py --yes              # repair everything without asking
  python3 doctor.py --section hub      # only the AI hub
  python3 doctor.py --section apps     # only Admidio/Flarum stack
  python3 doctor.py --no-system        # never run package-manager/sudo commands
  python3 doctor.py --no-restart       # repair but leave services as they are
  python3 doctor.py --check-only       # same as check_deps.py
"""

import argparse
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

import check_deps
import install as installer
import setup_apps

ROOT = Path(__file__).resolve().parent
IS_WIN = platform.system() == "Windows"

done, skipped = [], []


def note(msg):
    print(f"  [doctor] {msg}")


def agree(prompt, args):
    if args.yes:
        return True
    try:
        return input(f"  [doctor] {prompt} [y/N] ").strip().lower().startswith("y")
    except EOFError:
        return False


# ----------------------------------------------------------------------------
# repair actions
# ----------------------------------------------------------------------------

def repair_config(args):
    note("config.json is missing - the privacy choice is a human decision, "
         "so launching the interactive installer.")
    cmd = [sys.executable, str(ROOT / "install.py")]
    if args.yes:
        cmd += ["--yes", "--skip-models"]
    subprocess.run(cmd)
    return check_deps.load_config()


def repair_venv_and_packages():
    installer.install_deps()


def repair_llama_server():
    installer.download_llamacpp()


def repair_model(cfg):
    model_rel = (cfg.get("local_ai") or {}).get("model_path", "")
    key = Path(model_rel).name
    entry = next((m for m in installer.MODEL_CATALOG if m["key"] == key), None)
    if entry:
        installer.download_model(entry)
    else:
        note(f"could not map model {model_rel!r} to a known download - place the "
             f".gguf manually at {ROOT / model_rel}")


PM_PACKAGES = {
    "apt-get": {"php": ["php-cli", "php-mysql", "php-mbstring", "php-gd",
                        "php-xml", "php-curl"],
                "mariadb": ["mariadb-server"]},
    "dnf": {"php": ["php", "php-mysqlnd", "php-mbstring", "php-gd",
                    "php-xml", "php-curl"],
            "mariadb": ["mariadb-server"]},
    "pacman": {"php": ["php", "php-intl"], "mariadb": ["mariadb"]},
}


def repair_system(what, args):
    """what: 'php' or 'mariadb' - installs via the OS package manager."""
    pm = check_deps.pkg_manager()
    pkgs = PM_PACKAGES.get(pm, {}).get(what)
    if not pkgs:
        note(f"cannot auto-install {what} here - see the fix line above.")
        skipped.append(what)
        return
    prefix = ["sudo"] if pm != "pacman" else []
    confirm = ("install PHP + extensions via %s? (needs sudo) " % pm if what == "php"
               else "install MariaDB via %s? (needs sudo) " % pm)
    if args.no_system or not agree(confirm, args):
        skipped.append(what)
        return
    note(f"running: {' '.join(prefix + [pm, 'install'] +
                             (['-y'] if pm != 'pacman' else ['--noconfirm']) + pkgs)}")
    subprocess.run(prefix + [pm, "install"] +
                   (["-y"] if pm != "pacman" else ["--noconfirm"]) + pkgs)


def repair_mariadb_running(args):
    """Installed but not answering: try to start the service."""
    if IS_WIN:
        note("start MariaDB from its portable folder or Services app, then re-run doctor.")
        skipped.append("mariadb-start")
        return
    if args.no_system or not agree("start the MariaDB service? (needs sudo) ", args):
        skipped.append("mariadb-start")
        return
    subprocess.run(["sudo", "systemctl", "start", "mariadb"])


def repair_agent(cfg):
    """(Re)install nanobot and regenerate its config, preserving Telegram token."""
    apy = ROOT / ".venv-agent" / ("Scripts/python.exe" if IS_WIN else "bin/python")
    if not apy.exists():
        import venv
        venv.EnvBuilder(with_pip=True).create(ROOT / ".venv-agent")
    subprocess.run([str(apy), "-m", "pip", "install", "--quiet", "nanobot-ai"])
    # preserve any existing chat-channel tokens (telegram/discord)
    keep = {}
    acfg = ROOT / "data" / "nanobot" / "config.json"
    if acfg.exists():
        try:
            old = json.loads(acfg.read_text())
            for n in ("telegram", "discord"):
                ch = old.get("channels", {}).get(n, {})
                if ch.get("token"):
                    keep[n] = {"enabled": ch.get("enabled", False), "token": ch["token"],
                               "allowFrom": ch.get("allowFrom", [])}
        except Exception:
            pass
    cfgd = installer.agent_config(cfg, "Club Assistant")
    for n, ch in keep.items():
        cfgd["channels"][n] = ch
    (ROOT / "data" / "nanobot").mkdir(parents=True, exist_ok=True)
    (ROOT / "data" / "nanobot" / "workspace").mkdir(exist_ok=True)
    (ROOT / "data" / "nanobot" / "config.json").write_text(json.dumps(cfgd, indent=2))
    try:
        from hub.agent_sync import sync_docs_to_agent
        sync_docs_to_agent(cfg)
    except Exception:
        pass
    note("agent reinstalled and config regenerated (chat tokens preserved)")


def repair_admidio():
    setup_apps.fetch_admidio()


def repair_flarum():
    setup_apps.fetch_flarum()


# ----------------------------------------------------------------------------
# backup safety: is a recent snapshot available, is the nightly schedule on?
# ----------------------------------------------------------------------------

FRESH_HOURS = 48          # a snapshot older than this counts as stale


def backup_health():
    """Returns (issues, repairs).

    issues:  [(name, detail, fix_text), ...] - empty when everything is fine
    repairs: {name: callable} - what the doctor can do about each issue
    """
    try:
        from hub import backup as hub_backup
    except Exception as e:
        return [("backup module", f"cannot load hub/backup.py ({e})",
                 "check the installation")], {}
    issues, repairs = [], {}

    last = hub_backup.last_snapshot()
    if not last:
        issues.append(("backups", "no snapshot has ever been taken",
                       "take the first snapshot now"))
        repairs["backups"] = lambda: hub_backup.snapshot(label="doctor")
    else:
        age_h = None
        try:
            t = time.mktime(time.strptime(last["when"], "%Y-%m-%d %H:%M:%S"))
            age_h = (time.time() - t) / 3600
        except Exception:
            pass
        if age_h is None or age_h > FRESH_HOURS:
            age_txt = ("unknown age" if age_h is None
                       else f"{age_h / 24:.1f} days ago")
            issues.append(("backups", f"last snapshot is stale ({age_txt}: "
                           f"{last['when']})",
                           "take a fresh snapshot"))
            repairs["backups"] = lambda: hub_backup.snapshot(label="doctor")

    sched = hub_backup.schedule_status()
    if not sched.get("installed"):
        issues.append(("nightly backup schedule",
                       sched.get("detail", "off"),
                       "schedule the nightly 02:00 snapshot"))
        repairs["nightly backup schedule"] = hub_backup.schedule_install
    return issues, repairs


def print_backup_health():
    """Used in CHECK and VERIFY phases. Returns (issues, repairs)."""
    issues, repairs = backup_health()
    if not issues:
        from hub import backup as hub_backup
        last = hub_backup.last_snapshot() or {}
        sched = hub_backup.schedule_status()
        print(f"  [ok]  backups - last snapshot {last.get('when', '?')} "
              f"({last.get('files', '?')} files), nightly schedule "
              f"{'ON' if sched.get('installed') else 'OFF'}")
        return issues, repairs
    for name, detail, fix in issues:
        print(f"  [warn] {name} - {detail}")
        print(f"          fix: {fix}")
    return issues, repairs


def repair_backups(issues, repairs, args):
    """Offer the fixes. Everything is local and safe, so --yes accepts."""
    for name, _detail, _fix in issues:
        action = repairs.get(name)
        if not action:
            continue
        prompt = (f"take a backup snapshot right now? "
                  if name == "backups"
                  else "turn on the nightly backup (daily at 02:00)? ")
        if agree(prompt, args):
            note(f"fixing: {name} ...")
            try:
                res = action()
                ok = True if res is None else bool(res.get("installed", True))
                if ok:
                    done.append(name)
                else:
                    note(f"could not fix {name}: {res.get('detail', 'unknown')}")
                    skipped.append(name)
            except Exception as e:
                note(f"repair failed: {e}")
                skipped.append(name)
        else:
            skipped.append(name)


# ----------------------------------------------------------------------------
# dispatch + restart
# ----------------------------------------------------------------------------

def run_repairs(sections, cfg, args):
    fails = [(n, d) for (lvl, n, d, _f) in check_deps.results if lvl == "FAIL"]
    warns = [n for (lvl, n, _d, _f) in check_deps.results if lvl == "WARN"]
    if not fails:
        note("no FAIL-level problems in " + "/".join(sorted(sections)) + " - nothing to repair.")
    for name, detail in fails:
        print(f"\n  -- repairing: {name} ({detail})")
        if name == "config.json":
            cfg = repair_config(args)
            done.append(name)
        elif name == ".venv" or name.startswith("python package"):
            repair_venv_and_packages()
            done.append(name)
        elif name == "llama-server":
            repair_llama_server()
            done.append(name)
        elif name == "Qwen model":
            repair_model(cfg)
            done.append(name)
        elif name == "PHP" or name == "PHP extensions":
            repair_system("php", args)
        elif name == "MariaDB":
            repair_system("mariadb", args)
        elif name == "Power Mode agent" or name == "agent config":
            repair_agent(cfg)
            done.append(name)
        elif name == "apps/admidio":
            repair_admidio()
            done.append(name)
        elif name == "apps/flarum":
            repair_flarum()
            done.append(name)
        else:
            note(f"no automatic repair for {name!r} - follow the fix above.")
            skipped.append(name)
    if "cloud API key" in " ".join(warns):
        note("cloud key cannot be repaired automatically: set the env var shown "
             "by check_deps (free key from openrouter.ai/keys or console.groq.com/keys).")
    if "composer" in warns:
        note("composer must be installed by a human: https://getcomposer.org/download/")
    return cfg


def restart_services(sections, args):
    if args.no_restart:
        return
    script = "start-hub.bat" if IS_WIN else "start-hub.sh"
    apps_script = "start-apps.bat" if IS_WIN else "start-apps.sh"
    runner = [] if IS_WIN else ["bash"]
    if sections == {"hub", "apps"}:
        note("bringing up the entire blueprint in order (MariaDB, apps, AI) ...")
        subprocess.run(runner + [str(ROOT / script)])
        note("AI assistant: http://localhost:8090 | portal: :8080 | forum: :8081")
    elif "hub" in sections:
        note("ensuring the hub is running (--hub-only; start script is idempotent) ...")
        subprocess.run(runner + [str(ROOT / script), "--hub-only"])
        note("hub: http://localhost:8090")
    elif "apps" in sections:
        note("ensuring Admidio/Flarum are running ...")
        subprocess.run(runner + [str(ROOT / apps_script)])
        note("apps: http://localhost:8080 (Admidio), http://localhost:8081 (Flarum)")


def main():
    ap = argparse.ArgumentParser(description="Office Hub doctor: check + repair + restart")
    ap.add_argument("--section", choices=["hub", "apps", "all"], default="all")
    ap.add_argument("--yes", action="store_true", help="repair without asking")
    ap.add_argument("--no-system", action="store_true",
                    help="never run package-manager/sudo repairs")
    ap.add_argument("--no-restart", action="store_true", help="do not start services")
    ap.add_argument("--check-only", action="store_true", help="just report (like check_deps)")
    args = ap.parse_args()

    sections = {"hub", "apps"} if args.section == "all" else {args.section}
    print("=" * 66)
    print("  OFFICE HUB DOCTOR - check, repair, restart")
    print("=" * 66)

    cfg = check_deps.run_checks(sections)

    print("\n-- CHECK ------------------------------------------------------")
    icons = {"OK": "[ok]  ", "WARN": "[warn]", "FAIL": "[FAIL]", "INFO": "[info]"}
    for level, name, detail, fix in check_deps.results:
        line = f"  {icons[level]} {name}" + (f" - {detail}" if detail else "")
        print(line)
        if fix:
            print(f"          fix: {fix}")

    if "hub" in sections:
        print("\n-- BACKUP SAFETY ----------------------------------------------")
        bk_issues, bk_repairs = print_backup_health()

    if args.check_only:
        sys.exit(1 if any(l == "FAIL" for l, *_ in check_deps.results) else 0)

    print("\n-- REPAIR -----------------------------------------------------")
    cfg = run_repairs(sections, cfg, args)
    if "hub" in sections and bk_issues:
        repair_backups(bk_issues, bk_repairs, args)

    print("\n-- VERIFY -----------------------------------------------------")
    check_deps.results.clear()
    check_deps.run_checks(sections)
    fails = 0
    for level, name, detail, fix in check_deps.results:
        line = f"  {icons[level]} {name}" + (f" - {detail}" if detail else "")
        print(line)
        if fix:
            print(f"          fix: {fix}")
        fails += level == "FAIL"
    if "hub" in sections:
        print("  -- backup safety:")
        bk_after, _r = print_backup_health()
        if bk_after and all(n in skipped for n, _d, _f in bk_after):
            note("backup warnings remain (declined) - run the doctor again "
                 "any time, or use /admin -> Backups.")

    print("\n-- SUMMARY ----------------------------------------------------")
    if done:
        print(f"  repairs completed : {len(done)} ({', '.join(done)})")
    if skipped:
        print(f"  still needs a human: {', '.join(sorted(set(skipped)))}")
    verdict = "HEALTHY" if fails == 0 else f"STILL BROKEN ({fails} problem(s))"
    print(f"  >> {('/'.join(sorted(sections)))}: {verdict}")

    if fails == 0:
        restart_services(sections, args)
    sys.exit(0 if fails == 0 else 1)


if __name__ == "__main__":
    main()
