# What's new in Office Hub

One normal office PC runs your club's private AI, member portal, and forum.
No Docker, no subscriptions, 100% open source. This page covers everything
added since the first release — in plain language.

---

## v1.2 — Nightly backups, on a schedule

- **Backups now happen every night at 2 AM**, not just when the PC restarts.
  One click: open the admin console → **Backups** → *"Turn on nightly backup
  (02:00)"*. (Linux/macOS uses cron, Windows uses Task Scheduler — the hub
  sets it up for you.)
- **The doctor now watches your backups too**: run `python3 doctor.py` and it
  warns you if the last snapshot is missing or getting old — and offers to
  take one right there.
- Boot-time backups still happen automatically, so a PC that's often switched
  off at night is still covered.

## v1.1 — Your data is protected and logged

- **Backups from day one.** The installer takes a first snapshot before it
  says "Done", and every start takes another. One snapshot protects your
  **documents, settings, bot memory, and the member database** (Admidio +
  Flarum) — everything that can't be re-downloaded. Only a few MB each.
- **Bring it all back** after a dead drive or a bad delete:
  `python3 hub/backup.py --restore <snapshot>` (see `--list`).
- **Free cloud backups**: paste your OneDrive / Dropbox / Google Drive folder
  into the admin console's Backups card and every snapshot uploads itself.
- **Activity trail**: the status page now shows *who changed what, and when* —
  every admin action and every Officer AI command, recorded and backed up.
- **Officer AI (advanced, switched off by default)**: an AI helper that can
  run the doctor, check backups, and rebuild the search index — from the
  `office-ai` menu or by asking in plain English. It's passworded,
  local-machine-only, limited to a short allowlist, and fully audited.

## v1.0 — The whole backbone on one office PC

- **Private AI assistant**: ask questions about your bylaws, minutes, and
  budgets — answers come from your own documents, with sources. Fully offline
  with Qwen, or a free cloud API if you prefer.
- **Member portal** (Admidio), **forum** (Flarum), free **video rooms** (Jitsi).
- **Power Mode**: members chat with the hub from Telegram or Discord; weekly
  automations included.
- **Web admin console** at `/admin`: change anything later without
  reinstalling.
- **Self-repairing doctor** and a plain-English install guide for
  non-technical officers.

---

## The 60-second tour (after installing)

1. Start everything: `./start-hub.sh` (Windows: `start-hub.bat`)
2. Ask the AI: **http://localhost:8090** — try *"what are the annual dues?"*
3. Turn on nightly backups: **http://localhost:8090/admin** → Backups
4. See it all: **http://localhost:8090/status**

**Get help:** the [INSTALL_GUIDE.md](INSTALL_GUIDE.md) walks you from a fresh
PC to your first AI question. Want it installed for you? Visit
[MrTsComputers.com](https://MrTsComputers.com).
