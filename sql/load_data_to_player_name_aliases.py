import argparse
import os
import re
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
PLAYER_ROOT = PROJECT_ROOT / "data" / "player"


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


def clean_text(value):
    if value is not None and not isinstance(value, str):
        value = str(value)
    value = (value or "").strip()
    return value or None


def clean_int(value):
    value = clean_text(value)
    if value is None:
        return None
    return int(float(value))


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


def load_player_id_map(engine):
    statement = text(
        """
        SELECT player_id, nba_person_id, full_name
        FROM players
        WHERE nba_person_id IS NOT NULL
        """
    )
    with engine.connect() as connection:
        rows = connection.execute(statement).mappings().all()

    by_person_id = {}
    normalized_to_player_ids = {}
    for row in rows:
        player_id = int(row["player_id"])
        nba_person_id = int(row["nba_person_id"])
        by_person_id[nba_person_id] = player_id

        normalized = normalize_name(row["full_name"])
        if normalized:
            normalized_to_player_ids.setdefault(normalized, set()).add(player_id)

    return by_person_id, normalized_to_player_ids


def add_alias(aliases, player_id, source_name, source_dataset, is_manual_fix=0):
    source_name = clean_text(source_name)
    source_dataset = clean_text(source_dataset)
    normalized_name = normalize_name(source_name)
    if player_id is None or not source_name or not normalized_name or not source_dataset:
        return

    alias = {
        "player_id": int(player_id),
        "source_name": source_name[:160],
        "normalized_name": normalized_name[:160],
        "source_dataset": source_dataset[:100],
        "is_manual_fix": int(is_manual_fix),
    }
    key = (alias["player_id"], alias["source_name"], alias["source_dataset"])
    aliases[key] = alias


def collect_player_csv_aliases(aliases, person_to_player_id):
    path = PLAYER_ROOT / "player.csv"
    for row in read_csv_rows(path):
        player_id = person_to_player_id.get(clean_int(row.get("id")))
        add_alias(aliases, player_id, row.get("full_name"), "player.csv")


def collect_common_player_info_aliases(aliases, person_to_player_id):
    path = PLAYER_ROOT / "common_player_info.csv"
    name_columns = (
        "display_first_last",
        "display_last_comma_first",
        "display_fi_last",
    )
    for row in read_csv_rows(path):
        player_id = person_to_player_id.get(clean_int(row.get("person_id")))
        for column in name_columns:
            add_alias(aliases, player_id, row.get(column), f"common_player_info.csv:{column}")


def collect_draft_history_aliases(aliases, person_to_player_id):
    path = PLAYER_ROOT / "draft_history.csv"
    for row in read_csv_rows(path):
        player_id = person_to_player_id.get(clean_int(row.get("person_id")))
        add_alias(aliases, player_id, row.get("player_name"), "draft_history.csv")


def build_unique_name_index(person_to_player_id):
    normalized_to_player_ids = {}
    sources = (
        (PLAYER_ROOT / "player.csv", "id", ("full_name",)),
        (
            PLAYER_ROOT / "common_player_info.csv",
            "person_id",
            ("display_first_last", "display_last_comma_first", "display_fi_last"),
        ),
        (PLAYER_ROOT / "draft_history.csv", "person_id", ("player_name",)),
    )

    for csv_path, id_column, name_columns in sources:
        for row in read_csv_rows(csv_path):
            player_id = person_to_player_id.get(clean_int(row.get(id_column)))
            if player_id is None:
                continue
            for column in name_columns:
                normalized = normalize_name(row.get(column))
                if normalized:
                    normalized_to_player_ids.setdefault(normalized, set()).add(player_id)

    return {
        normalized: next(iter(player_ids))
        for normalized, player_ids in normalized_to_player_ids.items()
        if len(player_ids) == 1
    }, {
        normalized: player_ids
        for normalized, player_ids in normalized_to_player_ids.items()
        if len(player_ids) > 1
    }


