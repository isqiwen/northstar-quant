"""One claimed, train-only fit in a resource-admitted child process."""

from northstar_quant.data_management.publications import PublishedDatasets

from .experiments import Experiments
from .storage import open_store, require_current


def child(identity: str) -> None:
    engine = open_store()
    try:
        require_current(engine)
        Experiments(engine).execute_fit(identity, PublishedDatasets.from_environment())
    finally:
        engine.dispose()
