from __future__ import annotations

import re
import os
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STRUCTURE_FILE = PROJECT_ROOT / "sql" / "database_structure.txt"
OUTPUT_FILE = Path(os.getenv("NBA_SCHEMA_OUTPUT", str(Path(tempfile.gettempdir()) / "nba_schema.sql")))


def normalize_schema(text: str) -> str:
    text = text.replace("\ufeff", "")
    start = text.find("CREATE TABLE")
    if start < 0:
        raise RuntimeError("No CREATE TABLE statements found in sql/database_structure.txt")
    text = text[start:]

    text = re.sub(
        r"CREATE\s+ALGORITHM\s*=\s*UNDEFINED\s+DEFINER\s*=\s*`[^`]+`\s*@\s*`[^`]+`\s+SQL\s+SECURITY\s+DEFINER\s+VIEW",
        "CREATE OR REPLACE VIEW",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"CREATE\s+TABLE\s+`", "CREATE TABLE IF NOT EXISTS `", text, flags=re.IGNORECASE)
    text = text.replace("utf8mb4_0900_ai_ci", "utf8mb4_unicode_ci")

    lines = [line.rstrip() for line in text.splitlines()]
    statements: list[str] = []
    current: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        starts_statement = stripped.upper().startswith(("CREATE TABLE", "CREATE OR REPLACE VIEW"))
        if starts_statement and current:
            statements.append("\n".join(current).rstrip() + ";")
            current = []
        current.append(line)

    if current:
        statements.append("\n".join(current).rstrip() + ";")

    return (
        "SET NAMES utf8mb4;\n"
        "SET FOREIGN_KEY_CHECKS=0;\n"
        + "\n\n".join(statements)
        + "\nSET FOREIGN_KEY_CHECKS=1;\n"
    )


def main() -> None:
    schema = normalize_schema(STRUCTURE_FILE.read_text(encoding="utf-8"))
    OUTPUT_FILE.write_text(schema, encoding="utf-8")
    print(f"Wrote normalized schema to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
