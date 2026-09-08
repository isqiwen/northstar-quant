"""Immutable revisions remain usable independently of a Paper account."""

from dataclasses import replace

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from northstar_quant.research.configuration import ResearchConfig
from northstar_quant.research.configurations import (
    ConfigurationStore,
    initialize_configuration_store,
)


def test_revision_identity_and_immutability_survive_reinitialization(
    postgres_engine: Engine, clean_database: None
) -> None:
    store = ConfigurationStore(postgres_engine)
    config = ResearchConfig()
    original = store.save_configuration("fixed strategy", config)
    newer = store.save_configuration(
        "fixed strategy", replace(config, risk=replace(config.risk, max_lots=11))
    )
    assert newer["configuration_id"] != original["configuration_id"]
    assert newer["strategy_hash"] == original["strategy_hash"]
    assert newer["risk_hash"] != original["risk_hash"]
    with postgres_engine.begin() as connection:
        initialize_configuration_store(connection)
    reopened = ConfigurationStore(postgres_engine)
    assert reopened.save_configuration("fixed strategy", config) == original
    assert reopened.get_configuration(str(original["configuration_id"])) == original
    assert {item["configuration_id"] for item in reopened.list_configurations()} == {
        original["configuration_id"],
        newer["configuration_id"],
    }
    for operation in (
        "UPDATE paper_configurations SET name = 'replaced' WHERE configuration_id = :identity",
        "DELETE FROM paper_configurations WHERE configuration_id = :identity",
    ):
        with pytest.raises(DBAPIError, match="immutable"):
            with postgres_engine.begin() as connection:
                connection.execute(text(operation), {"identity": original["configuration_id"]})
    assert reopened.get_configuration(str(original["configuration_id"])) == original
