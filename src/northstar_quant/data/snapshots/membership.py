"""Merkle commitments for immutable storage-2.0 snapshot membership."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from northstar_quant.data.observations.revisions import stable_json_sha256

MAX_SNAPSHOT_MEMBERS = 250_000
SNAPSHOT_MEMBERSHIP_PROTOCOL = "dataset_snapshot_ordered_merkle_membership/1.0.0"
_SNAPSHOT_MEMBERSHIP_LEAF_PROTOCOL = "dataset_snapshot_ordered_merkle_leaf/1.0.0"
_SNAPSHOT_MEMBERSHIP_NODE_PROTOCOL = "dataset_snapshot_ordered_merkle_node/1.0.0"


def snapshot_member_leaf_hash(
    *,
    ordinal: int,
    event_time: str,
    canonical_bar_fingerprint: str,
) -> str:
    """Commit one ordered member and its complete canonical fingerprint."""

    return stable_json_sha256(
        {
            "protocol": _SNAPSHOT_MEMBERSHIP_LEAF_PROTOCOL,
            "ordinal": ordinal,
            "event_time": event_time,
            "canonical_bar_fingerprint": canonical_bar_fingerprint,
        }
    )


@dataclass(frozen=True)
class SnapshotMembershipTree:
    """Commit the complete ordered membership of one immutable snapshot."""

    _levels: tuple[tuple[str, ...], ...]

    @classmethod
    def build(cls, leaf_hashes: Sequence[str]) -> SnapshotMembershipTree:
        if not 1 <= len(leaf_hashes) <= MAX_SNAPSHOT_MEMBERS:
            raise ValueError("snapshot membership must contain between 1 and 250000 leaves")
        if any(not _is_sha256(leaf_hash) for leaf_hash in leaf_hashes):
            raise ValueError("snapshot membership leaves must be lowercase SHA-256")
        levels = [tuple(leaf_hashes)]
        while len(levels[-1]) > 1:
            current = levels[-1]
            levels.append(
                tuple(
                    _node_hash(
                        current[index],
                        current[index + 1] if index + 1 < len(current) else current[index],
                    )
                    for index in range(0, len(current), 2)
                )
            )
        return cls(tuple(levels))

    @property
    def member_count(self) -> int:
        return len(self._levels[0])

    @property
    def content_hash(self) -> str:
        return _root_commitment(self.member_count, self._levels[-1][0])


def _node_hash(left: str, right: str) -> str:
    return stable_json_sha256(
        {
            "protocol": _SNAPSHOT_MEMBERSHIP_NODE_PROTOCOL,
            "left": left,
            "right": right,
        }
    )


def _root_commitment(member_count: int, merkle_root: str) -> str:
    return stable_json_sha256(
        {
            "protocol": SNAPSHOT_MEMBERSHIP_PROTOCOL,
            "member_count": member_count,
            "merkle_root": merkle_root,
        }
    )


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


__all__ = [
    "SNAPSHOT_MEMBERSHIP_PROTOCOL",
    "SnapshotMembershipTree",
    "snapshot_member_leaf_hash",
]
