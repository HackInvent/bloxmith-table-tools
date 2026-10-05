"""FB1/FB2/FB3: strict table behavior, bounds, cancellation and controlled CSV reads."""

from pathlib import Path
import json
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from blocs.table_tools.logic import TableError, configuration, process


def fails(function):
    """Require a meaningful contract error instead of accepting malformed data."""
    try:
        function()
    except (TableError, ValueError):
        return
    raise AssertionError("Expected a rejected operation")


def main():
    """Exercise actual processing rather than fixtures returning prebuilt results."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        rows = [{"id": "a", "qty": "3"}, {"id": "b", "qty": "1"}]
        previous = [{"id": "a", "qty": 2}, {"id": "c", "qty": 7}]
        config = {"operation": "compare", "keys": ["id"], "column_types": {"qty": "integer"}}
        result = process(rows, previous, config, root)
        assert result["rows"][0]["qty"] == 3
        assert result["differences"]["added"] == [{"id": "b", "qty": 1}]
        assert result["differences"]["removed"] == [{"id": "c", "qty": 7}]
        assert result["differences"]["changed"][0]["before"]["qty"] == 2
        assert rows[0]["qty"] == "3", "Original data mutated"
        assert process(rows, None, {"operation": "select", "columns": ["id"]}, root)["rows"] == [{"id": "a"}, {"id": "b"}]
        assert process([], None, {}, root)["rows"] == []
        assert process([], None, {"runtime_path": [], "runtime_path_label": "Tables"}, root)["rows"] == []
        left = [{"id": 1, "name": "new"}, {"id": 2, "name": "second"}]
        right = [{"id": 1, "name": "old", "score": 5}]
        for operation, count in (("inner_join", 1), ("left_join", 2)):
            joined = process(left, right, {"operation": operation, "keys": ["id"]}, root)["rows"]
            assert len(joined) == count and joined[0]["reference.name"] == "old"
            assert joined[0]["name"] == "new" and joined[0]["score"] == 5
        fails(lambda: process(left, None, {"operation": "compare", "keys": ["id"]}, root))
        fails(lambda: process(left + left, right, {"operation": "compare", "keys": ["id"]}, root))
        fails(lambda: process([{"id": None}], right, {"operation": "compare", "keys": ["id"]}, root))
        fails(lambda: process([{"id": 1, "name": "n", "reference.name": "x"}], right,
                              {"operation": "left_join", "keys": ["id"]}, root))
        fails(lambda: process(rows, None, {}, root, lambda: True))
        for value in ("NaN", "Infinity", True, 1.5):
            fails(lambda v=value: process([{"q": v}], None, {"column_types": {"q": "integer"}}, root))
        for value in (float("nan"), float("inf")):
            fails(lambda v=value: process([{"q": v}], None, {}, root))
        assert process([{"b": "FALSE"}], None, {"column_types": {"b": "boolean"}}, root)["rows"] == [{"b": False}]
        fails(lambda: process([{"b": "yes"}], None, {"column_types": {"b": "boolean"}}, root))
        rejected = process([{"q": "bad"}, {"q": "4"}], None,
                           {"column_types": {"q": "integer"}, "reject_invalid_rows": True}, root)
        assert rejected["summary"]["rejected"] == 1 and rejected["rows"] == [{"q": 4}]
        assert "bad" not in json.dumps(rejected["summary"]), "A rejected cell leaked into diagnostics"
        for config in ({"keys": ["id", "id"]}, {"max_rows": True}, {"delimiter": "xx"}, {"unknown": 1},
                       {"column_types": {"q": "date"}}, {"max_bytes": 0}, {"operation": "select"}):
            fails(lambda c=config: configuration(c))
        csv_path = root / "source.csv"
        csv_path.write_text('id,qty\na,3\nb,1\n', encoding="utf-8")
        original = csv_path.read_bytes()
        assert process({"path": "source.csv"}, None, {"column_types": {"qty": "integer"}}, root)["rows"] == [
            {"id": "a", "qty": 3}, {"id": "b", "qty": 1}]
        assert csv_path.read_bytes() == original
        fails(lambda: process({"path": "source.csv"}, None, {"max_rows": 1}, root))
        csv_path.write_text("id,id\na,b\n", encoding="utf-8")
        fails(lambda: process(str(csv_path), None, {}, root))
        csv_path.write_text("id,qty\na,1,extra\n", encoding="utf-8")
        fails(lambda: process(str(csv_path), None, {}, root))
        allowed = root / "allowed"
        allowed.mkdir()
        (allowed / "outside.csv").symlink_to(csv_path)
        for path in (str(csv_path), "../source.csv", "outside.csv"):
            fails(lambda p=path: process({"path": p}, None, {"allowed_root": str(allowed)}, root))
        fails(lambda: process([{"x": "a" * 2048}], None, {"max_bytes": 1024}, root))
    print("[ok] Table Tools functional behavior")


if __name__ == "__main__":
    main()
