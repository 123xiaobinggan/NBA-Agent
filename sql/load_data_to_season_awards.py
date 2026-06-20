import argparse
import os
import re
import sys
import unicodedata
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from pandas_load_utils import read_csv_rows


database = {
    "host": os.getenv("NBA_DB_HOST", "localhost"),
    "user": os.getenv("NBA_DB_USER", "nba_agent"),
    "password": os.getenv("NBA_DB_PASSWORD", ""),
    "database": os.getenv("NBA_DB_NAME", "nba"),
    "port": int(os.getenv("NBA_DB_PORT", "3306")),
}

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_FILE = PROJECT_ROOT / "data" / "player" / "NBA_Player_Stats_2.csv"

SEASON_COLUMNS = ("season_id", "season", "Season", "year", "Year", "SeasonID")
AWARD_COLUMNS = ("award_type", "award", "Award", "Award Type", "award_name", "Award_Name")
PLAYER_COLUMNS = (
    "source_player_name",
    "player_name",
    "Player",
    "player",
    "Winner",
    "winner",
    "name",
    "Name",
)
IGNORED_WIDE_COLUMNS = set(SEASON_COLUMNS) | set(AWARD_COLUMNS) | set(PLAYER_COLUMNS)
TRUTHY_MARKERS = {"1", "y", "yes", "true", "t", "winner", "won", "award"}
ROW_PLAYER_AWARD_TRUE_VALUES = {"y", "yes", "true", "t", "winner", "won", "award"}


def make_engine():
    url = URL.create(
        "mysql+pymysql",
        username=database["user"],
        password=database["password"],
        host=database["host"],
        port=database["port"],
        database=database["database"],
        query={"charset": "utf8mb4"},
    )
    return create_engine(url, pool_pre_ping=True)


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def clean_text(value):
    if value is not None and not isinstance(value, str):
        value = str(value)
    value = (value or "").strip()
    return value or None


def normalize_name(name):
    name = clean_text(name)
    if name is None:
        return None
    normalized = unicodedata.normalize("NFKD", name)
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = normalized.lower()
    normalized = normalized.replace(".", "").replace("'", "")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized or None


def first_present(row, columns):
    for column in columns:
        value = clean_text(row.get(column))
        if value is not None:
            return value
    return None


def season_id_from_value(value):
    value = clean_text(value)
    if value is None:
        return None

    if value.isdigit():
        year = int(value)
        if 10000 <= year <= 99999:
            return int(f"2{str(year)[-4:]}")
        if 1900 <= year <= 2100:
            return int(f"2{year}")

    match = re.match(r"^(?P<start>\d{4})-(?P<end>\d{2}|\d{4})$", value)
    if match:
        start_year = int(match.group("start"))
        end_text = match.group("end")
        end_year = int(end_text) + 2000 if len(end_text) == 2 and int(end_text) < 50 else int(end_text)
        end_year = int(end_text) + 1900 if len(end_text) == 2 and int(end_text) >= 50 else end_year
        if end_year != start_year + 1:
            raise ValueError(f"Invalid season value: {value}")
        return int(f"2{start_year}")

    return None


def load_season_ids(engine):
    with engine.connect() as connection:
        return {int(value) for value in connection.execute(text("SELECT season_id FROM seasons")).scalars()}


def load_unique_player_name_map(engine):
    statement = text(
        """
        SELECT p.player_id, p.full_name, a.source_name
        FROM players p
        LEFT JOIN player_name_aliases a ON p.player_id = a.player_id
        """
    )
    normalized_to_ids = {}
    with engine.connect() as connection:
        rows = connection.execute(statement).mappings().all()

    for row in rows:
        for name in (row["full_name"], row["source_name"]):
            normalized = normalize_name(name)
            if normalized:
                normalized_to_ids.setdefault(normalized, set()).add(int(row["player_id"]))

    return {
        normalized: next(iter(player_ids))
        for normalized, player_ids in normalized_to_ids.items()
        if len(player_ids) == 1
    }


def is_truthy_marker(value):
    value = clean_text(value)
    return value is not None and value.lower() in TRUTHY_MARKERS


