# Power Mode — Officer's Guide

*Everything officers need to let members chat with the Office Hub from their
phones — without handing anyone the keys to the office computer.*

| | |
|---|---|
| **Who this is for** | The officer (or helpful volunteer) who manages the hub |
| **Time needed** | 15 minutes per chat channel, one-time |
| **Cost** | $0 — Telegram bots, Discord bots, and the agent are all free |
| **Golden rule** | The **member bot answers questions**. The **office computer itself stays members-reach-never**. |

---

## 1. The member-safe model (read this first)

Power Mode ships deliberately locked down:

| Ability | Member bot (default) | Officer use |
|---|---|---|
| Answer questions from `documents/` | ✅ | ✅ |
| Chat from Telegram / Discord / Slack / email | ✅ | ✅ |
| Scheduled automations, long-term memory | ✅ | ✅ |
| Read/write files outside its workspace | ❌ blocked | officer can enable |
| Run shell commands on the office PC | ❌ blocked | officer can enable |
| Browse the web | ❌ blocked | officer can enable |

Config lives in **`data/nanobot/config.json`** inside the hub folder. Every
change below is one edit + a restart with `start-hub`.

---

## 2. Telegram bot (recommended first channel)

1. In Telegram, search for **@BotFather** (blue-check, verified) and open it.
2. Send `/newbot`. Choose a display name ("Riverside Garden Club") and a
   username ending in `bot` (`riversidegarden_bot`).
3. BotFather replies with a **token** like `7123456789:AAE…`. That token is
   the bot's password — treat it like one.
4. Open `data/nanobot/config.json`, find the `"telegram"` section, and set:
   ```json
   "telegram": {
     "enabled": true,
     "token": "7123456789:AAE...",
     "allowFrom": [],
     "groupPolicy": "mention"
   }
   ```
5. Restart: run `stop-hub`, then `start-hub`. The console prints
   `[agent] Power Mode ON - chat live on: telegram`.
6. **Test from any phone:** open the bot in Telegram and send
   *"what are the annual dues?"* — it should answer from the bylaws and
   cite the file. (The office PC must be on and `start-hub` running.)

**Keep the bot to your club:** with `allowFrom: []` anyone who finds the bot
can ask questions. That is usually fine (answers come only from your public
documents) — but to restrict it, put the Telegram user IDs of your members in
`allowFrom`, or set the bot's privacy so only members you approve can DM it.

---

## 3. Discord bot

1. Go to **discord.com/developers/applications** → *New Application* →
   name it after the club.
2. Left menu → **Bot** → *Reset Token* → **copy the token** (you'll need it
   in a moment; anyone with this token controls the bot).
3. Still in the Bot tab, enable **Message Content Intent** (required to read
   questions).
4. Left menu → *OAuth2 → URL Generator*: tick **bot** scope, then in the
   permission list tick **Send Messages** and **Read Message History**.
   Copy the generated URL at the bottom, open it in a browser, and add the
   bot to your club's Discord server.
5. In `data/nanobot/config.json`, set the `"discord"` section:
   ```json
   "discord": {
     "enabled": true,
     "token": "MTIz...your-token...",
     "allowFrom": [],
     "allowChannels": [],
     "groupPolicy": "mention"
   }
   ```
6. Restart the hub. In Discord, **@mention the bot** with a question —
   `@RiversideClub what are the plot fees?` (the `mention` policy means it
   ignores chatter and only answers when called, like a polite member).
7. To limit it to a specific channel (say `#ask-the-club-bot`), put that
   channel's ID in `allowChannels`.

---

## 4. Testing from a phone (the 5-minute proof)

1. Office PC: `start-hub` running, and `http://localhost:8090/status` shows
   **Agent gateway (Power Mode): UP**.
2. Phone, on mobile data (not office Wi-Fi — prove it works from anywhere):
   message the bot: *"when is the next meeting?"* or *"who do I ask about
   plot fees?"*
3. Expect: an answer drawn from your `documents/` folder. Wrong answer?
   The document probably isn't in `documents/` yet — drop it in and ask
   again (the hub re-indexes automatically).

---

## 5. Safely unlocking officer tools

The blocked tools (shell, web) are what turn the assistant into a
**powerhouse** — drafting files, running automations that touch documents,
researching online. They are also the reason the default is OFF for members.

**Recommended pattern: two bots, two configs.**

1. Keep the **member bot** locked (default).
2. For the officer bot, copy the config:
   ```
   cp -r data/nanobot data/nanobot-officer        (Windows: xcopy /E /I data\nanobot data\nanobot-officer)
   ```
3. Edit `data/nanobot-officer/config.json`:
   - `"botName": "Club Officer Tool"` (so you can tell them apart in chat)
   - change `"gateway": { "port": 18790 }` to `18791`
   - in `"tools"`: set `"exec": { "enable": true }` and/or
     `"web": { "enable": true }`
   - in `"channels"`, put **only officer user IDs** in `allowFrom` — never
     leave an officer bot open to the whole club
4. Start it alongside the main agent (it coexists on its own port):
   ```
   .venv-agent/bin/nanobot gateway --foreground -c data/nanobot-officer/config.json -w data/nanobot-officer/workspace
   ```
   (Windows: `.venv-agent\Scripts\nanobot.exe` with the same arguments.)

**Rules of thumb:**
- Officer tokens/configs live in the same sealed envelope as the other hub
  passwords — whoever has the token *is* the bot.
- When an officer leaves the club: remove their user ID from `allowFrom`
  (Telegram/Discord IDs), not the whole bot.
- Confidential material (closed-session minutes, personal data) should not be
  discussed through any chat channel — messages transit Telegram/Discord
  servers. Officers handle those on the office PC directly.

---

## 6. Useful automations (examples to ask the agent)

Once an officer bot is running with tools enabled, try asking it:

- *"Every Monday at 9am, summarize any documents added to documents/ last
  week and send me the summary here."*
- *"Draft a reminder message about plot fees for the newsletter."*
- *"What changed in the bylaws between the copy from March and now?"*
  (after you drop both versions into `documents/`)

Schedules created through chat persist across restarts of the agent.

---

## 7. If something goes wrong

| Symptom | Fix |
|---|---|
| Bot doesn't answer | `http://localhost:8090/status` → is the agent UP? Then check the token in `data/nanobot/config.json` and restart with `start-hub`. |
| "chat live on: none" at startup | Token pasted but `"enabled"` still `false`, or token has a typo/space. |
| Telegram bot responds, Discord doesn't | Discord needs **Message Content Intent** enabled in the developer portal (step 3). |
| Agent answers "I can't do that" | That's the safety lock — the tool is disabled. Officers: see section 5. |
| Everything else | `python3 doctor.py` — it repairs the agent and regenerates its config **while preserving your chat tokens**. |

---

## 8. Quick reference

| Thing | Where |
|---|---|
| Agent config (both channels) | `data/nanobot/config.json` |
| Agent logs | `data/agent.log` |
| Restart everything | `stop-hub` → `start-hub` |
| Health/status | `http://localhost:8090/status` |
| Token created with | Telegram: @BotFather · Discord: discord.com/developers/applications |
| Repair | `python3 doctor.py` |