def collect_stats_name_aliases(aliases, unique_name_to_player_id, ambiguous_names):
    stats_files = [
        PLAYER_ROOT / "NBA_Player_Stats.csv",
        *(PLAYER_ROOT.glob("*NBA Player Stats*.csv")),
    ]
    skipped_ambiguous = {}
    skipped_unmatched = {}

    for csv_path in stats_files:
        if not csv_path.exists():
            continue
        source_dataset = csv_path.name
        for row in read_csv_rows(csv_path):
            source_name = clean_text(row.get("Player"))
            normalized = normalize_name(source_name)
            if not normalized:
                continue

            player_id = unique_name_to_player_id.get(normalized)
            if player_id is None:
                if normalized in ambiguous_names:
                    skipped_ambiguous[normalized] = skipped_ambiguous.get(normalized, 0) + 1
                else:
                    skipped_unmatched[normalized] = skipped_unmatched.get(normalized, 0) + 1
                continue

            add_alias(aliases, player_id, source_name, source_dataset)

    return skipped_ambiguous, skipped_unmatched


def build_player_name_aliases(engine):
    person_to_player_id, _ = load_player_id_map(engine)
    aliases = {}

    collect_player_csv_aliases(aliases, person_to_player_id)
    collect_common_player_info_aliases(aliases, person_to_player_id)
    collect_draft_history_aliases(aliases, person_to_player_id)

    unique_name_to_player_id, ambiguous_names = build_unique_name_index(person_to_player_id)
    skipped_ambiguous, skipped_unmatched = collect_stats_name_aliases(
        aliases,
        unique_name_to_player_id,
        ambiguous_names,
    )

    return (
        sorted(aliases.values(), key=lambda row: (row["player_id"], row["source_dataset"], row["source_name"])),
        skipped_ambiguous,
        skipped_unmatched,
    )


def insert_player_name_aliases(engine, aliases, upsert=False):
    duplicate_clause = (
        """
        ON DUPLICATE KEY UPDATE
            normalized_name = VALUES(normalized_name),
            is_manual_fix = VALUES(is_manual_fix)
        """
        if upsert
        else ""
    )
    insert_keyword = "INSERT" if upsert else "INSERT IGNORE"
    statement = text(
        f"""
        {insert_keyword} INTO player_name_aliases (
            player_id,
            source_name,
            normalized_name,
            source_dataset,
            is_manual_fix
        )
        VALUES (
            :player_id,
            :source_name,
            :normalized_name,
            :source_dataset,
            :is_manual_fix
        )
        {duplicate_clause}
        """
    )
    with engine.begin() as connection:
        result = connection.execute(statement, aliases)
    return result.rowcount


def print_preview(aliases, skipped_ambiguous, skipped_unmatched):
    print(f"Prepared {len(aliases)} player name aliases.")
    print(f"Skipped ambiguous stat names: {len(skipped_ambiguous)}.")
    print(f"Skipped unmatched stat names: {len(skipped_unmatched)}.")
    for alias in aliases[:10]:
        print(
            alias["player_id"],
            alias["source_name"],
            alias["normalized_name"],
            alias["source_dataset"],
            alias["is_manual_fix"],
        )
    if len(aliases) > 10:
        print("...")
        for alias in aliases[-5:]:
            print(
                alias["player_id"],
                alias["source_name"],
                alias["normalized_name"],
                alias["source_dataset"],
                alias["is_manual_fix"],
            )


def main():
    parser = argparse.ArgumentParser(description="Clean and load player name aliases.")
    parser.add_argument("--dry-run", action="store_true", help="Only print cleaned alias rows.")
    parser.add_argument(
        "--upsert",
        action="store_true",
        help="Update existing alias rows when duplicates are found. Requires UPDATE permission.",
    )
    args = parser.parse_args()

    engine = make_engine()
    aliases, skipped_ambiguous, skipped_unmatched = build_player_name_aliases(engine)
    print_preview(aliases, skipped_ambiguous, skipped_unmatched)

    if args.dry_run:
        return

    affected_rows = insert_player_name_aliases(engine, aliases, upsert=args.upsert)
    print(f"Inserted player name aliases. MySQL affected rows: {affected_rows}")


if __name__ == "__main__":
    main()
