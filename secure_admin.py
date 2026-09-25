#!/usr/bin/env python3
"""
secure_admin.py - create secure admin credentials for the Office Hub.

Generates a strong random passphrase per service (hub API, Flarum, Admidio),
shows it once, and saves it to ADMIN_CREDENTIALS.txt (chmod 600 on Linux).
Also prints a hardening checklist for an office machine.

Usage:  python3 secure_admin.py            # generate everything fresh
        python3 secure_admin.py --check    # just print the checklist
"""

import argparse
import hashlib
import secrets
import stat
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CREDS = ROOT / "ADMIN_CREDENTIALS.txt"

CHECKLIST = """
OFFICE MACHINE HARDENING CHECKLIST
----------------------------------
[x] Everything binds to 0.0.0.0 only inside your LAN - never port-forward
    8090/8080/8081 to the internet. For remote access use a VPN (e.g. Tailscale,
    free, WireGuard-based) instead of open ports.
[ ] Change all generated passwords after first login (this file gives you them once).
[ ] Enable disk encryption (BitLocker on Windows / LUKS on Linux) - member data
    is on a plain folder, encryption protects the whole machine.
[ ] Turn on automatic OS updates.
[ ] Back up data/ and documents/ weekly to a USB drive (see README).
[ ] In Flarum: disable guest posting; approve new registrations manually.
[ ] In Admidio: mark the members module "registered users only".
[ ] Cloud AI mode only: documents leave the machine - keep sensitive minutes
    in privacy mode 1 or 2 (re-run install.py --reconfigure to switch).
"""


def gen_password(words=4):
    """Diceware-style: memorable + strong. ~51 bits at 4 words + digit."""
    wordlist = ("harbor meadow lantern copper drift amber quill summit "
                "orchid tundra marble cinder willow zephyr garnet hollow "
                "cobalt thicket prism ember fjord lagoon sable quiver "
                "basalt meridian saffron timber clover ridgeline").split()
    parts = [secrets.choice(wordlist) for _ in range(words)]
    parts.append(secrets.choice(string.digits))
    parts.append(secrets.choice("!@#$%&*"))
    return "-".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="only print the hardening checklist")
    args = ap.parse_args()

    if args.check:
        print(CHECKLIST)
        return

    services = {
        "HUB_AI_API": "Office Hub AI glue (basic auth / first-run admin)",
        "FLARUM_ADMIN": "Flarum forum admin (enter during web install at :8081)",
        "ADMIDIO_ADMIN": "Admidio admin (enter during web install at :8080)",
        "MARIADB_ROOT": "MariaDB root (create-databases step in setup_apps.py)",
    }
    lines = ["# Office Hub admin credentials",
             "# Generated: shown once here, change after first login.", ""]
    print("\nGenerated admin credentials (also saved to ADMIN_CREDENTIALS.txt):\n")
    hub_pw = ""
    for svc, what in services.items():
        pw = gen_password()
        if svc == "HUB_AI_API":
            hub_pw = pw
        lines.append(f"{svc} = {pw}")
        print(f"  {svc:15} {pw}")
        print(f"  {'':15} ({what})\n")
    CREDS.write_text("\n".join(lines) + "\n")
    try:
        CREDS.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600 on Linux/macOS
    except OSError:
        pass
    # also set the /admin web console password to the HUB_AI_API password
    try:
        hash_file = ROOT / "data" / "admin_hash.txt"
        hash_file.parent.mkdir(exist_ok=True)
        import hashlib
        hash_file.write_text(hashlib.sha256(hub_pw.encode()).hexdigest())
        print(f"  /admin web console password set to HUB_AI_API's password "
              f"(change anytime at http://localhost:8090/admin)")
    except Exception:
        pass
    print(f"Saved to {CREDS.name}. Keep it safe, then change passwords after first login.")
    print(CHECKLIST)


if __name__ == "__main__":
    main()
