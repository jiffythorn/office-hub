# Office Hub — Installation Guide

**For clubs, HOAs, non-profits, and community organizations**
*A plain-English walkthrough from an empty computer to your first AI answer.*

| | |
|---|---|
| **Version** | 1.0 |
| **Time needed** | 45–75 minutes (most of it is waiting for downloads) |
| **Who does this** | The person setting up the office computer. No coding needed. |
| **Cost** | $0 in software. Every component is free and open source. |

---

## What you are installing

One normal office computer becomes your organization's digital hub:

| Piece | What your members use it for | Where |
|---|---|---|
| **AI Assistant** | Ask questions about your documents ("what are the dues?") | http://localhost:8090 |
| **Member portal** (Admidio) | Member list, dues, events, groups | http://localhost:8080 |
| **Forum** (Flarum) | Discussions, announcements, private messages | http://localhost:8081 |
| **Video meetings** | Free Jitsi rooms, nothing to install | meet.jit.si |

Everything runs on that one computer. Nothing is sent to the cloud unless
you choose the "cloud" privacy option during setup.

---

## Before you start — checklist

- [ ] A Windows 10/11 or Ubuntu/Linux computer that stays switched on
- [ ] At least **8 GB RAM** (4 GB works without the local AI)
- [ ] **10 GB free disk space** (more if you pick the local AI)
- [ ] Internet connection (only needed during installation)
- [ ] The **Office Hub folder** downloaded from the project's GitHub page
      (it contains this guide)

---

## Part 1 — Install Python (5 minutes)

### Windows
1. Open your browser and go to **https://www.python.org/downloads/**
2. Click the big yellow **Download Python** button.
3. Run the downloaded file. **IMPORTANT:** on the first screen, tick the box
   that says **"Add python.exe to PATH"** before clicking *Install Now*.
4. When it says *Setup was successful*, close the window.

### Linux (Ubuntu)
Open a terminal (Ctrl+Alt+T) and type:
```
sudo apt install python3 python3-venv python3-pip
```
Enter your password when asked.

**Check it worked:** open a new Command Prompt (Windows: Start → type `cmd`)
or terminal and type `python --version` (Windows) or `python3 --version`
(Linux). You want to see a number like 3.11 or 3.12.

---

## Part 2 — One-click install (10 minutes)

1. Open the **Office Hub folder** (the one you downloaded from GitHub — the
   green **Code** button → *Download ZIP*, then unzip).
2. Double-click:
   - **Windows:** `install.bat`
   - **Linux:** right-click in the folder → *Open Terminal* → type `./install.sh`
3. The installer will **check your computer** and print what it finds.
   If it lists something as `NEEDED` (like PHP or MariaDB), it will offer to
   install them for you — **type `y` and press Enter** and enter your
   password if asked. (On Windows it gives you download links instead.)
4. The installer asks **one important question**: how much privacy?

   | Option | Choose this if… |
   |---|---|
   | **1 — Retrieval-only** | Your documents are sensitive (financials, personal data). The assistant only quotes your documents back. Totally offline. *(Safest — recommended.)* |
   | **2 — Local AI** | You want full written answers, still 100% offline. Downloads a ~1–5 GB AI model. |
   | **3 — Cloud AI** | Documents are mostly public and you want the smartest answers. Needs a free API key (Part 6). |
5. **Power Mode question** — the installer offers an optional AI agent layer
   (free, MIT, ~200 MB RAM):
   - **y** = members can chat with the hub from **Telegram or Discord on their
     phones**, the assistant gains scheduling, automations, and long-term
     memory, and officers get a full agent WebUI. You'll be asked which
     channel(s) to connect and can paste a bot token right there (Telegram:
     free via **@BotFather** in 2 minutes; Discord: via the developer portal —
     see POWER_MODE_GUIDE.md), or skip and add them later.
   - **Enter/N** = keep the hub ultra-light. You can add Power Mode any time:
     `python3 install.py --with-agent`
6. Wait for "Your blueprint" to print. Done!

---

## Part 3 — Start everything (1 minute)

Double-click **`start-hub`** (`start-hub.bat` on Windows, `start-hub.sh` on
Linux). One script starts everything in order:

