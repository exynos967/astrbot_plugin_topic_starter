from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from .models import as_bool, as_float, as_int, as_non_empty_text, parse_time_hhmm


@dataclass(slots=True)
class QuietHours:
    enabled: bool = False
    start_minutes: int = 23 * 60
    end_minutes: int = 8 * 60

    def is_active(self, *, now: datetime | None = None) -> bool:
        if not self.enabled:
            return False

        moment = now or datetime.now()
        current = moment.hour * 60 + moment.minute

        if self.start_minutes == self.end_minutes:
            return True

        if self.start_minutes < self.end_minutes:
            return self.start_minutes <= current < self.end_minutes

        return current >= self.start_minutes or current < self.end_minutes


@dataclass(slots=True)
class PluginSettings:
    enabled: bool
    tick_interval_seconds: int
    trigger_probability: float
    cooldown_seconds: int
    silence_seconds: int
    message_window_size: int
    auto_bind_on_message: bool
    group_filter_mode: str
    group_filter_ids: list[str]
    max_message_chars: int
    chat_provider_id: str
    fallback_topics: list[str]
    quiet_hours: QuietHours

    def is_group_allowed(self, group_id: str) -> bool:
        normalized_group_id = as_non_empty_text(group_id, default="")
        if not normalized_group_id:
            return True

        mode = self.group_filter_mode
        if mode == "none":
            return True

        matched = normalized_group_id in self.group_filter_ids
        if mode == "whitelist":
            return matched
        if mode == "blacklist":
            return not matched
        return True

    @classmethod
    def from_config(cls, config: Mapping[str, object] | None) -> "PluginSettings":
        raw = dict(config or {})

        quiet_hours_raw = raw.get("quiet_hours")
        if isinstance(quiet_hours_raw, Mapping):
            quiet_hours = QuietHours(
                enabled=as_bool(quiet_hours_raw.get("enabled"), default=False),
                start_minutes=parse_time_hhmm(quiet_hours_raw.get("start"), default_minutes=23 * 60),
                end_minutes=parse_time_hhmm(quiet_hours_raw.get("end"), default_minutes=8 * 60),
            )
        else:
            quiet_hours = QuietHours()

        fallback_topics = _normalize_topic_lines(raw.get("fallback_topics"))

        tick_interval = max(as_int(raw.get("tick_interval_seconds"), default=300), 60)
        trigger_probability = min(max(as_float(raw.get("trigger_probability"), default=0.3), 0.0), 1.0)
        cooldown_seconds = max(as_int(raw.get("cooldown_seconds"), default=1800), 0)
        silence_seconds = max(as_int(raw.get("silence_seconds"), default=600), 0)
        message_window_size = max(as_int(raw.get("message_window_size"), default=20), 1)
        group_filter_mode = _normalize_group_filter_mode(raw.get("group_filter_mode"))
        group_filter_ids = _normalize_group_ids(raw.get("group_filter_ids"))
        max_message_chars = max(as_int(raw.get("max_message_chars"), default=120), 20)

        return cls(
            enabled=as_bool(raw.get("enabled"), default=True),
            tick_interval_seconds=tick_interval,
            trigger_probability=trigger_probability,
            cooldown_seconds=cooldown_seconds,
            silence_seconds=silence_seconds,
            message_window_size=message_window_size,
            auto_bind_on_message=as_bool(raw.get("auto_bind_on_message"), default=True),
            group_filter_mode=group_filter_mode,
            group_filter_ids=group_filter_ids,
            max_message_chars=max_message_chars,
            chat_provider_id=as_non_empty_text(raw.get("chat_provider_id"), default=""),
            fallback_topics=fallback_topics,
            quiet_hours=quiet_hours,
        )


def _normalize_topic_lines(raw: object) -> list[str]:
    if raw is None:
        return []

    lines: list[str] = []

    if isinstance(raw, str):
        text = as_non_empty_text(raw)
        if text:
            lines.append(text)
        return lines

    if isinstance(raw, list):
        for item in raw:
            text = as_non_empty_text(item)
            if text:
                lines.append(text)

    return lines


def _normalize_group_filter_mode(raw: object) -> str:
    mode = as_non_empty_text(raw, default="none").lower()
    if mode in {"none", "whitelist", "blacklist"}:
        return mode
    return "none"


def _normalize_group_ids(raw: object) -> list[str]:
    tokens: list[str] = []

    if raw is None:
        return tokens

    if isinstance(raw, str):
        tokens.extend(_split_group_tokens(raw))
    elif isinstance(raw, list):
        for item in raw:
            text = as_non_empty_text(item, default="")
            if text:
                tokens.extend(_split_group_tokens(text))

    normalized: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        value = token.strip()
        if value.lower().startswith("group:"):
            value = value.split(":", 1)[1].strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)

    return normalized


def _split_group_tokens(text: str) -> list[str]:
    return [token for token in re.split(r"[,\s，]+", text) if token]
