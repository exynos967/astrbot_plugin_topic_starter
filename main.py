from __future__ import annotations

import asyncio
import time
from typing import Any, Mapping

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register

try:
    from astrbot.api import AstrBotConfig
except Exception:  # pragma: no cover - runtime compatibility fallback
    AstrBotConfig = dict  # type: ignore[misc,assignment]

try:
    from .topic_starter import (
        AstrBotKVStore,
        ContentRenderingService,
        InitiationDecisionEngine,
        PluginSettings,
        SelectedTopic,
        TopicDraft,
        TopicSelectionService,
    )
except ImportError:  # pragma: no cover - plugin runtime may load as top-level module
    from topic_starter import (
        AstrBotKVStore,
        ContentRenderingService,
        InitiationDecisionEngine,
        PluginSettings,
        SelectedTopic,
        TopicDraft,
        TopicSelectionService,
    )


DEFAULT_FALLBACK_TOPICS = [
    "最近最实用的 AI 工具你推荐哪个？|可以从工作、学习或娱乐角度聊聊。",
    "最近有哪部电影或剧值得补？|聊聊你最推荐的一部和理由。",
    "你现在最想提升的一项能力是什么？|为什么会选它？",
    "如果周末只做一件让你恢复精力的事，会选什么？|分享你的方式。",
]