```
MariaDB (database) → Member portal → Forum → AI engine → AI assistant
```

You should see:
```
[hub] up.
  AI assistant : http://localhost:8090
  Member portal: http://localhost:8080
  Forum / chat : http://localhost:8081
```

> To stop everything later: double-click `stop-hub` (it leaves the database
> running, which is normal and safe).

---

## Part 4 — Create secure admin passwords (2 minutes)

In the terminal (or Command Prompt opened in the hub folder), run:
```
python3 secure_admin.py        (Windows: python secure_admin.py)
```
It prints four strong passwords and saves them to `ADMIN_CREDENTIALS.txt`.
**Write these down somewhere safe** (a paper note in a drawer is fine),
then delete the file after you've memorized or stored them.

---

## Part 5 — Set up the member portal and forum (15 minutes)

First fetch the two web apps (once):
```
python3 setup_apps.py          (Windows: python setup_apps.py)
```
Then run `start-hub` again, and in your browser:

### The member portal (Admidio)
1. Open **http://localhost:8080** — a setup wizard appears.
2. Fill in the database page exactly like this:
   - Database host: `localhost` · Port: `3306`
   - Database name: `admidio`
   - User: `root`, Password: *(the one from setup_apps.py output, or your
     MariaDB root password)*
3. Create the administrator account using the **ADMIDIO_ADMIN** password
   from Part 4. *Change it after first login.*

### The forum (Flarum)
1. Open **http://localhost:8081** — a setup wizard appears.
2. Same database details, but database name: `flarum`.
3. Create the admin account with the **FLARUM_ADMIN** password from Part 4.

*(No Flarum wizard? Composer was missing — see Troubleshooting below.)*

---

## Part 6 — Only if you chose Cloud AI

1. Go to **https://openrouter.ai/keys**, create a free account, and click
   **Create Key**. Copy the key.
2. Set it before starting the hub:
   - **Windows:** `set OPENROUTER_API_KEY=your-key-here` in the Command
     Prompt before running `start-hub.bat`
   - **Linux:** `export OPENROUTER_API_KEY=your-key-here` before `./start-hub.sh`

> Choosing option 1 or 2 (recommended for most organizations) skips this
> part entirely.

---

## Part 7 — Your first AI question 🎉

1. Put a document into the **`documents`** folder inside the hub folder —
   your bylaws, meeting minutes, a PDF of anything. (.txt, .md, .csv, .pdf)
2. Open **http://localhost:8090** in your browser.
3. Type: **"What do our documents say about dues?"** and press Enter.

The answer cites the file it came from. Add or change documents any time —
the hub notices and re-indexes automatically.

---

## Part 8 — Check that everything is healthy

Open **http://localhost:8090/status**. It shows:

- Which of the components are **UP** (green) or **DOWN** (red)
- How many documents are indexed
- The recent questions asked and how long answers took
- **Backups** — when the last snapshot ran, what it protects
- The **activity trail** — who changed what, and when

**Changing settings later?** Open **http://localhost:8090/admin** — the admin
console. First visit asks you to create an admin password; after that you can
switch privacy mode, Power Mode, chat tokens, the assistant's name, timezone,
and reindex or restart services with one click. No reinstalling.

If something is DOWN, run the doctor — it repairs most problems itself:
```
python3 doctor.py              (Windows: python doctor.py)
```

### Backups: your safety net (already working!)

Good news: **backups are automatic.** The installer took a first snapshot
before it said "Done", and every boot takes another (if the last one is over
6 hours old). One snapshot = your documents, settings, bot memory, and the
member database — everything that can't be re-downloaded. It's only a few MB.

Peek at **http://localhost:8090/admin → Backups**: the last snapshot is shown
there, with **Back up now** and **Check backups** buttons.

**One thing worth doing today:** tell the hub to also copy backups to a cloud
folder. If your club already uses OneDrive, Dropbox, or Google Drive on the
office PC, paste that folder's path into the Backups card and save. From then
on, every backup automatically lands in the cloud too. (Prefer a USB drive?
Same box: just use a folder on it.)

