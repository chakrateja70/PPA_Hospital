"""Shared helpers for parsing Meta WhatsApp webhook payloads.

Both the router (``main.py``) and the THS service (``src/server.py``) receive
the same Meta webhook envelope, so the ``entry → changes → value`` navigation
lives here in exactly one place instead of being copy-pasted in each entry point.
"""

from __future__ import annotations


def get_value(payload: dict) -> dict:
    """Return ``entry[0].changes[0].value`` from a webhook payload, or ``{}``."""
    try:
        return payload["entry"][0]["changes"][0]["value"] or {}
    except (KeyError, IndexError, TypeError):
        return {}


def get_phone_number_id(payload: dict) -> str:
    """Return the receiving number's ``phone_number_id`` (stripped), or ``""``."""
    return get_value(payload).get("metadata", {}).get("phone_number_id", "").strip()


def get_first_message(payload: dict) -> dict:
    """Return the first message object in the payload, or ``{}`` if there is none."""
    messages = get_value(payload).get("messages") or []
    return messages[0] if messages else {}
