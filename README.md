# Telegram Content Compliance Bot (Core)

Asynchronous moderation helper bot based on `aiogram 3.x` and `Telethon`.

## Features

- Session pool loading from `sessions/*.session`
- Session validation (`is_user_authorized`, `get_me`) and archiving broken sessions to `sessions/archive/`
- Proxy pool loading from `proxies/*.txt` with noisy text parsing via regex
- Static/Rotate proxy modes
- Exponential backoff (`1,2,4,8,16`) for connection and proxy-retry flows
- Queue-based report processing via `asyncio.Queue`
- FloodWait handling per session without stopping the whole queue
- Admin whitelist support
- Dry status logs to admin chat (`SessionX -> Success`)

## Commands

- `/start`
- `/status`
- `/process [url] [reason] [count]`
- `/info [username_or_link]`
- `/reload`

## Reasons

- `spam`
- `violence`
- `pornography`
- `childabuse`
- `copyright`
- `other`

## Setup

1. Create and activate venv.
2. Install dependencies:
   - `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill credentials.
4. Put Telegram `.session` files into `sessions/`.
5. Put proxy `.txt` files into `proxies/`.
6. Run:
   - `python main.py`
