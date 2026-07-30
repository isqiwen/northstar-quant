from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from sqlalchemy.orm import Session

from northstar_quant.common.time import utc_now
from northstar_quant.db.repositories import (
    release_execution_lease,
    renew_execution_lease,
    try_acquire_execution_lease,
)


def test_execution_lease_is_account_scoped_and_fenced_across_sessions(postgresql_engine):
    engine = postgresql_engine
    now = utc_now()
    resource = "live-submit:ctp:DU123456"

    with Session(engine, future=True) as first:
        first_token = try_acquire_execution_lease(
            first,
            resource_key=resource,
            owner_token="process-a",
            ttl_seconds=30,
            now=now,
        )

    with Session(engine, future=True) as second:
        blocked = try_acquire_execution_lease(
            second,
            resource_key=resource,
            owner_token="process-b",
            ttl_seconds=30,
            now=now + timedelta(seconds=10),
        )

    assert first_token == 1
    assert blocked is None

    with Session(engine, future=True) as second:
        second_token = try_acquire_execution_lease(
            second,
            resource_key=resource,
            owner_token="process-b",
            ttl_seconds=30,
            now=now + timedelta(seconds=31),
        )

    assert second_token == 2

    with Session(engine, future=True) as first:
        assert (
            renew_execution_lease(
                first,
                resource_key=resource,
                owner_token="process-a",
                fencing_token=first_token,
                ttl_seconds=30,
                now=now + timedelta(seconds=32),
            )
            is False
        )
        assert (
            release_execution_lease(
                first,
                resource_key=resource,
                owner_token="process-a",
                fencing_token=first_token,
            )
            is False
        )

    with Session(engine, future=True) as second:
        assert renew_execution_lease(
            second,
            resource_key=resource,
            owner_token="process-b",
            fencing_token=second_token,
            ttl_seconds=30,
            now=now + timedelta(seconds=32),
        )
        assert release_execution_lease(
            second,
            resource_key=resource,
            owner_token="process-b",
            fencing_token=second_token,
        )


def test_same_owner_renews_without_incrementing_fencing_token(postgresql_engine):
    engine = postgresql_engine
    now = utc_now()

    with Session(engine, future=True) as session:
        first = try_acquire_execution_lease(
            session,
            resource_key="live-submit:paper:paper-account",
            owner_token="run-1",
            ttl_seconds=60,
            now=now,
        )
        renewed = try_acquire_execution_lease(
            session,
            resource_key="live-submit:paper:paper-account",
            owner_token="run-1",
            ttl_seconds=60,
            now=now + timedelta(seconds=5),
        )

    assert first == 1
    assert renewed == 1


def test_concurrent_sessions_cannot_both_acquire_account_lease(postgresql_engine):
    engine = postgresql_engine
    now = utc_now()
    barrier = Barrier(2)

    def acquire(owner: str) -> int | None:
        with Session(engine, future=True) as session:
            barrier.wait()
            return try_acquire_execution_lease(
                session,
                resource_key="live-submit:ctp:DU123456",
                owner_token=owner,
                ttl_seconds=60,
                now=now,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(acquire, ("process-a", "process-b")))

    assert results.count(1) == 1
    assert results.count(None) == 1
