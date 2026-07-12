import json
import re
from pathlib import Path
from typing import Any


class ManifestError(ValueError):
    pass


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise ManifestError(f"cannot read manifest: {path}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ManifestError(
                f"{path}:{line_number}: invalid JSON: {exc.msg}"
            ) from exc
        if not isinstance(row, dict):
            raise ManifestError(f"{path}:{line_number}: each row must be a JSON object")
        row["_line"] = line_number
        rows.append(row)
    if not rows:
        raise ManifestError(f"manifest has no data rows: {path}")
    return rows


def require(
    row: dict[str, Any], key: str, expected_type: type | tuple[type, ...]
) -> Any:
    value = row.get(key)
    if not isinstance(value, expected_type):
        line = row.get("_line", "?")
        expected = (
            "/".join(item.__name__ for item in expected_type)
            if isinstance(expected_type, tuple)
            else expected_type.__name__
        )
        raise ManifestError(f"line {line}: {key!r} must be {expected}")
    return value


def normalize_text(text: str) -> str:
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", text.lower())


def edit_distance(reference: str, hypothesis: str) -> int:
    if len(reference) < len(hypothesis):
        reference, hypothesis = hypothesis, reference
    previous = list(range(len(hypothesis) + 1))
    for reference_index, reference_char in enumerate(reference, start=1):
        current = [reference_index]
        for hypothesis_index, hypothesis_char in enumerate(hypothesis, start=1):
            substitution = previous[hypothesis_index - 1] + (
                reference_char != hypothesis_char
            )
            current.append(
                min(
                    previous[hypothesis_index] + 1,
                    current[hypothesis_index - 1] + 1,
                    substitution,
                )
            )
        previous = current
    return previous[-1]


def ratio(numerator: int | float, denominator: int | float) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


def percentile(values: list[float], percentile_value: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile_value
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    fraction = position - lower
    return round(ordered[lower] * (1 - fraction) + ordered[upper] * fraction, 3)


def write_report(report: dict[str, Any], output_path: Path | None) -> None:
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
