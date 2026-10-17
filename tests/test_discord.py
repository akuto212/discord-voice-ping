"""Contract tests run real discord.py parsers, without opening a connection."""

import asyncio
import json
from unittest.mock import Mock

import pytest
from discord.gateway import DiscordWebSocket
from discord.user import ClientUser

from voice_notify.config import ConfigError
from voice_notify.discord_client import VoiceClient


def member(user_id, name="Александр"):
    return {
        "user": {"id": str(user_id), "username": name, "discriminator": "0", "avatar": None},
        "nick": name,
        "roles": [],
        "flags": 0,
    }


def voice(user_id, channel_id, **extra):
    return {
        "guild_id": "100",
        "user_id": str(user_id),
        "channel_id": str(channel_id) if channel_id else None,
        "session_id": "fake-session",
        "deaf": False,
        "mute": False,
        "self_deaf": False,
        "self_mute": False,
        "self_video": False,
        "suppress": False,
        "member": member(user_id),
        **extra,
    }


def guild_payload(states=()):
    return {
        "id": "100",
        "name": "Наш сервер",
        "owner_id": "777",
        "unavailable": False,
        "roles": [{"id": "100", "name": "@everyone", "permissions": "1024"}],
        "members": [member(999)],
        "channels": [
            {
                "id": str(channel),
                "name": f"Голосовой {channel}",
                "type": 2,
                "position": index,
                "permission_overwrites": [],
                "bitrate": 64000,
                "user_limit": 0,
            }
            for index, channel in enumerate((200, 300, 400))
        ],
        "voice_states": list(states),
    }


class RecordingSender:
    def __init__(self):
        self.notifications = []

    def enqueue(self, notification):
        self.notifications.append(notification)


@pytest.fixture
async def client(config):
    client = VoiceClient(config, RecordingSender())
    async with client:
        client._connection.user = ClientUser(state=client._connection, data=member(999)["user"])
        yield client
        if client.failure.done():
            client.failure.exception()


def packet(client, name, payload):
    # This matches DiscordWebSocket.received_message: socket_raw_receive is called
    # synchronously before the corresponding ConnectionState parser.
    client.dispatch("socket_raw_receive", json.dumps({"op": 0, "t": name, "d": payload, "s": 1}))
    client._connection.parsers[name](payload)


def start(client, states=()):
    client.dispatch("connect")
    packet(client, "GUILD_CREATE", guild_payload(states))
    client.dispatch("ready")
    assert not client.failure.done()


def joins(client, *events):
    for event in events:
        packet(client, "VOICE_STATE_UPDATE", event)
    assert not client.failure.done()


async def test_real_parser_burst_without_yielding(client):
    start(client)
    joins(client, voice(1, 200), voice(2, 200), voice(1, 200))
    assert len(client.sender.notifications) == 1
    assert client.sender.notifications[0].user_name == "Александр"
    assert client.get_guild(100).get_channel(200).members == []


@pytest.mark.parametrize(
    "change",
    [
        {"self_mute": True},
        {"self_deaf": True},
        {"self_video": True},
        {"self_stream": True},
        {"mute": True},
        {"deaf": True},
    ],
)
async def test_non_channel_updates(client, change):
    start(client, [voice(1, 200)])
    joins(client, voice(1, 200, **change))
    assert not client.sender.notifications


async def test_names_from_fresh_payload_and_updated_metadata(client):
    start(client, [voice(1, 200)])
    channel = dict(guild_payload()["channels"][1], name="Новое имя")
    packet(client, "CHANNEL_UPDATE", dict(channel, guild_id="100"))
    joins(client, voice(1, 300, member=member(1, "Новый ник")))
    notification = client.sender.notifications[0]
    assert notification.user_name == "Новый ник"
    assert notification.channel_name == "Новое имя"
    assert notification.guild_name == "Наш сервер"


async def test_moves_occupied_and_unmonitored(client):
    start(client)
    joins(client, voice(1, 200), voice(2, 300), voice(1, 300), voice(3, 400))
    assert len(client.sender.notifications) == 2
    joins(client, voice(1, None), voice(2, None), voice(3, 300))
    assert len(client.sender.notifications) == 3


