"""Bounded, deterministic table normalization, projection, comparison and joins."""

from __future__ import annotations

import csv
import io
import json
import math
from pathlib import Path


class TableError(ValueError):
    """A user-facing contract error, never containing an entire input document."""


def decode_json(value):
    """Read strict JSON text or retain an already decoded, JSON-compatible value."""
    try:
        if isinstance(value, str):
            value = json.loads(value, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        json.dumps(value, allow_nan=False)
        return value
    except (ValueError, TypeError, RecursionError) as exc:
        raise TableError("Expected finite JSON data.") from exc


def configuration(config):
    """Validate the complete table configuration before reading any file."""
    defaults = dict(operation="normalize", keys=[], columns=[], column_types={},
                    delimiter=",", encoding="utf-8-sig", allowed_root="",
                    max_rows=10000, max_bytes=2097152, reject_invalid_rows=False)
    # These are framework-authored execution/path metadata, not table settings.
    unknown = set(config) - set(defaults) - {"execution", "runtime_path", "runtime_path_label"}
    if unknown:
        raise TableError("Unknown table setting.")
    values = {**defaults, **{k: v for k, v in config.items() if k in defaults}}
    if values["operation"] not in {"normalize", "select", "compare", "inner_join", "left_join"}:
        raise TableError("Unsupported operation.")
    for key in ("keys", "columns"):
        value = decode_json(values[key])
        if not isinstance(value, list) or len(value) > 256 or any(
                not isinstance(s, str) or not s or len(s) > 256 for s in value) or len(set(value)) != len(value):
            raise TableError(f"{key} must be a list of distinct column names.")
        values[key] = value
    types = decode_json(values["column_types"])
    if not isinstance(types, dict) or len(types) > 256 or any(
            not isinstance(k, str) or not k or len(k) > 256 or
            v not in {"string", "integer", "number", "boolean", "json"} for k, v in types.items()):
        raise TableError("column_types must map column names to supported types.")
    values["column_types"] = types
    for key, low, high in (("max_rows", 1, 100000), ("max_bytes", 1024, 4194304)):
        value = values[key]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise TableError(f"{key} must be an integer between {low} and {high}.")
    if values["encoding"] not in {"utf-8", "utf-8-sig", "latin-1"}:
        raise TableError("Unsupported encoding.")
    if not isinstance(values["delimiter"], str) or len(values["delimiter"]) != 1 or values["delimiter"] in "\r\n\x00\"":
        raise TableError("delimiter must be one CSV separator character.")
    if not isinstance(values["allowed_root"], str) or len(values["allowed_root"]) > 4096:
        raise TableError("allowed_root must be a directory path.")
    if not isinstance(values["reject_invalid_rows"], bool):
        raise TableError("reject_invalid_rows must be a boolean.")
    if values["operation"] in {"compare", "inner_join", "left_join"} and not values["keys"]:
        raise TableError("Select at least one key column for comparison or joins.")
    if values["operation"] == "select" and not values["columns"]:
        raise TableError("Select at least one output column.")
    return values


def load_table(value, config, root_dir):
    """Read rows or a bounded CSV inside the configured read boundary; never write files."""
    if isinstance(value, str):
        if len(value.encode("utf-8")) > config["max_bytes"]:
            raise TableError("Table input exceeds max_bytes.")
        text = value.strip()
        value = decode_json(text) if text.startswith(("[", "{")) else {"path": text}
    if isinstance(value, dict):
        if set(value) == {"rows"}:
            value = value["rows"]
        elif set(value) == {"path"} and isinstance(value["path"], str):
            base = Path(config["allowed_root"]).expanduser() if config["allowed_root"] else Path(root_dir)
            base = (Path(root_dir) / base).resolve() if not base.is_absolute() else base.resolve()
            path = Path(value["path"]).expanduser()
            path = (base / path).resolve() if not path.is_absolute() else path.resolve()
            if not path.is_relative_to(base) or not path.is_file() or path.suffix.lower() != ".csv":
                raise TableError("Expected a CSV file inside allowed_root.")
            with path.open("rb") as source:
                raw = source.read(config["max_bytes"] + 1)
            if len(raw) > config["max_bytes"]:
                raise TableError("CSV exceeds max_bytes.")
            try:
                reader = csv.DictReader(io.StringIO(raw.decode(config["encoding"]), newline=""),
                                        delimiter=config["delimiter"], strict=True)
                headers = reader.fieldnames
                if not headers or len(headers) > 256 or any(not h for h in headers) or len(set(headers)) != len(headers):
                    raise TableError("CSV needs distinct, nonempty headers (maximum 256).")
                value = []
                for row in reader:
                    if len(value) >= config["max_rows"]:
                        raise TableError("CSV exceeds max_rows.")
                    if None in row or any(v is None for v in row.values()):
                        raise TableError("CSV row width differs from its header.")
                    value.append(row)
            except (csv.Error, UnicodeError) as exc:
                raise TableError("CSV decoding failed; check encoding and delimiter.") from exc
        else:
            raise TableError("Use a row array, {rows: [...]} or {path: 'file.csv'}.")
    if not isinstance(value, list) or len(value) > config["max_rows"]:
        raise TableError("Expected a row array within max_rows.")
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")) > config["max_bytes"]:
        raise TableError("Decoded table exceeds max_bytes.")
    return value


def convert(value, target):
    """Convert a non-null cell strictly; null stays null and booleans never become numbers."""
    if value is None:
        return None
    if target == "string":
        if isinstance(value, (list, dict)):
            raise TableError("Expected a scalar cell.")
        return str(value)
    if target == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in {"true", "false"}:
            return value.lower() == "true"
        raise TableError("Expected true or false.")
    if target == "json":
        return decode_json(value)
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise TableError("Expected a number, not a boolean or collection.")
    try:
        result = int(value) if target == "integer" else float(value)
        if isinstance(value, float) and target == "integer" and value != result:
            raise ValueError()
        if isinstance(result, float) and not math.isfinite(result):
            raise ValueError()
        return result
    except (ValueError, OverflowError) as exc:
        raise TableError(f"Expected a finite {target}.") from exc


def normalize(rows, config, source, cancel):
    """Normalize rows while reporting rejected positions without leaking their contents."""
    accepted, issues = [], []
    for index, row in enumerate(rows):
        if cancel():
            raise TableError("Table operation cancelled.")
        try:
            if not isinstance(row, dict) or len(row) > 256 or any(
                    not isinstance(k, str) or not k or len(k) > 256 for k in row):
                raise TableError("Expected an object with at most 256 named columns.")
            result = dict(row)
            for name, target in config["column_types"].items():
                if name not in row:
                    raise TableError("A typed column is missing.")
                result[name] = convert(row[name], target)
            if config["columns"]:
                if any(name not in result for name in config["columns"]):
                    raise TableError("A selected column is missing.")
                # Keys are retained internally until comparison/join has completed.
            accepted.append(result)
        except TableError as exc:
            if not config["reject_invalid_rows"]:
                raise TableError(f"{source} row {index + 1}: {exc}") from exc
            if len(issues) >= 1000:
                raise TableError("Too many invalid rows (maximum 1000).")
            issues.append({"source": source, "row": index + 1, "reason": str(exc)})
    return accepted, issues


def keyed(rows, keys):
    """Index unambiguous scalar composite keys, rejecting duplicates and missing keys."""
    result = {}
    for row in rows:
        parts = []
        for name in keys:
            value = row.get(name)
            if value is None or isinstance(value, (list, dict)):
                raise TableError("Key columns must contain non-null scalar values.")
            parts.append(value)
        key = json.dumps(parts, sort_keys=True, allow_nan=False)
        if key in result:
            raise TableError("Duplicate key in a table; comparison and joins require unique keys.")
        result[key] = row
    return result


def process(tables, reference, config, root_dir, cancel=lambda: False):
    """Return JSON outputs for one activation with stable input-order and bounded results."""
    config = configuration(config)
    left, issues = normalize(load_table(tables, config, root_dir), config, "tables", cancel)
    operation = config["operation"]
    output, differences = left, {"added": [], "removed": [], "changed": []}
    if operation in {"compare", "inner_join", "left_join"}:
        if reference is None or reference == "":
            raise TableError("reference is required for comparison and joins.")
        right, right_issues = normalize(load_table(reference, config, root_dir),
                                        {**config, "columns": []}, "reference", cancel)
        issues.extend(right_issues)
        current, previous = keyed(left, config["keys"]), keyed(right, config["keys"])
        if operation == "compare":
            differences["added"] = [r for key, r in current.items() if key not in previous]
            differences["removed"] = [r for key, r in previous.items() if key not in current]
            differences["changed"] = [{"before": previous[key], "after": r} for key, r in current.items()
                                      if key in previous and json.dumps(r, sort_keys=True) != json.dumps(previous[key], sort_keys=True)]
        else:
            output = []
            for key, row in current.items():
                if cancel():
                    raise TableError("Table operation cancelled.")
                match = previous.get(key)
                if match is None and operation == "inner_join":
                    continue
                combined = dict(row)
                for name, value in (match or {}).items():
                    if name in config["keys"]:
                        continue
                    target = f"reference.{name}" if name in combined else name
                    if target in combined:
                        raise TableError("Join column collision; rename columns before joining.")
                    combined[target] = value
                output.append(combined)
    if config["columns"]:
        output = [{name: row[name] for name in config["columns"]} for row in output]
    result = {"rows": output, "differences": differences,
              "summary": {"operation": operation, "rows": len(output), "rejected": len(issues), "issues": issues,
                          **{name: len(value) for name, value in differences.items()}}}
    if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 8388608:
        raise TableError("Result exceeds the 8 MiB output limit; reduce the input batch.")
    return result
