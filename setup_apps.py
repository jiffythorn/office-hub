#!/usr/bin/env python3
"""
setup_apps.py - fetch and stage the member portal (Admidio) and forum (Flarum).

Uses the GitHub CLI (gh) when available, curl otherwise. No Docker.
  apps/admidio  - downloaded release tarball from github.com/Admidio/admidio
  apps/flarum   - composer create-project (preferred) or instructions

Also attempts to pre-create the MariaDB databases if MariaDB is running.

Usage:  python3 setup_apps.py            # fetch everything missing
        python3 setup_apps.py --admidio-only
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APPS = ROOT / "apps"
ADMIDIO_VER = "v5.0.15"  # pin for reproducible bundles


def sh(cmd, **kw):
    print("  $", " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, **kw)


def have(*names):
    return next((n for n in names if shutil.which(n)), None)


def fetch_admidio():
    dest = APPS / "admidio"
    if dest.exists():
        print("  Admidio already present: apps/admidio")
        return
    APPS.mkdir(parents=True, exist_ok=True)
    # recovery: a previous run may have extracted but failed to stage
    recovered = sorted((p for p in APPS.glob("admidio-*")
                        if p.is_dir() and p.resolve() != dest.resolve()),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    if recovered:
        recovered[0].rename(dest)
        print("  Recovered previous extraction: apps/admidio")
        return
    print(f"  Downloading Admidio {ADMIDIO_VER} source ...")
    if shutil.which("gh"):
        r = sh(["gh", "release", "download", ADMIDIO_VER, "--repo", "Admidio/admidio",
                "--archive", "tar.gz", "--clobber"], cwd=APPS)
        if r.returncode != 0:
            print("  gh failed, trying curl ...")
            sh(["curl", "-L", "-o", "admidio.tar.gz",
                f"https://github.com/Admidio/admidio/archive/refs/tags/{ADMIDIO_VER}.tar.gz"], cwd=APPS)
    else:
        sh(["curl", "-L", "-o", "admidio.tar.gz",
            f"https://github.com/Admidio/admidio/archive/refs/tags/{ADMIDIO_VER}.tar.gz"], cwd=APPS)
    tarball = next((APPS / n for n in ("admidio.tar.gz", f"{ADMIDIO_VER}.tar.gz",
                                        f"admidio-admidio-{ADMIDIO_VER}.tar.gz")
                     if (APPS / n).exists()), None)
    if tarball is None:  # gh names --archive downloads after the tag; catch-all:
        tarball = next((p for p in APPS.glob("*.tar.gz") if p.stat().st_size > 100_000), None)
    if tarball:
        sh(["tar", "-xzf", str(tarball), "-C", str(APPS)])
        # gh may name the tarball after the tag (admidio-5.0.15.tar.gz), so find a
        # freshly-extracted DIRECTORY that isn't the destination, newest first.
        dirs = sorted((p for p in APPS.glob("admidio-*")
                       if p.is_dir() and p.resolve() != dest.resolve()),
                      key=lambda p: p.stat().st_mtime, reverse=True)
        src = dirs[0] if dirs else None
        if src:
            src.rename(dest)
            print("  OK: apps/admidio")
        else:
            print("  WARNING: tarball extracted but no admidio directory found "
                  "- check apps/ manually")
        tarball.unlink()  # clean up whichever tarball we used
    else:
        print("  ERROR: could not fetch Admidio - download manually from "
              "https://github.com/Admidio/admidio/releases and unzip to apps/admidio")


def fetch_flarum():
    dest = APPS / "flarum"
    if dest.exists():
        print("  Flarum already present: apps/flarum")
        return
    APPS.mkdir(parents=True, exist_ok=True)
    composer = have("composer", "composer.phar")
    if composer:
        cmd = ["composer"] if composer == "composer" else ["php", composer]
        print("  Creating Flarum project via composer (needs network) ...")
        r = sh(cmd + ["create-project", "flarum/flarum", str(dest), "--no-interaction"])
        if r.returncode == 0:
            print("  OK: apps/flarum")
            return
    print("  composer not available (or failed). Manual step:")
    print("    1. Install composer:  https://getcomposer.org/download/")
    print("    2. Run:  composer create-project flarum/flarum apps/flarum")
    print("    (Flarum has no zip releases - composer is the official installer.)")


def precreate_databases():
    mysql = have("mariadb", "mysql")
    if not mysql:
        print("  MariaDB client not found - create DBs during each app's web installer.")
        return
    print("  Attempting to pre-create databases (asks for MariaDB root password) ...")
    sql = ("CREATE DATABASE IF NOT EXISTS admidio CHARACTER SET utf8mb4; "
           "CREATE DATABASE IF NOT EXISTS flarum CHARACTER SET utf8mb4; "
           "CREATE USER IF NOT EXISTS 'hub'@'localhost' IDENTIFIED BY 'CHANGE_ME_STRONG'; "
           "GRANT ALL PRIVILEGES ON admidio.* AND flarum.* TO 'hub'@'localhost'; FLUSH PRIVILEGES;")
    sh([mysql, "-u", "root", "-p", "-e", sql])
    print("  (If that failed: run the SQL in data/createdb.sql during app setup.)")
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "createdb.sql").write_text(sql + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--admidio-only", action="store_true")
    args = ap.parse_args()

    print("\n=== Staging web apps (no Docker, plain folders + PHP) ===")
    fetch_admidio()
    if not args.admidio_only:
        fetch_flarum()
    precreate_databases()

    print("""
=== How to run the web apps (office scale: PHP built-in server is enough) ===

  Linux/macOS:   ./start-apps.sh
  Windows:       start-apps.bat

  Then in a browser, one time only:
    http://localhost:8080  -> Admidio web installer  (choose MariaDB, db: admidio)
    http://localhost:8081  -> Flarum web installer   (choose MariaDB, db: flarum)
  Admin logins: run python3 secure_admin.py and use those passwords.
""")


if __name__ == "__main__":
    main()