**Recommended: turn on the nightly backup.** One click in the admin console:
**Backups → Turn on nightly backup (02:00)**. The hub adds itself to the
system's own scheduler — cron on Linux, Task Scheduler on Windows — and takes
a snapshot every night at 2 AM while the office PC is on. (Boot-time backups
still happen regardless, so a machine that's often off at night is still
covered.) Prefer the terminal?

```
python3 hub/backup.py --schedule         # turn on the nightly snapshot
python3 hub/backup.py --unschedule      # turn it off again
python3 hub/backup.py --schedule-status # is it on?
```

Officers can check the current state any time: the Backups card shows
**Nightly schedule: ON/OFF**, and toggling it is logged in the activity trail
like every other admin action.

**If disaster strikes** (drive dies, someone deletes the wrong thing):
```
python3 hub/backup.py --list                              (see snapshots)
python3 hub/backup.py --restore snapshot-XXXXXXXX-XXXXXX  (bring them back)
```
Your club is back. That's the whole point.

### The Officer AI: your maintenance helper (optional, switched off by default)

Inside the admin console there's an **Officer AI (advanced)** card. Switch it
on and you get an AI helper that can check the installation, repair problems,
take backups, and rebuild the search index — on your say-so.

- It's **protected**: it only accepts requests holding the access key shown on
  that card, and it works **from the office PC only** unless you explicitly
  allow VPN users.
- It **can't run arbitrary commands** — only a short list of maintenance
  actions, and every single thing it does is written to the activity trail.
- Easiest way to use it, in a terminal in the hub folder:
  `./office-ai.sh` (Windows: `office-ai.bat`) — pick from the menu, or ask in
  plain English: `./office-ai.sh "is anything broken?"`

---

## Part 9 — Using the hub from outside the office

Sooner or later someone asks: *"can I check the forum from home?"* Here is
how to do it **safely** and for **$0 a month**.

### The golden rule: never open your router

You may see tutorials saying to "forward ports" or set up a "DMZ" on your
router. **Don't.** That copies your club's member data onto the open
internet, where automated bots find an open door within hours and try thousands
of password guesses a day. There is a better way that costs nothing.

### The free way: Tailscale (a private wire to your office PC)

