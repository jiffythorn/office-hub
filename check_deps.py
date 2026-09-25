#!/usr/bin/env python3
"""
check_deps.py - the single source of truth for "can this actually run?"

Sections:
  hub   - Python version, .venv + pip packages, config.json, AI stack for
          the configured privacy mode (local: llama-server + model,
          cloud: API key env var, retrieval_only: nothing extra)
  apps  - PHP 8.1+ with required extensions, composer, MariaDB,
          apps/admidio + apps/flarum folders

Usage:
  python3 check_deps.py                 # check everything
  python3 check_deps.py --section hub   # only what start-hub needs
  python3 check_deps.py --section apps  # only what start-apps needs
  python3 check_deps.py --quiet         # print only problems

Exit code: 1 if any FAIL in the requested sections (so scripts can abort).
"""

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IS_WIN = platform.system() == "Windows"

results = []  # (level, name, detail, fix)  level in OK/WARN/FAIL


def add(level, name, detail="", fix=""):
    results.append((level, name, detail, fix))


def load_config():
    p = ROOT / "config.json"
    if not p.exists():
        add("FAIL", "config.json", "missing", "Run: python3 install.py")
        return None
    try:
        return json.loads(p.read_text())
    except Exception as e:
        add("FAIL", "config.json", f"unreadable: {e}", "Re-run install.py")
        return None


# ---------------------------------------------------------------- hub section

def check_python():
    v = sys.version_info
    if (v.major, v.minor) >= (3, 9):
        add("OK", f"Python {v.major}.{v.minor}")
    else:
        add("FAIL", "Python", f"{v.major}.{v.minor} found, 3.9+ required",
            "Install Python 3.10+ (python.org / apt install python3)")


def check_venv():
    py = ROOT / ".venv" / ("Scripts/python.exe" if IS_WIN else "bin/python")
    if not py.exists():
        add("FAIL", ".venv", "virtual environment missing", "Run: python3 install.py")
        return None
    for mod in ("fastapi", "uvicorn", "pypdf", "multipart"):
        r = subprocess.run([str(py), "-c", f"import {mod}"], capture_output=True)
        if r.returncode == 0:
            add("OK", f"python package: {mod}")
        else:
            add("FAIL", f"python package: {mod}", "not importable",
                f"{py.relative_to(ROOT)} -m pip install {mod}")
    return py


def check_local_ai(cfg):
    la = cfg.get("local_ai", {})
    bin_rel = la.get("llamacpp_path", "")
    model_rel = la.get("model_path", "")
    bin_path = ROOT / bin_rel
    model_path = ROOT / model_rel
    if bin_rel and bin_path.exists():
        add("OK", f"llama-server ({bin_rel})")
    else:
        add("FAIL", "llama-server", f"not found at {bin_rel}",
            "Re-run: python3 install.py  (downloads it via gh), or place it manually")
    if model_rel and model_path.exists() and model_path.stat().st_size > 1_000_000:
        add("OK", f"model ({model_rel}, {model_path.stat().st_size // (1024*1024)} MB)")
    else:
        add("FAIL", "Qwen model", f"not found at {model_rel}",
            "Re-run: python3 install.py  (downloads it), or place the .gguf manually")
    if bin_path.exists() and model_path.exists():
        add("INFO", "start with ./start-hub.sh", "llama.cpp + glue API")


def check_cloud(cfg):
    env_name = cfg.get("cloud", {}).get("api_key_env", "")
    if os.environ.get(env_name):
        add("OK", f"cloud API key ({env_name} is set)")
    else:
        add("WARN", f"cloud API key ({env_name})", "environment variable is empty now",
            f"export {env_name}=<your free key>  before starting the hub")


def check_agent(cfg):
    """Power Mode (optional nanobot agent layer)."""
    if not cfg.get("power_mode"):
        return
    apy = ROOT / ".venv-agent" / ("Scripts/python.exe" if IS_WIN else "bin/nanobot")
    nanobot_bin = ROOT / ".venv-agent" / ("Scripts/nanobot.exe" if IS_WIN else "bin/nanobot")
    if nanobot_bin.exists():
        add("OK", "Power Mode agent (nanobot)")
    else:
        add("FAIL", "Power Mode agent", "nanobot not installed in .venv-agent",
            "python3 install.py --with-agent  (or: python3 doctor.py)")
    acfg = ROOT / "data" / "nanobot" / "config.json"
    if acfg.exists():
        add("OK", "agent config (data/nanobot/config.json)")
        try:
            c = json.loads(acfg.read_text())
            chans = c.get("channels", {})
            live = [n for n in ("telegram", "discord")
                    if chans.get(n, {}).get("enabled") and chans.get(n, {}).get("token")]
            if live:
                add("OK", f"chat channel(s) live: {', '.join(live)}")
            else:
                add("INFO", "chat channels: off",
                    "add a Telegram (@BotFather) or Discord token in "
                    "data/nanobot/config.json to let members chat from phones")
        except Exception:
            pass
    else:
        add("FAIL", "agent config", "data/nanobot/config.json missing", "python3 doctor.py")


def check_admin_console():
    hash_file = ROOT / "data" / "admin_hash.txt"
    if hash_file.exists():
        add("OK", "admin console password (set)")
    else:
        add("INFO", "admin console", "no password yet",
            "open http://localhost:8090/admin once to set it (or run python3 secure_admin.py)")


def check_hub(cfg):
    check_python()
    check_venv()
    check_admin_console()
    if cfg is None:
        return
    mode = cfg.get("privacy_mode", "")
    if mode == "local":
        check_local_ai(cfg)
    elif mode == "cloud":
        check_cloud(cfg)
    elif mode == "retrieval_only":
        add("OK", "retrieval-only mode: no extra AI dependencies")
    else:
        add("FAIL", "privacy_mode", f"unknown value {mode!r}", "Re-run install.py")
    check_agent(cfg)