async def test_resume_suppresses_replay_then_recovers(client):
    start(client, [voice(1, 200)])
    client.dispatch("disconnect")
    joins(client, voice(1, None), voice(2, 200), voice(2, 300))
    packet(client, "RESUMED", {})
    assert not client.sender.notifications
    joins(client, voice(3, 300), voice(4, 200))
    assert len(client.sender.notifications) == 1
    assert client.sender.notifications[0].channel_id == 200


async def test_new_session_has_no_startup_notifications(client):
    start(client, [voice(1, 200)])
    client.dispatch("disconnect")
    # New READY clears the library cache, then a full GUILD_CREATE arrives.
    client._connection.clear()
    client._connection.user = ClientUser(state=client._connection, data=member(999)["user"])
    start(client, [voice(2, 300)])
    joins(client, voice(3, 300), voice(4, 200))
    assert len(client.sender.notifications) == 1


async def test_guild_recovery_does_not_inherit_stale_library_entries(client):
    start(client, [voice(1, 200)])
    packet(client, "GUILD_DELETE", {"id": "100", "unavailable": True})
    # User 1 left during the outage. discord.py reuses its Guild object here.
    packet(client, "GUILD_CREATE", guild_payload([voice(2, 300)]))
    assert not client.sender.notifications
    # Assert the regression's precondition: the library still holds user 1.
    assert 1 in client.get_guild(100).get_channel(200).voice_states
    joins(client, voice(3, 200), voice(4, 300))
    assert [n.channel_id for n in client.sender.notifications] == [200]


async def test_missing_snapshot_is_unknown(client):
    client.dispatch("connect")
    payload = guild_payload()
    del payload["voice_states"]
    packet(client, "GUILD_CREATE", payload)
    client.dispatch("ready")
    joins(client, voice(1, 200))
    assert not client.tracker.active
    assert not client.sender.notifications


async def test_missing_member_does_not_corrupt_occupancy(client):
    start(client)
    event = voice(1, 200)
    del event["member"]
    joins(client, event)
    # discord.py discards this event because its member cache is disabled. The
    # raw event must still count the participant and suppress a second join.
    joins(client, voice(2, 200))
    assert not client.sender.notifications
    joins(client, voice(1, None), voice(2, None), voice(3, 200))
    assert len(client.sender.notifications) == 1


async def test_other_guild_does_not_change_state(client):
    start(client)
    event = dict(voice(1, 200), guild_id="101")
    joins(client, event)
    joins(client, voice(2, 200))
    assert len(client.sender.notifications) == 1


async def test_startup_waits_for_unavailable_guild(client):
    client.dispatch("connect")
    client._connection._add_guild_from_data({"id": "100", "unavailable": True})
    client.dispatch("ready")
    assert not client.failure.done()
    assert not client.tracker.active
    packet(client, "GUILD_CREATE", guild_payload([voice(1, 200)]))
    joins(client, voice(2, 200), voice(3, 300))
    assert [n.channel_id for n in client.sender.notifications] == [300]


async def test_joins_between_snapshot_and_ready_are_silent(client):
    client.dispatch("connect")
    packet(client, "GUILD_CREATE", guild_payload())
    joins(client, voice(1, 200))
    client.dispatch("ready")
    joins(client, voice(2, 200), voice(3, 300))
    assert [n.channel_id for n in client.sender.notifications] == [300]


async def test_deleted_channel_is_not_assumed_empty(client):
    start(client)
    packet(client, "CHANNEL_DELETE", {"id": "200", "guild_id": "100"})
    joins(client, voice(1, 200), voice(2, 300))
    assert [n.channel_id for n in client.sender.notifications] == [300]


async def test_delayed_ready_after_disconnect_is_ignored(client):
    client.dispatch("connect")
    packet(client, "GUILD_CREATE", guild_payload())
    client.dispatch("disconnect")
    client.dispatch("ready")
    joins(client, voice(1, 200))
    assert not client.sender.notifications
    assert not client.tracker.active


