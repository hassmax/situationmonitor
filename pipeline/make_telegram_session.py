#!/usr/bin/env python3
"""Run this once on your own computer to create the TG_SESSION secret.

    pip install telethon
    python3 pipeline/make_telegram_session.py

It asks for your API ID and hash (from https://my.telegram.org > API development tools),
then your phone number and the login code Telegram sends you. It then copies the
session string straight to your clipboard, so you can paste it into a GitHub secret
named TG_SESSION without selecting text in the terminal.

Treat that string like a password. Anyone holding it can use your Telegram account.
"""
import platform
import subprocess

from telethon.sessions import StringSession
from telethon.sync import TelegramClient

api_id = int(input("API ID: ").strip())
api_hash = input("API hash: ").strip()

with TelegramClient(StringSession(), api_id, api_hash) as client:
    session = client.session.save()
    me = client.get_me()
    print(f"\nLogged in as {getattr(me, 'username', None) or me.first_name}.")

# Round-trip check: make sure the string loads before handing it over.
StringSession(session)
print(f"Session string is {len(session)} characters and starts with {session[0]!r}.")

copier = {"Darwin": ["pbcopy"], "Windows": ["clip"]}.get(platform.system())
copied = False
if copier:
    try:
        subprocess.run(copier, input=session.encode(), check=True)
        copied = True
    except (OSError, subprocess.CalledProcessError):
        pass

if copied:
    print("\nCopied to your clipboard. In GitHub, open the TG_SESSION secret and paste (Cmd+V / Ctrl+V).")
    print("Nothing was saved to disk.")
else:
    print("\nCould not reach the clipboard. Copy the line below exactly:\n")
    print(session)