# --------------------------------------------------------------- apps section

PHP_EXT_REQUIRED = ["pdo_mysql", "mbstring", "gd", "dom", "openssl", "curl"]
EXT_APT_FIX = "sudo apt install php php-mysql php-mbstring php-gd php-xml php-curl"
EXT_DNF_FIX = "sudo dnf install php php-mysqlnd php-mbstring php-gd php-xml php-curl"
EXT_PACMAN_FIX = "sudo pacman -S php php-intl  # then enable extensions in php.ini"


def pkg_manager():
    for pm in ("apt-get", "dnf", "pacman", "zypper"):
        if shutil.which(pm):
            return pm
    return None


def php_ext_fix():
    pm = pkg_manager()
    return {"apt-get": EXT_APT_FIX, "dnf": EXT_DNF_FIX,
            "pacman": EXT_PACMAN_FIX}.get(pm, "Enable the extensions in php.ini")


def check_php():
    php = shutil.which("php")
    if not php:
        pm = pkg_manager()
        fix = {"apt-get": "sudo apt install php-cli php-mysql",
               "dnf": "sudo dnf install php",
               "pacman": "sudo pacman -S php"}.get(pm, "Install PHP 8.1+ from windows.php.net")
        add("FAIL", "PHP", "not installed (needed by Admidio + Flarum)", fix)
        return
    try:
        ver = subprocess.check_output(["php", "-v"], text=True, stderr=subprocess.DEVNULL)
        m = re.search(r"PHP (\d+)\.(\d+)", ver)
        major, minor = int(m.group(1)), int(m.group(2))
        if (major, minor) >= (8, 1):
            add("OK", f"PHP {major}.{minor}")
        else:
            add("FAIL", "PHP", f"{major}.{minor} found, Admidio/Flarum need 8.1+", php_ext_fix())
    except Exception:
        add("WARN", "PHP", "found but version check failed")
    mods = set()
    try:
        mods = set(subprocess.check_output(["php", "-m"], text=True,
                                           stderr=subprocess.DEVNULL).lower().split())
    except Exception:
        pass
    missing = [e for e in PHP_EXT_REQUIRED if e not in mods]
    if mods and missing:
        add("FAIL", "PHP extensions", f"missing: {', '.join(missing)}", php_ext_fix())
    elif mods:
        add("OK", f"PHP extensions ({len(PHP_EXT_REQUIRED)}/{len(PHP_EXT_REQUIRED)} required)")


def check_composer():
    if shutil.which("composer") or shutil.which("composer.phar") or \
            (ROOT / "composer.phar").exists():
        add("OK", "composer")
    else:
        add("WARN", "composer", "needed to install Flarum (no zip releases exist)",
            "https://getcomposer.org/download/")


def check_mariadb():
    cli = shutil.which("mariadb") or shutil.which("mysql")
    server = shutil.which("mariadbd") or shutil.which("mysqld")
    if not (cli or server):
        pm = pkg_manager()
        fix = {"apt-get": "sudo apt install mariadb-server",
               "dnf": "sudo dnf install mariadb-server",
               "pacman": "sudo pacman -S mariadb"}.get(
            pm, "Install MariaDB from mariadb.org (Windows: portable zip)")
        add("FAIL", "MariaDB", "not installed (Admidio + Flarum databases)", fix)
        return
    add("OK", "MariaDB installed")
    # is the server actually reachable?
    if cli:
        r = subprocess.run([cli, "-u", "root", "-e", "SELECT 1"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            add("OK", "MariaDB is running (root/no-password socket auth)")
        else:
            add("WARN", "MariaDB is not answering", r.stderr.strip().splitlines()[0][:80]
                if r.stderr else "connection failed",
                "sudo systemctl start mariadb   (or the portable-windows equivalent)")


def check_app_dirs():
    for name in ("admidio", "flarum"):
        d = ROOT / "apps" / name
        if d.exists() and any(d.iterdir()):
            add("OK", f"apps/{name} staged")
        else:
            add("FAIL", f"apps/{name}", "not downloaded yet",
                "python3 setup_apps.py" + ("  (flarum needs composer)" if name == "flarum" else ""))


def check_apps(cfg=None):
    check_php()
    check_composer()
    check_mariadb()
    check_app_dirs()


# ---------------------------------------------------------------------- main

def run_checks(sections):
    cfg = load_config() if sections & {"hub", "all"} else None
    if "hub" in sections:
        check_hub(cfg)
    if "apps" in sections:
        check_apps(cfg)
    return cfg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--section", choices=["hub", "apps", "all"], default="all")
    ap.add_argument("--quiet", action="store_true", help="only print problems")
    args = ap.parse_args()

    sections = {"hub", "apps"} if args.section == "all" else {args.section}
    run_checks(sections)

    icons = {"OK": "[ok]  ", "WARN": "[warn]", "FAIL": "[FAIL]",
             "INFO": "[info]"}
    failed = 0
    for level, name, detail, fix in results:
        if args.quiet and level == "OK":
            continue
        line = f"  {icons[level]} {name}"
        if detail:
            line += f" - {detail}"
        print(line)
        if fix:
            print(f"          fix: {fix}")
        if level == "FAIL":
            failed += 1

    label = " / ".join(sorted(sections))
    if failed:
        print(f"\n  >> {label}: NOT READY ({failed} problem(s) above).")
        sys.exit(1)
    print(f"\n  >> {label}: READY.")
    sys.exit(0)


if __name__ == "__main__":
    main()
