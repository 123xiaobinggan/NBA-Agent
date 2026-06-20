from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, URL


@dataclass(frozen=True)
class DatabaseConfig:
    host: str = os.getenv("NBA_DB_HOST", "localhost")
    user: str = os.getenv("NBA_DB_USER", "nba_agent")
    password: str = os.getenv("NBA_DB_PASSWORD", "")
    database: str = os.getenv("NBA_DB_NAME", "nba")
    port: int = int(os.getenv("NBA_DB_PORT", "3306"))


class Database:
    def __init__(self, config: DatabaseConfig | None = None):
        self.config = config or DatabaseConfig()
        self.engine = self._create_engine()

    def _create_engine(self) -> Engine:
        url = URL.create(
            "mysql+pymysql",
            username=self.config.user,
            password=self.config.password,
            host=self.config.host,
            port=self.config.port,
            database=self.config.database,
            query={"charset": "utf8mb4"},
        )
        return create_engine(url, pool_pre_ping=True)

    def execute_read(self, sql: str, limit: int = 200) -> dict[str, Any]:
        statement = text(sql)
        with self.engine.connect() as connection:
            result = connection.execute(statement)
            rows = result.mappings().fetchmany(limit)
            return {
                "columns": list(result.keys()),
                "rows": [dict(row) for row in rows],
                "row_count": len(rows),
                "truncated": len(rows) >= limit,
                "limit": limit,
            }


_db: Database | None = None


def get_database() -> Database:
    global _db
    if _db is None:
        _db = Database()
    return _db
