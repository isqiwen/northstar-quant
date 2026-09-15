"""Reconcile a completed collection with its whole-contract admission evidence."""

from sqlalchemy import Engine, text

from ..maintenance import library_write
from ..tushare.contract_review import review_connection


def process_next(engine: Engine) -> str | None:
    with library_write(engine), engine.begin() as c:
        scope = c.scalar(
            text("""SELECT scope FROM data_contract_collections
            WHERE status IN ('VERIFYING','REJECTED') AND updated_at<now()-interval '1 minute'
            ORDER BY updated_at,scope LIMIT 1 FOR UPDATE SKIP LOCKED""")
        )
        if scope is None:
            return None
        scope = str(scope)
        result = review_connection(c, scope)
        if result["admitted"]:
            # Package writing runs outside this projection transaction, with its
            # own source admission lock and a fresh fixed input/evidence snapshot.
            c.execute(
                text("UPDATE data_contract_collections SET updated_at=now() WHERE scope=:scope"),
                {"scope": scope},
            )
        else:
            c.execute(
                text("""UPDATE data_contract_collections SET status=:status,
                reason=:reason,updated_at=now() WHERE scope=:scope"""),
                dict(
                    scope=scope,
                    status="REJECTED" if result["status"] == "INVALID" else "VERIFYING",
                    reason="；".join(result["reasons"]),
                ),
            )
            return scope
    from .snapshots import publish

    publish(engine, scope)
    return scope