Tailscale (https://tailscale.com) gives your office PC and your home
device a private, encrypted tunnel — the hub behaves as if you were sitting
in the office. No router changes, no open doors.

**One-time setup (10 minutes):**
1. On the **office PC**: go to tailscale.com → *Download* → install → sign
   in with a Google/Microsoft/GitHub account. The PC now has a private
   name (something like `office-pc`).
2. On the **laptop or phone you travel with**: install Tailscale and sign
   in **with the same account**.
3. From anywhere, open `http://office-pc:8090` (and `:8080` / `:8081` for
   the portal and forum). That's it — hotel Wi-Fi, phone hotspot, works.

### The one-shared-account trick (avoid the "per user" billing trap)

Tailscale's free plan allows **3 users and 100 devices** — and paid plans
charge **per user, not per device**. So don't invite each board member as a
separate user (that's what triggers the "upgrade to $6/user/month" e-mail).
Instead:

1. Create **one shared account** just for this (for example a free Gmail
   like `yourclub.remote@gmail.com`) and use it for the Tailscale login on
   the office PC.
2. Install Tailscale on each officer's laptop/phone and sign **every one of
   them into that same shared account**. Each device counts as a device —
   you can have dozens, all free.
3. Keep that shared password in the same sealed envelope as the other hub
   passwords. When someone leaves the club, remove their device in the
   Tailscale admin page (one click) — no passwords to chase.

That's the whole trick: **one user, many devices, $0 forever.**

### What if VPNs are not for you?

- Meetings stay on **Jitsi** (free, already part of the hub) — nobody needs
  remote access just to attend.
- Or simply decide the hub is office-only. It works perfectly that way.

### Talking to the hub from members' phones (Power Mode)

See **[POWER_MODE_GUIDE.md](POWER_MODE_GUIDE.md)** for the full officer
walkthrough (Telegram and Discord, testing from a phone, and safely
unlocking officer tools). The short version:

1. Get a bot token — Telegram: message **@BotFather** → `/newbot`; Discord:
   create an app at discord.com/developers/applications → Bot → Reset Token.
2. Put the token in `data/nanobot/config.json` (set `"enabled": true` in the
   matching section), or re-run the installer and paste it when asked.
3. Restart with `start-hub`. Members now just message your bot — questions
   about fees, events, and bylaws get answered from your documents.

The agent is **member-safe by default** (no shell/web tools). Officers can
unlock more abilities for themselves in the same config file. Privacy note:
chat messages travel over Telegram's or Discord's servers — great for public
info, keep confidential minutes on the office screen.

### What NOT to do

| Tempting shortcut | Why not |
|---|---|
| Port forwarding / DMZ on the router | Open to the whole internet; bots find it within hours |
| "Dynamic DNS + port forward" packages from router vendors | Same open door, plus a recurring fee |
| Business VPN subscriptions sold to clubs | $60–100+/year for what Tailscale does free |
| Putting the hub on a $10–20/month cloud server | Your office PC already does the job for free |

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `install.bat` closes instantly | Python wasn't added to PATH — reinstall it and tick **Add python.exe to PATH**. |
| "dependency check failed" when starting | Read the `[FAIL]` lines; each has a `fix:` command. Or just run `python3 doctor.py`. |
| Portal says "database connection failed" | MariaDB isn't running. Linux: `sudo systemctl start mariadb`. Windows: Start → Services → start **MariaDB**. |
| Flarum wizard never appears | Composer is missing. Install from https://getcomposer.org/download/, then run `python3 setup_apps.py` again. |
| AI says "Local AI engine is not reachable" | You're in local mode but the model isn't downloaded. Run `python3 install.py` again (it finishes the downloads). |
| Cloud mode says API key missing | Repeat Part 6 — the key is set per terminal window. |
| Can't reach the hub from home | You need the private tunnel, not open ports. Install Tailscale on **both** machines and sign in with the same shared account (Part 9). Then use `http://office-pc:8090`. |
| Everything else | `python3 doctor.py --yes` repairs venv, packages, downloads, and restarts services automatically. |

---

## Security for non-technical folks (read this bit!)

- ✅ Everything only works **inside your office network**. Never "open
  ports" on your router — for remote access use the private Tailscale
  tunnel (free: https://tailscale.com). The **one-shared-account trick** in
  Part 9 keeps it on the free tier no matter how many officers need in.
- ✅ Turn on **disk encryption** (Windows: Settings → Privacy & Security →
  BitLocker. Ubuntu: tick "encrypt home folder" or use LUKS).
- ✅ Turn on **automatic updates** for the operating system.
- ✅ In Flarum: require admin approval for new members. In Admidio: set
  member lists to "registered users only".
- ✅ Back up weekly: copy the whole hub folder's `data/` and `documents/`
  subfolders to a USB stick.
- ⚠️ Only the **Cloud AI** option sends anything to the internet — keep
  sensitive minutes in option 1 or 2.

---

## Final checklist — before you call it done

Whoever set the hub up should leave behind:

- [ ] The hub folder, with `install.bat`/`install.sh` still in it
- [ ] A printed copy of this guide
- [ ] The privacy mode they chose, written down
- [ ] Admin passwords changed from the generated ones, in a sealed envelope
- [ ] `ADMIN_CREDENTIALS.txt` **deleted**
- [ ] One test question answered successfully on their own documents
- [ ] http://localhost:8090/status showing all green
- [ ] A scheduled task so the hub starts when the computer boots
      (Windows: put a shortcut to `start-hub.bat` in the Startup folder —
      Win+R → `shell:startup`. Linux: a systemd user service for
      `start-hub.sh`.)

---

## Licensing

Everything in this bundle is free and open source (this hub's glue code:
MIT; Qwen AI: Apache 2.0; llama.cpp and Flarum: MIT; Admidio and MariaDB:
GPL v2; PHP/Python: liberal licenses). Share it, modify it, use it freely —
if you redistribute, keep the license/notice files and point to the upstream
projects for the GPL parts. Do **not** swap in the Qwen 2.5-3B or Meta Llama
models: their licenses are more restrictive.
