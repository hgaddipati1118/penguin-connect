#!/usr/bin/env python3
"""
One-time Telegram authentication for PenguinConnect.

Run interactively in your terminal:
    python scripts/telegram_auth.py

This will prompt for your phone number and verification code,
then save the session to ~/penguin-connect-data/telegram.session

Prerequisites:
    1. Go to https://my.telegram.org → API development tools
    2. Create an app (title: "PenguinConnect", platform: "Other")
    3. Set env vars:
       PENGUIN_CONNECT_TELEGRAM_API_ID=<your api_id>
       PENGUIN_CONNECT_TELEGRAM_API_HASH=<your api_hash>
"""

import os
import sys
from pathlib import Path

# Load .env if available
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

api_id_raw = os.environ.get("PENGUIN_CONNECT_TELEGRAM_API_ID")
api_hash = os.environ.get("PENGUIN_CONNECT_TELEGRAM_API_HASH")

if not api_id_raw or not api_hash:
    print("Error: PENGUIN_CONNECT_TELEGRAM_API_ID and PENGUIN_CONNECT_TELEGRAM_API_HASH must be set.")
    print("Get them from https://my.telegram.org → API development tools")
    sys.exit(1)

api_id = int(api_id_raw)

DATA_DIR = Path.home() / "penguin-connect-data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
session_path = os.environ.get(
    "PENGUIN_CONNECT_TELEGRAM_SESSION_PATH",
    str(DATA_DIR / "telegram"),
)

from telethon.sync import TelegramClient

print("Authenticating with Telegram...")
client = TelegramClient(session_path, api_id, api_hash)
client.start()  # Interactive: prompts for phone + code + optional 2FA

me = client.get_me()
name = f"{me.first_name or ''} {me.last_name or ''}".strip()
print(f"\nAuthenticated as: {name} (@{me.username or 'N/A'})")
print(f"Session saved to: {session_path}.session")

# Quick test — list first 5 dialogs
print("\nRecent chats:")
for dialog in client.iter_dialogs(limit=5):
    print(f"  {dialog.id}: {dialog.name} ({dialog.unread_count} unread)")

client.disconnect()
print("\nDone! Telegram is ready for PenguinConnect.")
