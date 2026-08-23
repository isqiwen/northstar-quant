import ast
from pathlib import Path


MODULE = Path(__file__).resolve().parents[3] / "src" / "northstar_quant" / "portfolio_risk" / "portfolio" / "targets.py"


def test_portfolio_targets_do_not_import_execution_or_broker_layers() -> None:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any(
        imported == "northstar_quant.trading_execution"
        or imported.startswith("northstar_quant.trading_execution.")
        for imported in imports
    )