def award_rows_from_source_row(row, source_file):
    season_id = season_id_from_value(first_present(row, SEASON_COLUMNS))
    if season_id is None:
        return []

    award_type = first_present(row, AWARD_COLUMNS)
    source_player_name = first_present(row, PLAYER_COLUMNS)
    if award_type and source_player_name:
        return [
            {
                "season_id": season_id,
                "award_type": award_type[:60],
                "source_player_name": source_player_name[:160],
                "source_file": source_file,
            }
        ]

    awards = []
    row_player_name = first_present(row, PLAYER_COLUMNS)
    for column, value in row.items():
        column_name = clean_text(column)
        value = clean_text(value)
        if column_name is None or column_name in IGNORED_WIDE_COLUMNS or value is None:
            continue

        if row_player_name:
            if value.lower() in ROW_PLAYER_AWARD_TRUE_VALUES:
                awards.append(
                    {
                        "season_id": season_id,
                        "award_type": column_name[:60],
                        "source_player_name": row_player_name.rstrip("*")[:160],
                        "source_file": source_file,
                    }
                )
            continue

        if not is_truthy_marker(value):
            awards.append(
                {
                    "season_id": season_id,
                    "award_type": column_name[:60],
                    "source_player_name": value[:160],
                    "source_file": source_file,
                }
            )

    return awards


def build_season_awards(engine, source_path):
    if not source_path.exists():
        raise FileNotFoundError(f"Source file not found: {source_path}")

    player_name_map = load_unique_player_name_map(engine)
    awards = {}
    skipped = {
        "missing_player_match": 0,
        "duplicate_award": 0,
        "empty_rows": 0,
    }

    for row in read_csv_rows(source_path):
        parsed_awards = award_rows_from_source_row(row, source_path.name)
        if not parsed_awards:
            skipped["empty_rows"] += 1
            continue

        for award in parsed_awards:
            award["player_id"] = player_name_map.get(normalize_name(award["source_player_name"]))
            if award["player_id"] is None:
                skipped["missing_player_match"] += 1

            key = (award["season_id"], award["award_type"])
            if key in awards:
                skipped["duplicate_award"] += 1
                continue
            awards[key] = award

    return sorted(awards.values(), key=lambda row: (row["season_id"], row["award_type"])), skipped


def validate_foreign_keys(awards, season_ids):
    missing_seasons = sorted({row["season_id"] for row in awards if row["season_id"] not in season_ids})
    if missing_seasons:
        raise ValueError(f"seasons table is missing season_id values required by season_awards: {missing_seasons}")


def insert_season_awards(engine, awards, upsert=False):
    duplicate_clause = (
        """
        ON DUPLICATE KEY UPDATE
            player_id = VALUES(player_id),
            source_player_name = VALUES(source_player_name),
            source_file = VALUES(source_file)
        """
        if upsert
        else ""
    )
    insert_keyword = "INSERT" if upsert else "INSERT IGNORE"
    statement = text(
        f"""
        {insert_keyword} INTO season_awards (
            season_id,
            award_type,
            player_id,
            source_player_name,
            source_file
        )
        VALUES (
            :season_id,
            :award_type,
            :player_id,
            :source_player_name,
            :source_file
        )
        {duplicate_clause}
        """
    )
    with engine.begin() as connection:
        result = connection.execute(statement, awards)
    return result.rowcount


def print_preview(awards, skipped):
    print(f"Prepared {len(awards)} season awards.")
    print(f"Skipped: {skipped}")
    for award in awards[:12]:
        print(
            award["season_id"],
            award["award_type"],
            award["player_id"],
            award["source_player_name"],
            award["source_file"],
        )
    if len(awards) > 12:
        print("...")
        for award in awards[-8:]:
            print(
                award["season_id"],
                award["award_type"],
                award["player_id"],
                award["source_player_name"],
                award["source_file"],
            )


def main():
    parser = argparse.ArgumentParser(description="Clean and load NBA season awards.")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE_FILE), help="Path to NBA_Player_Stats_2.csv.")
    parser.add_argument("--dry-run", action="store_true", help="Only print cleaned award rows.")
    parser.add_argument(
        "--upsert",
        action="store_true",
        help="Update existing awards when duplicates are found. Requires UPDATE permission.",
    )
    args = parser.parse_args()

    source_path = Path(args.source)
    engine = make_engine()
    try:
        awards, skipped = build_season_awards(engine, source_path)
    except FileNotFoundError as exc:
        print(exc)
        sys.exit(1)
    print_preview(awards, skipped)

    if args.dry_run:
        return

    validate_foreign_keys(awards, load_season_ids(engine))
    affected_rows = insert_season_awards(engine, awards, upsert=args.upsert)
    print(f"Inserted season awards. MySQL affected rows: {affected_rows}")


if __name__ == "__main__":
    main()
