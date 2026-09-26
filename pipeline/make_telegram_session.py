#!/usr/bin/env python3
"""Run this once on your own computer to create the TG_SESSION secret.

    pip install telethon
    python pipeline/make_telegram_session.py

It asks for your API ID and hash (from https://my.telegram.org > API development tools),
then your phone number and the login code Telegram sends you. It prints a long string:
paste that into a GitHub secret named TG_SESSION.

Treat that string like a password. Anyone holding it can use your Telegram account.
Consider using a secondary Telegram account for this.
"""
from telethon.sessions import StringSession
from telethon.sync import TelegramClient

api_id = int(input("API ID: ").strip())
api_hash = input("API hash: ").strip()

with TelegramClient(StringSession(), api_id, api_hash) as client:
    session = client.session.save()
    me = client.get_me()
    print(f"\nLogged in as {getattr(me, 'username', None) or me.first_name}.")
    print("\nTG_SESSION value (copy everything on the next line):\n")
    print(session)
