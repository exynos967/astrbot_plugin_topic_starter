from __future__ import annotations

import random
from datetime import datetime
from typing import Callable

from .config import PluginSettings
from .models import InitiationDecision, SelectedTopic, TopicRecord, TopicSeed


class InitiationDecisionEngine:
    def __init__(self, random_provider: Callable[[], float] | None = None):
        self._random_provider = random_provider or random.random

    def should_initiate(
        self,
        stream,
        settings: PluginSettings,
        *,
        now: float,
        force: bool = False,
    ) -> InitiationDecision:
        if not settings.enabled:
            return InitiationDecision(False, "plugin_disabled")

        if not stream.active:
            return InitiationDecision(False, "stream_inactive")

        if settings.quiet_hours.is_active(now=datetime.fromtimestamp(now)) and not force:
            return InitiationDecision(False, "quiet_hours")

        if force:
            return InitiationDecision(True, "force")

        if stream.last_bot_initiate_ts > 0 and (now - stream.last_bot_initiate_ts) < settings.cooldown_seconds:
            return InitiationDecision(False, "cooldown")

        if stream.last_user_message_ts > 0 and (now - stream.last_user_message_ts) < settings.silence_seconds:
            return InitiationDecision(False, "conversation_active")

        random_value = self._random_provider()
        if random_value >= settings.trigger_probability:
            return InitiationDecision(False, "random_gate")

        return InitiationDecision(True, "ready")


class TopicSelectionService:
    def pick_topic(
        self,
        *,
        topics: list[TopicRecord],
        fallback_lines: list[str],
        now: float,
    ) -> SelectedTopic | None:
        if topics:
            weighted_topics: list[tuple[float, TopicRecord]] = []
            for topic in topics:
                staleness_hours = max(now - topic.last_used_at, 0.0) / 3600.0 if topic.last_used_at else 24.0
                freshness_boost = min(staleness_hours / 24.0, 2.0)
                weight = max(topic.priority, 1) * (1.0 + freshness_boost)
                weighted_topics.append((weight, topic))

            total = sum(weight for weight, _ in weighted_topics)
            if total <= 0:
                choice = weighted_topics[0][1]
            else:
                pick = random.random() * total
                acc = 0.0
                choice = weighted_topics[-1][1]
                for weight, topic in weighted_topics:
                    acc += weight
                    if pick <= acc:
                        choice = topic
                        break

            return SelectedTopic(topic_id=choice.id, title=choice.title, description=choice.description)

        parsed = [self._parse_fallback_line(line) for line in fallback_lines]
        candidates = [seed for seed in parsed if seed is not None]
        if not candidates:
            return None

        fallback_seed = random.choice(candidates)
        return SelectedTopic(topic_id=None, title=fallback_seed.title, description=fallback_seed.description)

    @staticmethod
    def _parse_fallback_line(line: str) -> TopicSeed | None:
        stripped = line.strip()
        if not stripped:
            return None

        for delimiter in ("|", "｜"):
            if delimiter in stripped:
                title, desc = stripped.split(delimiter, 1)
                title = title.strip()
                desc = desc.strip()
                if title and desc:
                    return TopicSeed(title=title, description=desc)

        return TopicSeed(title=stripped, description="")


class ContentRenderingService:
    def render_fallback_content(self, *, topic: TopicSeed, recent_dialogue: list[str]) -> str:
        if recent_dialogue:
            context = recent_dialogue[0]
            return (
                f"看到大家刚刚聊到：{context[:40]}...\n"
                f"想延展一个话题：{topic.title}\n"
                f"{topic.description}".strip()
            )

        lines = [f"换个话题聊聊：{topic.title}"]
        if topic.description:
            lines.append(topic.description)
        lines.append("大家怎么看？")
        return "\n".join(lines)