async def test_visibility_loss_blocks_until_full_snapshot(client):
    start(client)
    channel = dict(guild_payload()["channels"][0], guild_id="100")
    channel["permission_overwrites"] = [{"id": "100", "type": 0, "allow": "0", "deny": "1024"}]
    packet(client, "CHANNEL_UPDATE", channel)
    joins(client, voice(1, 200), voice(2, 300))
    assert [n.channel_id for n in client.sender.notifications] == [300]
    channel["permission_overwrites"] = []
    packet(client, "CHANNEL_UPDATE", channel)
    joins(client, voice(1, None), voice(3, 200))
    assert len(client.sender.notifications) == 1
    packet(client, "GUILD_CREATE", guild_payload())
    joins(client, voice(4, 200))
    assert len(client.sender.notifications) == 2


@pytest.mark.parametrize(
    ("channel_type", "type_name"), [(0, "text"), (4, "category"), (13, "stage_voice")]
)
async def test_invalid_channel_type_is_reported(client, channel_type, type_name):
    client.dispatch("connect")
    payload = guild_payload()
    payload["channels"][0]["type"] = channel_type
    packet(client, "GUILD_CREATE", payload)
    client.dispatch("ready")
    with pytest.raises(ConfigError, match=f"Канал 200, сервер 100:.*{type_name}"):
        await client.failure
    assert not client.tracker.active


async def test_missing_channel_is_reported(client):
    client.dispatch("connect")
    payload = guild_payload()
    payload["channels"] = payload["channels"][1:]
    packet(client, "GUILD_CREATE", payload)
    client.dispatch("ready")
    with pytest.raises(ConfigError, match="Канал 200, сервер 100: канал не найден"):
        await client.failure
    assert not client.tracker.active


async def test_missing_bot_member_is_reported(client):
    client.dispatch("connect")
    payload = guild_payload()
    payload["members"] = []
    packet(client, "GUILD_CREATE", payload)
    client.dispatch("ready")
    with pytest.raises(ConfigError, match="Discord не передал данные самого бота"):
        await client.failure
    assert not client.tracker.active


@pytest.mark.parametrize("channel_overwrite", [False, True])
async def test_missing_view_channel_is_reported(client, channel_overwrite):
    client.dispatch("connect")
    payload = guild_payload()
    if channel_overwrite:
        payload["channels"][0]["permission_overwrites"] = [
            {"id": "100", "type": 0, "allow": "0", "deny": "1024"}
        ]
    else:
        payload["roles"][0]["permissions"] = "0"
    packet(client, "GUILD_CREATE", payload)
    client.dispatch("ready")
    with pytest.raises(
        ConfigError, match="Канал 200, сервер 100: у бота 999 нет разрешения View Channel"
    ):
        await client.failure
    assert not client.tracker.active


async def test_view_channel_is_sufficient_without_connect_or_speak(client):
    start(client)
    guild = client.get_guild(100)
    permissions = guild.get_channel(200).permissions_for(guild.me)
    assert permissions.view_channel
    assert not permissions.connect
    assert not permissions.speak
    assert not permissions.administrator
    joins(client, voice(1, 200))
    assert len(client.sender.notifications) == 1


async def test_wrong_guild_fails_clearly(client):
    client.dispatch("connect")
    client.dispatch("ready")
    with pytest.raises(ConfigError, match="DISCORD_GUILD_ID"):
        await client.failure


async def test_minimal_intents_and_cache(client):
    assert client.intents.value == (1 | 128)
    assert not client.intents.members
    assert not client.intents.message_content
    assert client._connection.member_cache_flags.value == 0
    assert client._connection.max_messages is None
    assert client.voice_clients == []


async def test_gateway_debug_hook_contract(client):
    # Run the real WebSocket dispatcher too, not just the state parser helpers.
    client.dispatch("connect")
    ws = DiscordWebSocket(Mock(), loop=asyncio.get_running_loop())
    ws._dispatch = client.dispatch
    ws.shard_id = None
    ws._dispatch_listeners = []
    ws._discord_parsers = client._connection.parsers
    ws._keep_alive = None
    # The documented debug-events switch binds log_receive to debug_log_receive.
    assert client._enable_debug_events
    ws.log_receive = ws.debug_log_receive
    await ws.received_message(
        json.dumps({"op": 0, "t": "GUILD_CREATE", "s": 1, "d": guild_payload()})
    )
    client.dispatch("ready")
    await ws.received_message(
        json.dumps({"op": 0, "t": "VOICE_STATE_UPDATE", "s": 2, "d": voice(1, 200)})
    )
    assert len(client.sender.notifications) == 1
