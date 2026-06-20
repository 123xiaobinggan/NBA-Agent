from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any


def normalize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: normalize_value(value) for key, value in row.items()} for row in rows]


def format_query_result(result: dict[str, Any]) -> dict[str, Any]:
    rows = normalize_rows(result.get("rows", []))
    return {
        "columns": result.get("columns", []),
        "row_count": len(rows),
        "truncated": bool(result.get("truncated", False)),
        "limit": result.get("limit"),
        "rows": rows,
    }


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)