@register(
    "astrbot_plugin_topic_starter",
    "薄暝",
    "主动话题发起、会话跟踪、可配置调度",
    "0.1.0",
    "https://github.com/AstrBotDevs/AstrBot",
)
class TopicStarterPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config if config is not None else {}

        self._store = AstrBotKVStore(self.get_kv_data, self.put_kv_data, self.delete_kv_data)

        self._decision_engine = InitiationDecisionEngine()
        self._topic_selector = TopicSelectionService()
        self._content_renderer = ContentRenderingService()

        self._shutdown_event = asyncio.Event()
        self._tick_lock = asyncio.Lock()
        self._tick_task = self._spawn_scheduler_task()

    async def terminate(self):
        self._shutdown_event.set()

        if self._tick_task is not None:
            self._tick_task.cancel()
            try:
                await self._tick_task
            except asyncio.CancelledError:
                pass

    @filter.command("topic_help")
    async def topic_help(self, event: AstrMessageEvent):
        """Topic Starter 帮助"""
        lines = [
            "Topic Starter 指令：",
            "/topic_bind 绑定当前会话为主动发言目标",
            "/topic_unbind 解除当前会话绑定",
            "/topic_status 查看当前状态",
            "/topic_create 标题|描述 创建话题",
            "/topic_list 查看话题列表",
            "/topic_delete 话题ID 删除话题",
            "/topic_initiate 立即在当前会话触发一次主动发言",
        ]
        yield event.plain_result("\n".join(lines))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("topic_bind")
    async def topic_bind(self, event: AstrMessageEvent):
        """绑定当前会话"""
        now = time.time()
        umo = event.unified_msg_origin
        await self._store.bind_stream(
            unified_msg_origin=umo,
            session_name=self._build_session_name(event),
            platform=self._safe_platform_name(event),
            is_group=bool(self._safe_group_id(event)),
            now=now,
        )
        await self._store.touch_user_message(umo, now=now)

        yield event.plain_result("✅ 已绑定当前会话，插件将在满足条件时主动发起话题。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("topic_unbind")
    async def topic_unbind(self, event: AstrMessageEvent):
        """解绑当前会话"""
        ok = await self._store.deactivate_stream(event.unified_msg_origin)
        if ok:
            yield event.plain_result("✅ 已解绑当前会话。")
            return

        yield event.plain_result("ℹ️ 当前会话尚未绑定。")

    @filter.command("topic_status")
    async def topic_status(self, event: AstrMessageEvent):
        """查看插件状态"""
        settings = self._settings()
        stream = await self._store.get_stream(event.unified_msg_origin)
        active_streams = await self._store.list_active_streams()
        topics = await self._store.list_topics(enabled_only=True)

        lines = [
            "Topic Starter 状态：",
            f"- 全局启用: {'是' if settings.enabled else '否'}",
            f"- 绑定会话数: {len(active_streams)}",
            f"- 启用话题数: {len(topics)}",
            f"- 调度间隔: {settings.tick_interval_seconds}s",
            f"- 触发概率: {settings.trigger_probability:.2f}",
            f"- 冷却时间: {settings.cooldown_seconds}s",
            f"- 静默阈值: {settings.silence_seconds}s",
            f"- 最大字数: {settings.max_message_chars}",
            f"- 指定模型提供商: {settings.chat_provider_id or '自动使用当前会话'}",
        ]

        if stream is None or not stream.active:
            lines.append("- 当前会话: 未绑定")
        else:
            lines.append(f"- 当前会话: 已绑定({stream.session_name})")
            lines.append(f"- 距上次用户发言: {self._format_elapsed(stream.last_user_message_ts)}")
            lines.append(f"- 距上次主动发言: {self._format_elapsed(stream.last_bot_initiate_ts)}")

        yield event.plain_result("\n".join(lines))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("topic_create")
    async def topic_create(self, event: AstrMessageEvent):
        """创建话题：/topic_create 标题|描述"""
        payload = self._extract_payload(event, "topic_create")
        topic = self._parse_topic_payload(payload)
        if topic is None:
            yield event.plain_result("❌ 格式错误，请使用：/topic_create 标题|描述")
            return

        topic_id = await self._store.create_topic(topic)
        yield event.plain_result(f"✅ 已创建话题 #{topic_id}: {topic.title}")

    @filter.command("topic_list")
    async def topic_list(self, event: AstrMessageEvent):
        """列出话题"""
        topics = await self._store.list_topics(enabled_only=True)
        if not topics:
            yield event.plain_result("📭 当前没有启用的话题，可用 /topic_create 添加。")
            return

        lines = ["📋 已启用话题："]
        for topic in topics:
            lines.append(
                f"#{topic.id} [P{topic.priority}] {topic.title}"
                f" | 已触发{topic.use_count}次"
            )

        yield event.plain_result("\n".join(lines))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("topic_delete")
    async def topic_delete(self, event: AstrMessageEvent):
        """删除话题：/topic_delete 话题ID"""
        payload = self._extract_payload(event, "topic_delete")
        try:
            topic_id = int(payload)
        except ValueError:
            yield event.plain_result("❌ 格式错误，请使用：/topic_delete 话题ID")
            return

        ok = await self._store.delete_topic(topic_id)
        if ok:
            yield event.plain_result(f"✅ 已删除话题 #{topic_id}")
            return

        yield event.plain_result(f"ℹ️ 话题 #{topic_id} 不存在。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("topic_initiate")
    async def topic_initiate(self, event: AstrMessageEvent):
        """手动触发当前会话一次主动发言"""
        await self._ensure_current_stream_bound(event)
        settings = self._settings()

        sent_count, reasons = await self._run_tick(settings=settings, force=True, target_umo=event.unified_msg_origin)
        if sent_count > 0:
            yield event.plain_result("✅ 已在当前会话触发主动发言。")
            return

        reason_text = "、".join(reasons[:2]) if reasons else "未满足发言条件"
        yield event.plain_result(f"ℹ️ 本次未发言：{reason_text}")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def _track_messages(self, event: AstrMessageEvent):
        """跟踪绑定会话消息，用于主动话题上下文"""
        text = (event.message_str or "").strip()
        if not text or text.startswith("/"):
            return

        umo = event.unified_msg_origin
        stream = await self._store.get_stream(umo)
        if stream is None or not stream.active:
            return

        now = time.time()
        settings = self._settings()
        await self._store.touch_user_message(umo, now=now)
        await self._store.append_message(
            unified_msg_origin=umo,
            sender_id=self._safe_sender_id(event),
            sender_name=self._safe_sender_name(event),
            content=text,
            created_at=now,
            max_records=settings.message_window_size,
        )

    async def _scheduler_loop(self):
        while not self._shutdown_event.is_set():
            settings = self._settings()
            try:
                await self._run_tick(settings=settings, force=False)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(f"[astrbot_plugin_topic_starter] scheduler tick failed: {exc}")

            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=settings.tick_interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _run_tick(
        self,
        *,
        settings: PluginSettings,
        force: bool,
        target_umo: str | None = None,
    ) -> tuple[int, list[str]]:
        async with self._tick_lock:
            if not settings.enabled and not force:
                return 0, ["插件未启用"]

            now = time.time()
            streams = await self._streams_for_tick(target_umo)
            enabled_topics = await self._store.list_topics(enabled_only=True)

            sent_count = 0
            reasons: list[str] = []

            for stream in streams:
                decision = self._decision_engine.should_initiate(stream, settings, now=now, force=force)
                if not decision.should_send:
                    reasons.append(f"{stream.session_name}:{decision.reason}")
                    continue

                selected = self._topic_selector.pick_topic(
                    topics=enabled_topics,
                    fallback_lines=settings.fallback_topics,
                    now=now,
                )
                if selected is None:
                    reasons.append(f"{stream.session_name}:no_topic")
                    continue

                content = await self._build_send_content(settings=settings, stream=stream, topic=selected)
                if not content:
                    reasons.append(f"{stream.session_name}:empty_content")
                    continue

                sent = await self._send_message(stream.unified_msg_origin, content)
                if not sent:
                    reasons.append(f"{stream.session_name}:send_failed")
                    continue

                await self._store.mark_bot_initiated(stream.unified_msg_origin, now=now)
                if selected.topic_id is not None:
                    await self._store.mark_topic_used(selected.topic_id, now=now)

                sent_count += 1

            return sent_count, reasons

    async def _build_send_content(self, *, settings: PluginSettings, stream, topic: SelectedTopic) -> str:
        recent_messages = await self._store.list_recent_messages(
            stream.unified_msg_origin,
            limit=settings.message_window_size,
        )
        recent_dialogue = [f"{msg.sender_name}: {msg.content}" for msg in recent_messages]

        fallback_text = self._content_renderer.render_fallback_content(
            topic=topic,
            recent_dialogue=recent_dialogue,
        )
        fallback_text = self._truncate_text(fallback_text, settings.max_message_chars)

        provider_id = await self._resolve_chat_provider_id(
            preferred_provider_id=settings.chat_provider_id,
            umo=stream.unified_msg_origin,
        )

        if not provider_id:
            return fallback_text

        prompt = self._build_llm_prompt(
            topic=topic,
            recent_dialogue=recent_dialogue,
            max_message_chars=settings.max_message_chars,
        )

        try:
            resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt,
            )
            text = self._truncate_text((getattr(resp, "completion_text", "") or "").strip(), settings.max_message_chars)
            if text:
                return text
        except Exception as exc:
            logger.warning(
                f"[astrbot_plugin_topic_starter] llm_generate fallback triggered: {exc}. "
                f"provider_id={provider_id}"
            )

        return fallback_text

    async def _resolve_chat_provider_id(self, *, preferred_provider_id: str, umo: str) -> str:
        if preferred_provider_id:
            return preferred_provider_id

        try:
            return await self.context.get_current_chat_provider_id(umo=umo)
        except Exception:
            return ""

    async def _send_message(self, unified_msg_origin: str, content: str) -> bool:
        try:
            chain = MessageChain().message(content)
            await self.context.send_message(unified_msg_origin, chain)
            return True
        except Exception as exc:
            logger.error(f"[astrbot_plugin_topic_starter] send_message failed: {exc}")
            return False

    def _spawn_scheduler_task(self) -> asyncio.Task | None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                return None

        return loop.create_task(self._scheduler_loop())

    def _settings(self) -> PluginSettings:
        settings = PluginSettings.from_config(self._as_mapping(self.config))
        if not settings.fallback_topics:
            settings.fallback_topics = list(DEFAULT_FALLBACK_TOPICS)
        return settings

    async def _streams_for_tick(self, target_umo: str | None) -> list[Any]:
        if target_umo:
            target = await self._store.get_stream(target_umo)
            return [target] if target and target.active else []
        return await self._store.list_active_streams()

    async def _ensure_current_stream_bound(self, event: AstrMessageEvent) -> None:
        now = time.time()
        await self._store.bind_stream(
            unified_msg_origin=event.unified_msg_origin,
            session_name=self._build_session_name(event),
            platform=self._safe_platform_name(event),
            is_group=bool(self._safe_group_id(event)),
            now=now,
        )
        await self._store.touch_user_message(event.unified_msg_origin, now=now)

    def _extract_payload(self, event: AstrMessageEvent, command: str) -> str:
        text = (event.message_str or "").strip()
        if text.startswith("/"):
            text = text[1:]
        if not text.startswith(command):
            return ""
        return text[len(command) :].strip()

    def _parse_topic_payload(self, payload: str) -> TopicDraft | None:
        if not payload:
            return None

        delimiter = "|" if "|" in payload else "｜" if "｜" in payload else ""
        if not delimiter:
            return None

        title, description = [part.strip() for part in payload.split(delimiter, 1)]
        if not title or not description:
            return None

        return TopicDraft(title=title, description=description)

    def _build_session_name(self, event: AstrMessageEvent) -> str:
        group_id = self._safe_group_id(event)
        if group_id:
            return f"group:{group_id}"

        sender_id = self._safe_sender_id(event)
        return f"private:{sender_id or 'unknown'}"

    def _safe_group_id(self, event: AstrMessageEvent) -> str:
        try:
            value = event.get_group_id()
            return str(value) if value else ""
        except Exception:
            return ""

    def _safe_platform_name(self, event: AstrMessageEvent) -> str:
        try:
            value = event.get_platform_name()
            return str(value) if value else "unknown"
        except Exception:
            return "unknown"

    def _safe_sender_id(self, event: AstrMessageEvent) -> str:
        try:
            value = event.get_sender_id()
            return str(value) if value else "unknown"
        except Exception:
            return "unknown"

    def _safe_sender_name(self, event: AstrMessageEvent) -> str:
        try:
            value = event.get_sender_name()
            return str(value) if value else "unknown"
        except Exception:
            return "unknown"

    def _build_llm_prompt(self, *, topic: SelectedTopic, recent_dialogue: list[str], max_message_chars: int) -> str:
        history = "\n".join(recent_dialogue[:12]) if recent_dialogue else "(最近消息为空)"
        topic_desc = topic.description or "请围绕该话题抛出一个自然的问题。"
        lower_bound = min(50, max_message_chars)
        return (
            "你是群聊里的自然参与者，不要自称机器人。"
            "基于最近聊天上下文，发一条简短且自然的引导发言。\n\n"
            f"话题标题: {topic.title}\n"
            f"话题描述: {topic_desc}\n\n"
            "最近聊天:\n"
            f"{history}\n\n"
            "要求:\n"
            "1) 输出简体中文。\n"
            f"2) {lower_bound}-{max_message_chars}字。\n"
            "3) 语气自然，不要模板腔。\n"
            "4) 结尾尽量带一个开放问题，引导群友回复。\n"
            "5) 只输出最终发言内容，不要解释。"
        )

    def _truncate_text(self, text: str, max_chars: int) -> str:
        if max_chars <= 0:
            return ""
        if len(text) <= max_chars:
            return text
        return text[:max_chars]

    def _format_elapsed(self, ts: float) -> str:
        if ts <= 0:
            return "从未"

        elapsed = int(max(time.time() - ts, 0))
        if elapsed < 60:
            return f"{elapsed}s"
        if elapsed < 3600:
            return f"{elapsed // 60}m"
        if elapsed < 86400:
            return f"{elapsed // 3600}h"
        return f"{elapsed // 86400}d"

    @staticmethod
    def _as_mapping(value: Any) -> Mapping[str, Any]:
        if isinstance(value, Mapping):
            return value
        return {}
