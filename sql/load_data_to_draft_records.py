import argparse
import os
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
DRAFT_HISTORY_CSV = PROJECT_ROOT / "data" / "player" / "draft_history.csv"
SOURCE_FILE = DRAFT_HISTORY_CSV.name


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


def load_player_id_map(engine):
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT player_id, nba_person_id FROM players WHERE nba_person_id IS NOT NULL")
        ).mappings()
        return {int(row["nba_person_id"]): int(row["player_id"]) for row in rows}


def row_signature(row):
    return tuple((key, clean_text(value)) for key, value in sorted(row.items()))


def build_draft_records(engine):
    person_to_player_id = load_player_id_map(engine)
    records = {}
    signatures = set()
    conflicts = {}
    skipped = {
        "duplicate_full_rows": 0,
        "person_year_conflicts": 0,
        "missing_person_id": 0,
        "missing_player_name": 0,
        "missing_draft_year": 0,
        "missing_player_match": 0,
    }

    for row in read_csv_rows(DRAFT_HISTORY_CSV):
        signature = row_signature(row)
        if signature in signatures:
            skipped["duplicate_full_rows"] += 1
            continue
        signatures.add(signature)

        source_person_id = clean_int(row.get("person_id"))
        player_name = clean_text(row.get("player_name"))
        draft_year = clean_int(row.get("season"))

        if source_person_id is None:
            skipped["missing_person_id"] += 1
            continue
        if player_name is None:
            skipped["missing_player_name"] += 1
            continue
        if draft_year is None:
            skipped["missing_draft_year"] += 1
            continue

        key = (source_person_id, draft_year)
        record = {
            "source_person_id": source_person_id,
            "player_id": person_to_player_id.get(source_person_id),
            "player_name": player_name,
            "draft_year": draft_year,
            "round_number": clean_int(row.get("round_number")),
            "round_pick": clean_int(row.get("round_pick")),
            "overall_pick": clean_int(row.get("overall_pick")),
            "draft_type": clean_text(row.get("draft_type")),
            "draft_team_id": clean_int(row.get("team_id")),
            "team_city": clean_text(row.get("team_city")),
            "team_name": clean_text(row.get("team_name")),
            "team_abbreviation": clean_text(row.get("team_abbreviation")),
            "organization": clean_text(row.get("organization")),
            "organization_type": clean_text(row.get("organization_type")),
            "source_file": SOURCE_FILE,
        }

        if record["player_id"] is None:
            skipped["missing_player_match"] += 1

        existing = records.get(key)
        if existing is not None:
            if existing != record:
                conflicts[key] = (existing, record)
                skipped["person_year_conflicts"] += 1
            continue

        records[key] = record

    if conflicts:
        sample_key, (left, right) = next(iter(conflicts.items()))
        raise ValueError(f"Conflicting draft rows for person_id/year {sample_key}: {left} vs {right}")

    return sorted(records.values(), key=lambda row: (row["draft_year"], row["overall_pick"] or 9999, row["player_name"])), skipped


def insert_draft_records(engine, records, upsert=False):
    duplicate_clause = (
        """
        ON DUPLICATE KEY UPDATE
            player_id = VALUES(player_id),
            player_name = VALUES(player_name),
            round_number = VALUES(round_number),
            round_pick = VALUES(round_pick),
            overall_pick = VALUES(overall_pick),
            draft_type = VALUES(draft_type),
            draft_team_id = VALUES(draft_team_id),
            team_city = VALUES(team_city),
            team_name = VALUES(team_name),
            team_abbreviation = VALUES(team_abbreviation),
            organization = VALUES(organization),
            organization_type = VALUES(organization_type),
            source_file = VALUES(source_file)
        """
        if upsert
        else ""
    )
    insert_keyword = "INSERT" if upsert else "INSERT IGNORE"
    statement = text(
        f"""
        {insert_keyword} INTO draft_records (
            source_person_id,
            player_id,
            player_name,
            draft_year,
            round_number,
            round_pick,
            overall_pick,
            draft_type,
            draft_team_id,
            team_city,
            team_name,
            team_abbreviation,
            organization,
            organization_type,
            source_file
        )
        VALUES (
            :source_person_id,
            :player_id,
            :player_name,
            :draft_year,
            :round_number,
            :round_pick,
            :overall_pick,
            :draft_type,
            :draft_team_id,
            :team_city,
            :team_name,
            :team_abbreviation,
            :organization,
            :organization_type,
            :source_file
        )
        {duplicate_clause}
        """
    )
    with engine.begin() as connection:
        result = connection.execute(statement, records)
    return result.rowcount


def print_preview(records, skipped):
    print(f"Prepared {len(records)} draft records.")
    print(f"Skipped: {skipped}")
    for record in records[:10]:
        print(
            record["source_person_id"],
            record["player_id"],
            record["player_name"],
            record["draft_year"],
            record["round_number"],
            record["round_pick"],
            record["overall_pick"],
            record["draft_type"],
            record["draft_team_id"],
            record["team_abbreviation"],
        )
    if len(records) > 10:
        print("...")
        for record in records[-8:]:
            print(
                record["source_person_id"],
                record["player_id"],
                record["player_name"],
                record["draft_year"],
                record["round_number"],
                record["round_pick"],
                record["overall_pick"],
                record["draft_type"],
                record["draft_team_id"],
                record["team_abbreviation"],
            )


def main():
    parser = argparse.ArgumentParser(description="Clean and load NBA draft records.")
    parser.add_argument("--dry-run", action="store_true", help="Only print cleaned draft records.")
    parser.add_argument(
        "--upsert",
        action="store_true",
        help="Update existing draft records when duplicates are found. Requires UPDATE permission.",
    )
    args = parser.parse_args()

    engine = make_engine()
    records, skipped = build_draft_records(engine)
    print_preview(records, skipped)

    if args.dry_run:
        return

    affected_rows = insert_draft_records(engine, records, upsert=args.upsert)
    print(f"Inserted draft records. MySQL affected rows: {affected_rows}")


if __name__ == "__main__":
    main()
