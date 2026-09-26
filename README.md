# Office Hub — The Ultimate Lightweight Blueprint

One normal office desktop runs the whole digital backbone for a club, HOA,
non-profit, or community group. **Zero Docker. 100% open source. Free to use,
share, and build on.**

| Layer | What it does | Runs as |
|---|---|---|
| **AI Engine** | Qwen 2.5 (Apache-2.0) via llama.cpp, or free cloud API, or retrieval-only | single binary, port 8082 |
| **AI Glue** | indexes your documents, answers questions, tiny chat UI | Python/FastAPI, port 8090 |
| **Member portal** | Admidio: members, dues, events, groups | PHP, port 8080 |
| **Forum/Chat** | Flarum: discussions, private conversations | PHP, port 8081 |
| **Video** | Jitsi Meet (free public meet.jit.si rooms) | nothing local |
| **Documents** | plain folder of text/PDF the AI references dynamically | `documents/` |

Resource use while idle: **≈ 2 GB RAM** (mostly the AI model). An office PC
won't notice it running.

---

## One-click install

> **Non-technical?** Follow **[INSTALL_GUIDE.md](INSTALL_GUIDE.md)** —
> a plain-English walkthrough from a fresh PC to the first AI question.

**Windows:** double-click `install.bat`
**Linux/macOS:** `./install.sh`

The installer will:

1. **Audit** the machine (CPU, RAM, disk, installed tools).
2. **Decide dependencies** automatically from the audit.
3. **Ask one human question** — how much privacy does this organization need?

   | Level | Mode | Data flow | Needs |
   |---|---|---|---|
   | 1 | `retrieval_only` | 100% offline, answers quoted from your documents | nothing |
   | 2 | `local` | 100% offline, Qwen writes the answers | ~1–5 GB download, ~2 GB RAM |
   | 3 | `cloud` | questions + snippets go to a free cloud API | free API key |

4. **Pick the best Qwen model** that fits the measured RAM (0.5B / 1.5B / 7B,
   all Apache-2.0; the restrictive-license 3B is deliberately excluded).
5. Create the Python environment, write `config.json`, download the AI stack
   (llama.cpp via `gh`, model via HuggingFace).

Then:

```bash
python3 secure_admin.py     # generates strong admin passwords (saved once)
python3 setup_apps.py       # fetches Admidio + Flarum (needs PHP + MariaDB)
./start-hub.sh              # or start-hub.bat - ONE script starts EVERYTHING:
                            #   MariaDB -> Admidio :8080 -> Flarum :8081
                            #   -> llama.cpp (local mode) -> AI API :8090
```

First time in each web app, run its installer: choose MariaDB, database
`admidio` / `flarum`, user/password from `python3 setup_apps.py` output.
Change all passwords after first login.

## How the dependency chain works

Every layer checks the layer below it, so nothing half-boots:

```
install.sh / install.bat
  └── install.py      audits machine -> decides deps -> offers to auto-install
        │             system packages (PHP, MariaDB, gh) on Linux; creates .venv;
        │             installs pip packages; downloads AI stack; writes config.json
        └── check_deps.py   full readiness report (same one the start scripts run)

start-hub.sh / .bat   -> check_deps.py --section hub  (Python, venv, packages,
                         config, llama-server + model OR cloud key), then brings
                         up MariaDB, Admidio and Flarum (best effort), the local
                         AI engine when configured, and finally the glue API
start-apps.sh / .bat  -> check_deps.py --section apps (PHP 8.1+ with pdo_mysql,
                         mbstring, gd, dom, openssl, curl; composer; MariaDB
                         running; apps/admidio + apps/flarum staged) then launch
```

`python3 check_deps.py` any time prints a full `[ok]/[warn]/[FAIL]` report with
the exact fix for each problem. Installers accept `--auto-install-system` to
let install.py run the package manager for you (apt/dnf/pacman).

Or let the machine fix itself: `python3 doctor.py` checks, repairs what it
can (venv, pip packages, llama-server, model, app downloads), asks before any
sudo/system step, verifies again, and restarts the services when healthy.
Use `--yes` for unattended repairs, `--section apps` / `--section hub` to
scope it, `--no-system` to forbid sudo, `--check-only` for a plain report.

## Daily use

- Drop club documents (`.txt`, `.md`, `.pdf`) into `documents/` — the hub
  re-indexes them automatically on the next question.
- Ask things like *"what was decided about the picnic budget?"* at
  `http://localhost:8090`. Answers cite their source files.
- `POST /reindex` forces a rescan; `GET /health` shows mode + index stats.

## Power Mode (optional): the AI powerhouse layer

