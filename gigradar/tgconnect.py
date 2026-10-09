"""Connect a Telegram bot: verify the token (getMe), find the chat id (getUpdates), send a test message.

Used by `python -m gigradar.cli telegram-connect`. Stdlib only. The bot token is part of every API URL:
it is never printed or logged here, and it is scrubbed from any error text before that text leaves.
The user first writes any message to the bot in Telegram; getUpdates then shows that chat.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass

from gigradar.telegram import API, PostFn, TelegramError, TelegramNotifier

TOKEN_PATTERN = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{20,}$")
CHAT_ID_PATTERN = re.compile(r"^-?\d{1,20}$")


class ConnectError(Exception):
    """Why connecting failed. `code` is machine-readable; the message never contains the token."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Connected:
    bot_username: str
    chat_id: str
    chat_name: str | None


def call(token: str, method: str, params: dict[str, str], post: PostFn) -> object:
    """One Bot API call; returns the `result` field. Raises ConnectError (token scrubbed)."""
    try:
        status, raw = post(f"{API}/bot{token}/{method}", urllib.parse.urlencode(params).encode())
    except Exception as exc:  # noqa: BLE001  network errors: re-raise without the token
        raise ConnectError("network", f"{method} failed: {type(exc).__name__}: {str(exc).replace(token, '<bot-token>')}") from None
    try:
        reply = json.loads(raw)
    except ValueError:
        reply = {}
    if status != 200 or not reply.get("ok"):
        description = str(reply.get("description") or raw[:200].decode("utf-8", "replace")).replace(token, "<bot-token>")
        code = "bad_token" if status in (401, 404) else "telegram"
        raise ConnectError(code, f"{method}: HTTP {status}: {description}")
    return reply.get("result")


def get_me(token: str, post: PostFn) -> str:
    """The bot's username; proves the token works."""
    result = call(token, "getMe", {}, post)
    if not isinstance(result, dict) or not result.get("username"):
        raise ConnectError("telegram", "getMe: unexpected reply")
    return str(result["username"])


def find_chat(token: str, post: PostFn) -> tuple[str, str | None]:
    """(chat id, name) of the newest private chat that wrote to the bot."""
    updates = call(token, "getUpdates", {"limit": "100", "allowed_updates": '["message"]'}, post)
    for update in reversed(updates if isinstance(updates, list) else []):
        chat = (update.get("message") or {}).get("chat") or {}
        if chat.get("type") == "private" and "id" in chat:
            name = chat.get("first_name") or chat.get("username")
            return str(chat["id"]), str(name) if name else None
    raise ConnectError("no_message", "no message to the bot found: open the bot in Telegram, press Start, "
                                     "write 'hi', then try again")


def connect(token: str, chat_id: str | None, post: PostFn) -> Connected:
    """Verify the token, discover (or accept) the chat id, send one test message."""
    if not TOKEN_PATTERN.match(token):
        raise ConnectError("bad_token", "that does not look like a bot token (expected 123456:ABC...)")
    if chat_id is not None and not CHAT_ID_PATTERN.match(chat_id):
        raise ConnectError("bad_chat_id", "chat id must be a number")
    username = get_me(token, post)
    name = None
    if chat_id is None:
        chat_id, name = find_chat(token, post)
    try:
        TelegramNotifier(token, chat_id, post).notify("gigradar", "Connected. New Upwork jobs will arrive in this chat.")
    except TelegramError as exc:
        raise ConnectError("send_failed", str(exc)) from None
    return Connected(username, chat_id, name)
