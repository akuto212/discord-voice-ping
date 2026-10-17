"""Ordered adapter for discord.py's synchronous event dispatch."""

import asyncio
import json
import logging
from typing import Any

import discord

from .config import Config, ConfigError
from .state import OccupancyTracker
from .telegram import Notification, TelegramSender

log = logging.getLogger(__name__)


class VoiceClient(discord.Client):
    def __init__(self, config: Config, sender: TelegramSender) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True
        super().__init__(
            intents=intents,
            member_cache_flags=discord.MemberCacheFlags.none(),
            chunk_guilds_at_startup=False,
            max_messages=None,
            enable_debug_events=True,
        )
        self.config = config
        self.sender = sender
        self.tracker = OccupancyTracker(config.channel_ids)
        self.failure: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._gateway_live = False
        self._awaiting_ready = False
        self._has_snapshot = False
        self._validated = False
        self._blocked: set[int] = set()

    def dispatch(self, event: str, /, *args: Any, **kwargs: Any) -> None:
        # socket_raw_receive runs before parsing; high-level events run after cache
        # mutation. Async on_* callbacks may see a cache several events ahead.
        # Keep all occupancy/lifecycle changes here, with no await or network I/O.
        try:
            self._handle_event(event, *args)
        except Exception as error:
            self.tracker.pause()
            if not self.failure.done():
                self.failure.set_exception(error)
        super().dispatch(event, *args, **kwargs)

    def _handle_event(self, event: str, *args: Any) -> None:
        if event == "socket_raw_receive":
            packet = json.loads(args[0])
            if packet.get("t") == "GUILD_CREATE":
                self._receive_snapshot(packet["d"])
            elif packet.get("t") == "VOICE_STATE_UPDATE":
                self._receive_voice_state(packet["d"])
        elif event in {"disconnect", "connect"}:
            self._gateway_live = False
            self._awaiting_ready = event == "connect"
            if event == "connect":
                self._has_snapshot = False
            self.tracker.pause()
            log.info("discord_sync_pending: event=%s; уведомления приостановлены", event)
        elif event in {"ready", "resumed"}:
            if event == "ready" and not self._awaiting_ready:
                # A delayed ready task from a disconnected session is not proof
                # that the connection is live.
                return
            self._awaiting_ready = False
            self._gateway_live = True
            self._synchronize()
        elif event in {"guild_available", "guild_join"}:
            if args[0].id == self.config.guild_id:
                if self._gateway_live:
                    self._synchronize()
        elif event in {"guild_unavailable", "guild_remove"}:
            if args[0].id == self.config.guild_id:
                self.tracker.pause()
                self._has_snapshot = False
                log.warning("discord_guild_unavailable: уведомления приостановлены")
        elif event in {
            "guild_channel_create",
            "guild_channel_update",
            "guild_channel_delete",
            "guild_role_create",
            "guild_role_update",
            "guild_role_delete",
            "member_update",
        }:
            entity = args[-1]
            if entity.guild.id == self.config.guild_id:
                self._check_visibility()

    def _receive_voice_state(self, data: dict[str, Any]) -> None:
        if data.get("guild_id") != str(self.config.guild_id):
            return
        user_id = int(data["user_id"])
        channel_id = int(data["channel_id"]) if data.get("channel_id") else None
        if not self.tracker.update(user_id, channel_id):
            return
        guild = self.get_guild(self.config.guild_id)
        channel = guild.get_channel(channel_id) if guild is not None else None
        if guild is None or not self._can_observe(guild, channel):
            self.tracker.invalidate(channel_id)
            log.warning("discord_channel_unknown: нет достоверных метаданных канала")
            return
        # A guild VOICE_STATE_UPDATE contains a member, even with member caching
        # disabled. Read its fresh name instead of an older cached Member.
        member = data.get("member", {})
        user = member.get("user", {})
        name = member.get("nick") or user.get("global_name") or user.get("username")
        if not isinstance(name, str):
            log.warning("discord_member_unknown: имя отсутствует; уведомление пропущено")
            return
        self.sender.enqueue(Notification(name, channel.name, guild.name, guild.id, channel_id))

    def _receive_snapshot(self, data: dict[str, Any]) -> None:
        if int(data["id"]) != self.config.guild_id:
            return
        self.tracker.pause()
        self._has_snapshot = False
        # Missing data is unknown, never an empty channel. In particular a REST
        # Guild/Channel response is not a voice occupancy snapshot.
        if not isinstance(data.get("voice_states"), list) or not isinstance(
            data.get("channels"), list
        ):
            log.warning("discord_snapshot_missing: ожидается полный GUILD_CREATE")
            return
        snapshot: dict[int, set[int]] = {
            int(channel["id"]): set()
            for channel in data["channels"]
            if channel["type"] == 2 and int(channel["id"]) in self.config.channel_ids
        }
        for state in data["voice_states"]:
            channel_id = int(state["channel_id"]) if state.get("channel_id") else None
            if channel_id in snapshot:
                snapshot[channel_id].add(int(state["user_id"]))
        self.tracker.synchronize(snapshot)
        self.tracker.pause()
        self._blocked.clear()
        self._has_snapshot = True

    def _observation_problem(self, guild: discord.Guild, channel: Any) -> str | None:
        if channel is None:
            return (
                "канал не найден на указанном сервере. Проверьте DISCORD_GUILD_ID и "
                "DISCORD_VOICE_CHANNEL_IDS; нужен ID самого голосового канала, а не категории."
            )
        if not isinstance(channel, discord.VoiceChannel):
            return (
                f"неподдерживаемый тип канала {channel.type.name} ({channel.type.value}). "
                "Нужен обычный голосовой канал (voice, тип 2); Stage, категории и текстовые "
                "каналы не поддерживаются."
            )
        bot_member = guild.me
        if bot_member is None:
            return (
                "Discord не передал данные самого бота на сервере; проверить права невозможно. "
                "Перезапустите контейнер для получения нового снимка сервера."
            )
        if not channel.permissions_for(bot_member).view_channel:
            return (
                f"у бота {bot_member.id} нет разрешения View Channel («Просматривать канал»). "
                "Проверьте права роли бота и переопределения прав канала/категории."
            )
        return None

    def _can_observe(self, guild: discord.Guild, channel: Any) -> bool:
        return self._observation_problem(guild, channel) is None

    def _check_visibility(self) -> None:
        guild = self.get_guild(self.config.guild_id)
        if guild is None or guild.unavailable:
            return
        for channel_id in sorted(self.config.channel_ids):
            problem = self._observation_problem(guild, guild.get_channel(channel_id))
            if problem is not None:
                self.tracker.invalidate(channel_id)
                if channel_id not in self._blocked:
                    self._blocked.add(channel_id)
                    log.warning(
                        "discord_channel_unknown: channel_id=%d; причина=%s; "
                        "после исправления перезапустите контейнер для полной синхронизации",
                        channel_id,
                        problem,
                    )

    def _synchronize(self) -> None:
        guild = self.get_guild(self.config.guild_id)
        if guild is None:
            raise ConfigError("DISCORD_GUILD_ID: бот не состоит на указанном сервере.")
        if guild.unavailable:
            self.tracker.pause()
            log.warning("discord_guild_unavailable: ожидается достоверное состояние сервера")
            return
        if not self._has_snapshot:
            self.tracker.pause()
            log.warning("discord_snapshot_pending: уведомления приостановлены до полного снимка")
            return
        current = self.tracker.snapshot()
        snapshot: dict[int, set[int]] = {}
        for channel_id in sorted(self.config.channel_ids):
            channel = guild.get_channel(channel_id)
            problem = self._observation_problem(guild, channel)
            if problem is not None:
                if not self._validated:
                    raise ConfigError(f"Канал {channel_id}, сервер {guild.id}: {problem}")
                self._blocked.add(channel_id)
            elif channel_id not in self._blocked:
                # Replay has already updated our authoritative snapshot. Do not
                # re-read channel.members or even voice_states: discord.py 2.7.1
                # can retain stale entries on an unavailable guild's recovery.
                if channel_id in current:
                    snapshot[channel_id] = current[channel_id]
        self.tracker.synchronize(snapshot)
        self._validated = True
        log.info(
            "discord_synchronized: каналов=%d/%d; начальные уведомления подавлены",
            len(snapshot),
            len(self.config.channel_ids),
        )
