"""Messaging channel adapters for PenguinConnect."""

from __future__ import annotations

from .base import MessagingChannelAdapter
from .imessage import IMessageChannelAdapter
from .slack import SlackChannelAdapter
from .telegram import TelegramChannelAdapter
from .whatsapp import WhatsAppChannelAdapter

_APPLE_MESSAGES_ADAPTER = IMessageChannelAdapter()
_WHATSAPP_ADAPTER = WhatsAppChannelAdapter()
_TELEGRAM_ADAPTER = TelegramChannelAdapter()
_SLACK_ADAPTER = SlackChannelAdapter()

_CHANNELS: dict[str, MessagingChannelAdapter] = {
    "imessage": _APPLE_MESSAGES_ADAPTER,
    "apple_messages": _APPLE_MESSAGES_ADAPTER,
    "sms": _APPLE_MESSAGES_ADAPTER,
    "rcs": _APPLE_MESSAGES_ADAPTER,
    "whatsapp": _WHATSAPP_ADAPTER,
    "telegram": _TELEGRAM_ADAPTER,
    "slack": _SLACK_ADAPTER,
}


def get_channel_adapter(provider: str = "imessage") -> MessagingChannelAdapter:
    normalized = (provider or "imessage").strip().lower()
    try:
        return _CHANNELS[normalized]
    except KeyError as exc:
        raise KeyError(f"Unknown messaging provider: {normalized}") from exc


__all__ = [
    "MessagingChannelAdapter",
    "IMessageChannelAdapter",
    "WhatsAppChannelAdapter",
    "TelegramChannelAdapter",
    "SlackChannelAdapter",
    "get_channel_adapter",
]
