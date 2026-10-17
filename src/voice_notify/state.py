"""Pure, synchronous state machine. Unknown channels are never assumed empty."""

from collections.abc import Iterable, Mapping


class OccupancyTracker:
    def __init__(self, channel_ids: frozenset[int]) -> None:
        self.channel_ids = channel_ids
        self._members: dict[int, set[int]] = {}
        self._locations: dict[int, int] = {}
        self.active = False

    def pause(self) -> None:
        self.active = False

    def snapshot(self) -> dict[int, set[int]]:
        return {channel_id: users.copy() for channel_id, users in self._members.items()}

    def invalidate(self, channel_id: int) -> None:
        for user_id in self._members.pop(channel_id, ()):
            self._locations.pop(user_id, None)

    def synchronize(self, snapshot: Mapping[int, Iterable[int]]) -> None:
        """Atomically replace all known state, without producing notifications."""
        self._members = {
            channel_id: set(users)
            for channel_id, users in snapshot.items()
            if channel_id in self.channel_ids
        }
        self._locations = {
            user_id: channel_id for channel_id, users in self._members.items() for user_id in users
        }
        self.active = True

    def update(self, user_id: int, channel_id: int | None) -> bool:
        """Apply the user's absolute location; return True only for a live 0 → 1."""
        previous = self._locations.get(user_id)
        if previous == channel_id:
            return False
        if previous is not None:
            self._members[previous].discard(user_id)
            del self._locations[user_id]
        if channel_id not in self._members:
            return False
        members = self._members[channel_id]
        was_empty = not members
        members.add(user_id)
        self._locations[user_id] = channel_id
        return self.active and was_empty