At install you're asked one extra question: **enable Power Mode?** It adds
[nanobot](https://github.com/HKUDS/nanobot) (MIT, 48k★) as an agent layer on
top of the same privacy mode you chose — nothing else changes:

- **Members chat from their phones** — a Telegram or Discord bot (free, 2-min
  setup) answers questions from your documents. Slack, email, Matrix and more
  are supported too. Members install nothing new.
- **Automations** — ready-made preset pack (`automations/club-presets.json`):
  weekly event digests, meeting-eve reminders, Monday document summaries,
  dues follow-ups, minutes drafting — installed by sending one sentence to
  the bot in chat.
- **Long-term memory** and a full **agent WebUI** (`nanobot webui`) for officers.
- **Member-safe by default**: the generated config disables shell/web tools and
  restricts the agent to its workspace. Officers can re-enable tools for
  themselves in `data/nanobot/config.json`.
- Uses the **same engine** (local llama.cpp or cloud) — the privacy choice is
  preserved. Costs ~200 MB RAM extra.

Install it any time with `python3 install.py --with-agent` (or answer **y** at
the prompt). The doctor knows how to repair it. Skip it and the hub stays the
ultra-light blueprint.

> Note: Telegram/Discord messages transit those services' servers. Fine for
> bylaws/fees/events; keep confidential minutes on the office LAN.

## Admin console & switching settings later

**http://localhost:8090/admin** — a password-protected settings page for
everything the installer decides: privacy mode, cloud provider, local-AI
context/threads, chain tiers, Power Mode with chat tokens, assistant name,
timezone, plus Reindex and Restart-AI-services buttons. First visit asks you
to create the admin password (or set it via `secure_admin.py`, which uses the
HUB_AI_API password). Saves apply after the built-in restart — no need to
re-run the installer.

The command line still works too:

```bash
python3 install.py --reconfigure
```

Cloud API keys are **never stored in config.json** — they're read from an
environment variable (`OPENROUTER_API_KEY` or `GROQ_API_KEY`) at runtime.

## Backups: built in from day one

The installer takes a **first backup** before it prints "Done", and every
`start-hub.sh` boot takes an **incremental snapshot** (skipped if one is less
than 6 hours old). A snapshot protects everything irreplaceable:

- `documents/` — the club's actual knowledge
- `config.json` + `data/` — settings, admin password hash, **audit trail**, bot
  config and memory, pairing approvals, document index
- **Admidio + Flarum databases** via `mysqldump` (members and forum!)

It deliberately skips re-downloadable stuff (AI model, apps, venvs), so a
snapshot is only a few MB. The newest **20 snapshots / 30 days** are kept in
`data/backups/` as plain files — copy them to a USB stick, or point the admin
console at a folder your OneDrive/Dropbox/Google Drive already syncs (or an
[rclone](https://rclone.org) remote) and they upload automatically.

```bash
python3 hub/backup.py            # take a snapshot now
python3 hub/backup.py --list     # see what exists
python3 hub/backup.py --verify   # re-hash every file, prove integrity
python3 hub/backup.py --restore snapshot-20260101-120000-boot   # bring it all back
python3 hub/backup.py --restore <name> --restore-databases      # + member DBs
```

The **Backups card** in the admin console shows the last snapshot and has
"Back up now" / "Check backups" buttons plus the cloud-folder setting.
**Nightly backups** are one click away — "Turn on nightly backup (02:00)"
installs a cron entry (Linux/macOS) or a Task Scheduler job (Windows) so a
snapshot exists even if the PC doesn't reboot for weeks. CLI equivalents:
`python3 hub/backup.py --schedule` / `--unschedule` / `--schedule-status`.

## Officer AI: a maintenance assistant with real powers (heavily fenced)

The member chat bot is deliberately restricted. Officers need the opposite: an
AI that can actually *fix* things. The Officer AI can run the readiness check,
auto-repair the installation, take and verify backups, rebuild the index,
restart AI services, and install helper CLIs (`opencode`, `freebuff`) — from
the **office-ai** CLI or a chat agent you point at it.

It is fenced four ways:

1. **Switched off until you enable it** — in the admin console's "Officer AI
   (advanced)" card. Off means off.
2. **Local machine only** — the gateway binds to `127.0.0.1`; nothing on the
   LAN or internet can reach it. Remote use over a VPN requires flipping an
   explicit switch *and* holding the access key (regenerate it any time).
3. **Allowlist, not freedom** — there is no shell. The AI can "press buttons"
   from a short, readable list (`check`, `doctor`, `backup`, `reindex`, …);
   anything else is refused and logged. `rm`, `sudo`, and arbitrary commands
   simply do not exist for it.
4. **Everything is audited** — every run, refusal, and login attempt lands in
   `data/audit.jsonl`, shown on the status page and included in every backup.

```bash
./office-ai.sh                        # interactive menu (Windows: office-ai.bat)
./office-ai.sh check                  # run one action directly
./office-ai.sh "is anything broken?"  # ask in plain English - the local AI
                                      # picks the action; answers stay local
./office-ai.sh history                # what has it been doing?
```

The same allowlist is exposed as an OpenAI-style tool bridge at
`http://127.0.0.1:8090/officer` so a Power Mode agent profile can act as the
Officer AI's conversational front-end — with the same key, allowlist and audit.

## Security posture

- Everything binds to the LAN only; **never port-forward** 8080–8090.
- For remote access use a VPN (Tailscale is free and zero-config) — see
  **Part 9 of the [Install Guide](INSTALL_GUIDE.md)** for the full walkthrough,
  including the one-shared-account trick that keeps it on the free tier
  (one login, many devices, $0/month — avoid per-user VPN billing).
- `secure_admin.py` prints a hardening checklist (disk encryption, backups,
  manual approval of new forum users).
- `ADMIN_CREDENTIALS.txt` is written with owner-only permissions; delete it
  after you've memorized/changed the passwords.

## Licenses of the building blocks

Every component is free and open source software:

| Component | License | What that means |
|---|---|---|
| Qwen 2.5 (0.5B/1.5B/7B) | **Apache 2.0** | use, modify, redistribute freely (keep the NOTICE file) |
| llama.cpp | **MIT** | use, embed, no source obligations |
| FastAPI / uvicorn / pypdf | MIT/BSD | use, embed freely |
| Python | PSF | bundle the runtime freely |
| Flarum | **MIT** | use, bundle freely |
| Admidio | **GPL v2** | use freely — redistributions must provide source (a link to upstream satisfies this) |
| MariaDB | **GPL v2** | use freely — same source obligation when redistributed |
| PHP | PHP License | bundle freely |
| Jitsi Meet (public service) | Apache 2.0 | free to link/embed |

**Two obligations if you redistribute:** ship the source (or upstream links)
for the GPL components, and keep license/notice files intact. *Do not*
substitute Qwen **2.5-3B** (Qwen research license) or Meta Llama models
(community license) — Qwen 0.5B/1.5B/7B are the redistributable picks.

## Project layout

```
install.py            smart installer (audit -> decide -> privacy -> power mode -> model)
.venv-agent/          optional nanobot agent (Power Mode: chat apps, automations)
check_deps.py         readiness checker used by installer AND all start scripts
doctor.py              check + auto-repair + restart (python3 doctor.py --yes)
INSTALL_GUIDE.md       plain-English setup guide for non-technical users
POWER_MODE_GUIDE.md    officer handbook: chat bots, testing, officer tools
automations/           ready-to-install club automation preset pack
install.sh / .bat     one-click wrappers
start-hub.sh / .bat   ONE script starts the whole blueprint in order:
                         MariaDB -> Admidio -> Flarum -> llama.cpp -> AI API
                         (--hub-only skips MariaDB + apps)
start-apps.sh / .bat  launch just Admidio + Flarum (subset of start-hub)
stop-hub.sh / .bat    stop everything except the MariaDB system service
secure_admin.py       strong admin credentials + hardening checklist
setup_apps.py         fetch Admidio/Flarum, pre-create databases
hub/engine.py         indexer (SQLite FTS5) + retrieval + AI backends
hub/server.py         FastAPI glue + chat UI
hub/admin.py          /admin web console (settings without the installer)
hub/llm_proxy.py      tiered AI fallback chain (:8085)
hub/backup.py         self-contained snapshots + mysqldump + verify/restore
hub/audit.py          append-only audit trail (data/audit.jsonl)
hub/admin_ai.py       Officer AI gateway: allowlist + key + local-only fence
office-ai.sh / .bat   officer CLI: menu, one-shot actions, plain-English asks
config.json           written by installer; the whole system reads it
documents/            <-- your club's documents live here
apps/                 admidio/ flarum/ (after setup_apps.py)
ai/                   llama.cpp binary + GGUF models (after install)
data/                 pidfiles, logs, document index, audit trail, backups
```

## Credits & upstream resources

This hub is glue around excellent open-source projects — all credit and
upstream docs live here:

- **Admidio** (member portal) — https://github.com/Admidio/admidio · docs: https://www.admidio.org
- **Flarum** (forum) — https://github.com/flarum/flarum · docs: https://docs.flarum.org
- **llama.cpp** (local AI engine) — https://github.com/ggml-org/llama.cpp
- **Qwen 2.5 models** (Apache-2.0 GGUFs) — https://huggingface.co/Qwen
- **FastAPI** — https://github.com/fastapi/fastapi · **uvicorn** — https://github.com/encode/uvicorn · **pypdf** — https://github.com/py-pdf/pypdf
- **MariaDB** — https://mariadb.org · **PHP** — https://www.php.net
- **Jitsi Meet** (video) — https://meet.jit.si
- **OpenRouter / Groq** (optional free cloud AI tiers) — https://openrouter.ai · https://groq.com
- **Tailscale** (recommended VPN for remote access) — https://tailscale.com

The glue code in this repository (installer, doctor, dependency checker,
FastAPI service, scripts) is released under the MIT License — see `LICENSE`.
