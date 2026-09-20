# TheRealPhoenixBot · Music Sidecar (PyTgCalls)

Group voice-chat music streamed by a **spare Telegram account** — the sidecar
architecture behind TheRealPhoenixBot's VC powers. The main bot never needs
voice capabilities; this self-contained userbot does all the voice-chat work.

> **Why a sidecar?** Telegram's bot API has no voice-chat surface — a `bot`
> token cannot join a group call. Only an MTProto userbot (Pyrogram +
> PyTgCalls) can stream into a voice chat. So this service runs as a *spare
> account* that joins the group's VC and plays the queue. The main bot just
> forwards commands; it stays clean of voice-chat powers.

---

## Features

- **Direct-stream playback** — resolves a query to ONE direct audio URL and
  streams it with FFmpeg/PyTgCalls; no full-file downloads, no disk.
- **Queue** — `/play`, `/skip`, `/stop`, `/pause`, `/resume`, `/queue`.
- **Datacenter-safe sources** — this sidecar is *designed around the Render
  datacenter IP*: YouTube's playable URLs are refused at the resolver, and
  we target hosts that don't gate datacenter traffic (SoundCloud, Bandcamp,
  direct HTTP). The architectural lesson that shaped TheRealPhoenixBot:
  *"render IPs 403 on gated hosts, so never ship a gated url to the player."*

---

## Commands

| Command | Action |
|---|---|
| `/play <query\|url>` | Resolve & enqueue a track |
| `/skip` | Skip current |
| `/stop` | Stop + leave voice chat |
| `/pause` / `/resume` | Pause/resume |
| `/queue` | Show queued tracks |

Admin-only (controlled by `ADMIN_IDS`).

---

## Env vars

| Var | Required | Notes |
|---|---|---|
| `API_ID` | yes | Your Telegram app id |
| `API_HASH` | yes | Your Telegram app hash |
| `STRING_SESSION` | yes | Pyrogram string session of the **spare** account |
| `ADMIN_IDS` | yes | comma-separated user ids allowed to control |
| `MAX_QUEUE` | no | default 50 |

---

## Run

```
pip install -r requirements.txt
python main.py
```

## Deploy (Render)

- **Runtime: Python 3.9** (PyTgCalls' `tgcalls` ships prebuilt cp39 wheels;
  a newer Python would try to compile the native binding from source).
- Build: `pip install -r requirements.txt`
- Start: `python main.py`
- Set the env vars above from the Render dashboard.

---

## Repo layout

```
tg_bot/config.py     env -> config objects + logger
tg_bot/resolver.py   query -> ONE direct audio URL (gated-URL refuser)
tg_bot/player.py     PyTgCalls sidecar: queue loop, stream, pause/resume
tg_bot/main.py       Pyrogram client boot
```
