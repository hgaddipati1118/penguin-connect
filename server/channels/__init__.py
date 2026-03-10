"""Messaging channel adapters for PenguinConnect."""

from __future__ import annotations

from .base import MessagingChannelAdapter
from .imessage import IMessageChannelAdapter

_CHANNELS: dict[str, MessagingChannelAdapter] = {
    "imessage": IMessageChannelAdapter(),
}


def get_channel_adapter(provider: str = "imessage") -> MessagingChannelAdapter:
    normalized = (provider or "imessage").strip().lower()
    try:
        return _CHANNELS[normalized]
    except KeyError as exc:
        raise KeyError(f"Unknown messaging provider: {normalized}") from exc


__all__ = ["MessagingChannelAdapter", "IMessageChannelAdapter", "get_channel_adapter"]
