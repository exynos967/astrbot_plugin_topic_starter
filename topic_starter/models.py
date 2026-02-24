from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(slots=True)
class TopicDraft:
    title: str
    description: str
    priority: int = 1
    enabled: bool = True


@dataclass(slots=True)
class TopicRecord:
    id: int
    title: str
    description: str
    priority: int
    enabled: bool
    use_count: int
    last_used_at: float
    created_at: float
    updated_at: float


@dataclass(slots=True)
class StreamTarget:
    unified_msg_origin: str
    session_name: str
    platform: str
    is_group: bool
    active: bool
    last_user_message_ts: float
    last_bot_initiate_ts: float
    created_at: float
    updated_at: float


@dataclass(slots=True)
class MessageSnapshot:
    unified_msg_origin: str
    sender_id: str
    sender_name: str
    content: str
    created_at: float


@dataclass(slots=True)
class InitiationDecision:
    should_send: bool
    reason: str


@dataclass(slots=True)
class TopicSeed:
    title: str
    description: str


@dataclass(slots=True)
class SelectedTopic:
    topic_id: Optional[int]
    title: str
    description: str


@dataclass(slots=True)
class SendCandidate:
    stream: StreamTarget
    topic: TopicSeed
    content: str


def as_bool(value: object, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def as_non_empty_text(value: object, *, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def as_int(value: object, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_float(value: object, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_time_hhmm(value: object, *, default_minutes: int) -> int:
    text = as_non_empty_text(value, default="")
    if not text:
        return default_minutes

    parts = text.split(":")
    if len(parts) != 2:
        return default_minutes

    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return default_minutes

    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return default_minutes

    return hour * 60 + minute
