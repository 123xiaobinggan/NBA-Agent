from __future__ import annotations

import re
from dataclasses import dataclass

try:
    from .schema_service import allowed_relation_names
except ImportError:
    from schema_service import allowed_relation_names


READ_ONLY_START = re.compile(r"^\s*(select|with|show|describe|desc|explain)\b", re.IGNORECASE)
DANGEROUS = re.compile(
    r"\b(insert|update|delete|replace|merge|drop|alter|create|truncate|rename|grant|revoke|"
    r"call|load|outfile|infile|lock|unlock|set|use|start|commit|rollback)\b",
    re.IGNORECASE,
)
RELATION_PATTERN = re.compile(r"\b(?:from|join)\s+(?!`?[a-zA-Z_][\w]*`?\.)`?([a-zA-Z_][\w]*)`?", re.IGNORECASE)
SCHEMA_QUALIFIED_PATTERN = re.compile(r"\b(?:from|join)\s+`?([a-zA-Z_][\w]*)`?\.`?([a-zA-Z_][\w]*)`?", re.IGNORECASE)
CTE_PATTERN = re.compile(r"(?:with|,)\s+`?([a-zA-Z_][\w]*)`?\s+as\s*\(", re.IGNORECASE)
COMMENT_PATTERN = re.compile(r"(--|#|/\*)")


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    error: str | None = None


def _strip_trailing_semicolon(sql: str) -> str:
    return sql.strip().removesuffix(";").strip()


def _has_multiple_statements(sql: str) -> bool:
    in_single = False
    in_double = False
    escaped = False
    semicolons = 0
    for char in sql:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == ";" and not in_single and not in_double:
            semicolons += 1
    return semicolons > 1 or (semicolons == 1 and not sql.rstrip().endswith(";"))


def validate_readonly_sql(sql: str) -> GuardResult:
    if not sql or not sql.strip():
        return GuardResult(False, "SQL不能为空。")

    if COMMENT_PATTERN.search(sql):
        return GuardResult(False, "不允许在SQL中使用注释。")

    if _has_multiple_statements(sql):
        return GuardResult(False, "只允许执行单条SQL语句。")

    cleaned = _strip_trailing_semicolon(sql)
    if not READ_ONLY_START.match(cleaned):
        return GuardResult(False, "只允许只读SQL:SELECT/WITH/SHOW/DESCRIBE/EXPLAIN。")

    if DANGEROUS.search(cleaned):
        return GuardResult(False, "SQL包含非只读或危险关键字。")

    allowed = allowed_relation_names()
    cte_names = {match.group(1) for match in CTE_PATTERN.finditer(cleaned)}
    referenced = set()
    for match in SCHEMA_QUALIFIED_PATTERN.finditer(cleaned):
        referenced.add(match.group(2))
    for match in RELATION_PATTERN.finditer(cleaned):
        referenced.add(match.group(1))

    disallowed = sorted(name for name in referenced if name not in allowed and name not in cte_names)
    if disallowed:
        return GuardResult(False, f"SQL访问了不允许的表或视图:{', '.join(disallowed)}。")

    return GuardResult(True)


def ensure_readonly_sql(sql: str) -> str:
    result = validate_readonly_sql(sql)
    if not result.ok:
        raise ValueError(result.error)
    return _strip_trailing_semicolon(sql)
