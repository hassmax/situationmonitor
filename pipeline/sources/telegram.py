"""Read recent messages from public Telegram channels with your own Telegram login.

Needs three secrets: TG_API_ID and TG_API_HASH (from https://my.telegram.org) and
TG_SESSION (created once on your computer with pipeline/make_telegram_session.py).
Without them, Telegram is skipped and the other sources still run.
"""
from __future__ import annotations

import asyncio
import os

from common import health_fail, health_ok, log, make_item


def fetch(sources: list[dict], state: dict, health: dict) -> list[dict]:
    if not sources:
        return []
    api_id = os.environ.get("TG_API_ID", "").strip()
    api_hash = os.environ.get("TG_API_HASH", "").strip()
    session = os.environ.get("TG_SESSION", "").strip()
    if not (api_id and api_hash and session):
        for src in sources:
            sid = f"tg:{src['username']}"
            health[sid] = health_fail(src.get("name") or src["username"], "telegram",
                                      "Telegram secrets not set", health.get(sid))
        return []
    problem = None
    if not api_id.isdigit():
        problem = "TG_API_ID must be only the numeric App api_id from my.telegram.org"
    elif len(api_hash) != 32:
        problem = "TG_API_HASH should be the 32-character App api_hash from my.telegram.org"
    if problem:
        log(f"[telegram] {problem}")
        for src in sources:
            sid = f"tg:{src['username']}"
            health[sid] = health_fail(src.get("name") or src["username"], "telegram", problem, health.get(sid))
        return []
    try:
        return asyncio.run(_fetch(sources, state, health, int(api_id), api_hash, session))
    except Exception as exc:  # noqa: BLE001
        log(f"[telegram] run failed: {exc}")
        for src in sources:
            sid = f"tg:{src['username']}"
            if sid not in health or health[sid].get("ok") is not True:
                health[sid] = health_fail(src.get("name") or src["username"], "telegram", exc, health.get(sid))
        return []


async def _fetch(sources, state, health, api_id, api_hash, session_str):
    from telethon import TelegramClient, errors
    from telethon.sessions import StringSession
    from telethon.tl.types import InputPeerChannel

    try:
        client = TelegramClient(StringSession(session_str), api_id, api_hash, receive_updates=False)
    except TypeError:  # older Telethon without receive_updates
        client = TelegramClient(StringSession(session_str), api_id, api_hash)

    items: list[dict] = []
    # Resolving @usernames is rate-limited by Telegram, so resolved peers are cached in state.
    peers = state.setdefault("tg_peers", {})
    last_ids = state.setdefault("tg_last_id", {})

    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("session is not authorised; create a new TG_SESSION")
        for src in sources:
            username = src["username"].lstrip("@")
            sid = f"tg:{username}"
            name = src.get("name") or username
            try:
                if username in peers:
                    peer = InputPeerChannel(int(peers[username][0]), int(peers[username][1]))
                else:
                    peer = await client.get_input_entity(username)
                    if not isinstance(peer, InputPeerChannel):
                        raise ValueError("not a public channel")
                    peers[username] = [peer.channel_id, peer.access_hash]
                    await asyncio.sleep(2)
                min_id = int(last_ids.get(username, 0))
                messages = await client.get_messages(peer, limit=40, min_id=min_id)
                latest = None
                count = 0
                for m in messages:
                    text = getattr(m, "message", None) or ""
                    if not text.strip() or not m.date:
                        continue
                    url = f"https://t.me/{username}/{m.id}"
                    items.append(make_item(src, "telegram", sid, url, text, m.date, uid=f"{username}/{m.id}"))
                    count += 1
                    latest = m.date if latest is None or m.date > latest else latest
                if messages:
                    last_ids[username] = max(min_id, max(m.id for m in messages))
                health[sid] = health_ok(name, "telegram", latest, count, health.get(sid))
            except errors.FloodWaitError as exc:
                health[sid] = health_fail(name, "telegram", f"Telegram asked to wait {exc.seconds}s", health.get(sid))
            except (errors.ChannelPrivateError, errors.UsernameNotOccupiedError,
                    errors.UsernameInvalidError, errors.ChannelInvalidError, ValueError) as exc:
                peers.pop(username, None)
                health[sid] = health_fail(name, "telegram", exc, health.get(sid))
            except Exception as exc:  # noqa: BLE001
                log(f"[telegram] {username}: {exc}")
                health[sid] = health_fail(name, "telegram", exc, health.get(sid))
            await asyncio.sleep(1)
    finally:
        await client.disconnect()
    return items
