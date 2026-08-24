import ast
from pathlib import Path
SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "storage_maintenance.py"


def test_run_counts_archive_retention_after_hot_storage() -> None:
    tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"))
    prune_call = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "prune_expired_archives"
    )
    retention_keyword = next(
        keyword for keyword in prune_call.keywords if keyword.arg == "retention_hours"
    )

    value = retention_keyword.value
    assert isinstance(value, ast.BinOp)
    assert isinstance(value.op, ast.Add)
    assert isinstance(value.left, ast.Attribute)
    assert isinstance(value.right, ast.Attribute)
    assert value.left.attr == "storage_hot_raw_hours"
    assert value.right.attr == "storage_archive_retention_hours"
