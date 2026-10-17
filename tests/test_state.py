import asyncio

import pytest

from voice_notify.state import OccupancyTracker


@pytest.fixture
def tracker():
    tracker = OccupancyTracker(frozenset({200, 300}))
    tracker.synchronize({200: set(), 300: set()})
    return tracker


def test_entire_occupancy_lifecycle(tracker):
    assert tracker.update(1, 200)  # first joins empty
    assert not tracker.update(2, 200)  # second joins occupied
    assert not tracker.update(1, None)  # one leaves
    assert not tracker.update(2, None)  # last leaves
    assert tracker.update(3, 200)  # empty again


def test_move_to_empty_monitored_channel(tracker):
    assert tracker.update(1, 200)
    assert tracker.update(1, 300)
    assert tracker.update(2, 200)  # old channel is now empty


def test_move_to_occupied_channel(tracker):
    assert tracker.update(1, 200)
    assert tracker.update(2, 300)
    assert not tracker.update(1, 300)
    assert tracker.update(3, 200)


def test_unmonitored_channel(tracker):
    assert not tracker.update(1, 999)
    assert tracker.update(1, 200)
    assert not tracker.update(1, 999)
    assert tracker.update(2, 200)


def test_duplicate_and_non_channel_changes(tracker):
    assert tracker.update(1, 200)
    for _ in range(10):
        assert not tracker.update(1, 200)
    assert not tracker.update(1, None)
    assert not tracker.update(1, None)


async def test_almost_simultaneous_joins(tracker):
    async def join(user):
        return tracker.update(user, 200)

    assert sum(await asyncio.gather(*(join(user) for user in range(100)))) == 1


def test_startup_occupied(tracker):
    tracker.synchronize({200: {1, 2}, 300: set()})
    assert not tracker.update(1, 200)
    assert not tracker.update(3, 200)
    assert not tracker.update(1, None)
    assert not tracker.update(2, None)
    assert not tracker.update(3, None)
    assert tracker.update(4, 200)


def test_channels_are_independent(tracker):
    assert tracker.update(1, 200)
    assert tracker.update(2, 300)
    assert not tracker.update(3, 300)


def test_unknown_channel_is_not_empty(tracker):
    tracker.synchronize({300: set()})
    assert not tracker.update(1, 200)
    assert not tracker.update(2, 200)
    assert tracker.update(3, 300)


def test_disconnect_replay_and_resynchronization(tracker):
    tracker.update(1, 200)
    tracker.pause()
    assert not tracker.update(1, None)
    assert not tracker.update(2, 200)
    tracker.synchronize(tracker.snapshot())
    assert not tracker.update(3, 200)
    tracker.update(2, None)
    tracker.update(3, None)
    assert tracker.update(4, 200)


def test_new_session_replaces_stale_state(tracker):
    tracker.update(1, 200)
    tracker.pause()
    tracker.synchronize({200: set(), 300: {2}})
    assert not tracker.update(3, 300)
    assert tracker.update(4, 200)


def test_invalidation_does_not_assume_empty(tracker):
    tracker.update(1, 200)
    tracker.invalidate(200)
    assert not tracker.update(1, 200)
    assert tracker.update(2, 300)


def test_snapshot_does_not_expose_internal_state(tracker):
    snapshot = tracker.snapshot()
    snapshot[200].add(1)
    assert tracker.update(2, 200)
