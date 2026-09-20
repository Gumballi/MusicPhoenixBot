# MusicPhoenixBot · Poke's PyTgCalls sidecar

Userbot that turns a **spare Telegram account** into a group–voice-chat
streamer.  The main PTB bot never touches a voice chat — the spare account's
Pyrogram session + PyTgCalls do all the VC work (exactly the architecture "
Poke" 🌴 described, and the reason Phoenix's main bot needs no voice powers).

**Why non-YouTube by default:** your Render datacenter IP gets 403'd by
YouTube's googlevideo CDN.  This sidecar deliberately refuses YouTube/gated
playable URLs and streams SoundCloud/Bandcamp/SoundCloud-direct-http instead
— same "IP lesson" that already fixed Phoenix's TikTok/Instagram carousels.
Nothing here fixes YouTube: it routes *around* it.

## Boot (Render)

| env                     | meaning                                      |
|-------------------------|----------------------------------------------|
| `API_ID` / `API_HASH`   | your Telegram app credentials                |
| `STRING_SESSION`        | Pyrogram string session of the **spare** acc |
| `SESSION_TIMEOUT`...    | (optional floor)                             |
| `ADMIN_IDS`             | comma list allowed to send /play etc.        |
| `MAX_QUEUE`             | max queued tracks (default 50)               |

Run: `python main.py`   (Render: `python main.py`)

## Commands (in any group the spare account is in)

- `/play <query|url>` — resolve + enqueue
- `/skip`, `/stop`, `/pause`, `/resume`, `/queue`
- Only `ADMIN_IDS` may issue the above.

## Layout
`tg_bot/config.py`   dots-in env → Python objects
`tg_bot/main.py`     Pyrogram client + boot
`tg_bot/resolver.py` yt-dlp: query → ONE direct audio URL (non-gated)
`tg_bot/player.py`   MusicPlayer: PyTgCalls stream + queue loop
