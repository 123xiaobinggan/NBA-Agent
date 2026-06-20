import argparse
import os
import re
import sys
import unicodedata
from decimal import Decimal, InvalidOperation
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
DEFAULT_SOURCE_FILE = PROJECT_ROOT / "data" / "player" / "draft_combine_stats.csv"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


COLUMN_ALIASES = {
    "draft_year": ("draft_year", "season", "Season", "YEAR", "year"),
    "source_player_id": ("player_id", "person_id", "PLAYER_ID", "nba_person_id"),
    "player_name": ("player_name", "Player", "PLAYER", "name", "Name"),
    "first_name": ("first_name", "FIRST_NAME", "First Name"),
    "last_name": ("last_name", "LAST_NAME", "Last Name"),
    "position": ("position", "Pos", "POS", "POSITION"),
    "height_without_shoes_in": (
        "height_without_shoes",
        "height_wo_shoes",
        "height_no_shoes",
        "HEIGHT_WO_SHOES",
        "height_without_shoes_in",
    ),
    "height_with_shoes_in": (
        "height_with_shoes",
        "height_w_shoes",
        "HEIGHT_W_SHOES",
        "height_with_shoes_in",
    ),
    "weight_lb": ("weight", "weight_lbs", "WEIGHT", "weight_lb"),
    "wingspan_in": ("wingspan", "WINGSPAN", "wingspan_in"),
    "standing_reach_in": ("standing_reach", "STANDING_REACH", "standing_reach_in"),
    "body_fat_pct": ("body_fat_pct", "BODY_FAT_PCT", "body_fat", "Body Fat %"),
    "hand_length_in": ("hand_length", "HAND_LENGTH", "hand_length_in"),
    "hand_width_in": ("hand_width", "HAND_WIDTH", "hand_width_in"),
    "standing_vertical_leap_in": (
        "standing_vertical_leap",
        "STANDING_VERTICAL_LEAP",
        "standing_vertical",
        "standing_vertical_leap_in",
    ),
    "max_vertical_leap_in": (
        "max_vertical_leap",
        "MAX_VERTICAL_LEAP",
        "max_vertical",
        "max_vertical_leap_in",
    ),
    "lane_agility_time_sec": (
        "lane_agility_time",
        "LANE_AGILITY_TIME",
        "lane_agility",
        "lane_agility_time_sec",
    ),
    "modified_lane_agility_sec": (
        "modified_lane_agility_time",
        "MODIFIED_LANE_AGILITY_TIME",
        "modified_lane_agility",
        "modified_lane_agility_sec",
    ),
    "three_quarter_sprint_sec": (
        "three_quarter_sprint",
        "THREE_QUARTER_SPRINT",
        "three_quarter_sprint_sec",
    ),
    "bench_press_reps": ("bench_press", "BENCH_PRESS", "bench_press_reps"),
}


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
    if value.lower() in {"", "nan", "none", "null", "--"}:
        return None
    return value


def get_value(row, logical_name):
    for column in COLUMN_ALIASES[logical_name]:
        value = clean_text(row.get(column))
        if value is not None:
            return value
    return None


def clean_decimal(value, scale="0.01"):
    value = clean_text(value)
    if value is None:
        return None
    value = value.replace("%", "").replace('"', "").strip()
    try:
        return Decimal(value).quantize(Decimal(scale))
    except InvalidOperation:
        return None


def clean_int(value):
    number = clean_decimal(value, "1")
    if number is None:
        return None
    return int(number)


def clean_measurement_inches(value):
    value = clean_text(value)
    if value is None:
        return None

    normalized = value.lower().replace("feet", "'").replace("foot", "'").replace("inches", '"').replace("inch", '"')
    normalized = normalized.replace("''", '"').replace("’", "'").replace("”", '"')
    match = re.match(r"^\s*(?P<feet>\d+)\s*[-']\s*(?P<inches>\d+(?:\.\d+)?)", normalized)
    if match:
        feet = Decimal(match.group("feet"))
        inches = Decimal(match.group("inches"))
        return (feet * Decimal("12") + inches).quantize(Decimal("0.01"))

    return clean_decimal(value, "0.01")


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


def load_player_maps(engine):
    statement = text(
        """
        SELECT p.player_id, p.nba_person_id, p.full_name, a.source_name
        FROM players p
        LEFT JOIN player_name_aliases a ON p.player_id = a.player_id
        """
    )
    by_person_id = {}
    normalized_to_ids = {}
    with engine.connect() as connection:
        rows = connection.execute(statement).mappings().all()

    for row in rows:
        player_id = int(row["player_id"])
        if row["nba_person_id"] is not None:
            by_person_id[int(row["nba_person_id"])] = player_id
        for name in (row["full_name"], row["source_name"]):
            normalized = normalize_name(name)
            if normalized:
                normalized_to_ids.setdefault(normalized, set()).add(player_id)

    unique_name_map = {
        normalized: next(iter(player_ids))
        for normalized, player_ids in normalized_to_ids.items()
        if len(player_ids) == 1
    }
    return by_person_id, unique_name_map


def build_player_name(row):
    player_name = get_value(row, "player_name")
    if player_name:
        return player_name

    first_name = get_value(row, "first_name")
    last_name = get_value(row, "last_name")
    return " ".join(part for part in (first_name, last_name) if part) or None


def build_record(row, source_file, by_person_id, unique_name_map):
    draft_year = clean_int(get_value(row, "draft_year"))
    source_player_id = clean_int(get_value(row, "source_player_id"))
    if source_player_id is not None and source_player_id <= 0:
        source_player_id = None
    player_name = build_player_name(row)

    if draft_year is None:
        return None, "missing_draft_year"
    if player_name is None:
        return None, "missing_player_name"

    player_id = by_person_id.get(source_player_id)
    if player_id is None:
        player_id = unique_name_map.get(normalize_name(player_name))

    return {
        "draft_year": draft_year,
        "source_player_id": source_player_id,
        "player_id": player_id,
        "player_name": player_name,
        "position": get_value(row, "position"),
        "height_without_shoes_in": clean_measurement_inches(get_value(row, "height_without_shoes_in")),
        "height_with_shoes_in": clean_measurement_inches(get_value(row, "height_with_shoes_in")),
        "weight_lb": clean_decimal(get_value(row, "weight_lb")),
        "wingspan_in": clean_measurement_inches(get_value(row, "wingspan_in")),
        "standing_reach_in": clean_measurement_inches(get_value(row, "standing_reach_in")),
        "body_fat_pct": clean_decimal(get_value(row, "body_fat_pct")),
        "hand_length_in": clean_measurement_inches(get_value(row, "hand_length_in")),
        "hand_width_in": clean_measurement_inches(get_value(row, "hand_width_in")),
        "standing_vertical_leap_in": clean_decimal(get_value(row, "standing_vertical_leap_in")),
        "max_vertical_leap_in": clean_decimal(get_value(row, "max_vertical_leap_in")),
        "lane_agility_time_sec": clean_decimal(get_value(row, "lane_agility_time_sec")),
        "modified_lane_agility_sec": clean_decimal(get_value(row, "modified_lane_agility_sec")),
        "three_quarter_sprint_sec": clean_decimal(get_value(row, "three_quarter_sprint_sec")),
        "bench_press_reps": clean_int(get_value(row, "bench_press_reps")),
        "source_file": source_file,
    }, None


def row_signature(record):
    return tuple((key, record.get(key)) for key in sorted(record))


def build_combine_measurements(engine, source_path):
    if not source_path.exists():
        raise FileNotFoundError(f"Source file not found: {source_path}")

    by_person_id, unique_name_map = load_player_maps(engine)
    records = {}
    signatures = set()
    skipped = {
        "duplicate_full_rows": 0,
        "duplicate_source_player_year": 0,
        "missing_draft_year": 0,
        "missing_player_name": 0,
        "missing_player_match": 0,
    }

    for row in read_csv_rows(source_path):
        record, skip_reason = build_record(row, source_path.name, by_person_id, unique_name_map)
        if record is None:
            skipped[skip_reason] += 1
            continue

        signature = row_signature(record)
        if signature in signatures:
            skipped["duplicate_full_rows"] += 1
            continue
        signatures.add(signature)

        if record["player_id"] is None:
            skipped["missing_player_match"] += 1

        natural_key = (
            record["source_player_id"],
            record["draft_year"],
        )
        if record["source_player_id"] is None:
            natural_key = (
                normalize_name(record["player_name"]),
                record["draft_year"],
            )

        if natural_key in records:
            skipped["duplicate_source_player_year"] += 1
            continue
        records[natural_key] = record

    return sorted(records.values(), key=lambda row: (row["draft_year"], row["player_name"])), skipped


def combine_exists(connection, record):
    if record["source_player_id"] is not None:
        statement = text(
            """
            SELECT 1
            FROM draft_combine_measurements
            WHERE source_player_id = :source_player_id
              AND draft_year = :draft_year
            LIMIT 1
            """
        )
        return connection.execute(statement, record).first() is not None

    statement = text(
        """
        SELECT 1
        FROM draft_combine_measurements
        WHERE source_player_id IS NULL
          AND draft_year = :draft_year
          AND player_name = :player_name
        LIMIT 1
        """
    )
    return connection.execute(statement, record).first() is not None


def insert_combine_measurements(engine, records):
    statement = text(
        """
        INSERT INTO draft_combine_measurements (
            draft_year,
            source_player_id,
            player_id,
            player_name,
            position,
            height_without_shoes_in,
            height_with_shoes_in,
            weight_lb,
            wingspan_in,
            standing_reach_in,
            body_fat_pct,
            hand_length_in,
            hand_width_in,
            standing_vertical_leap_in,
            max_vertical_leap_in,
            lane_agility_time_sec,
            modified_lane_agility_sec,
            three_quarter_sprint_sec,
            bench_press_reps,
            source_file
        )
        VALUES (
            :draft_year,
            :source_player_id,
            :player_id,
            :player_name,
            :position,
            :height_without_shoes_in,
            :height_with_shoes_in,
            :weight_lb,
            :wingspan_in,
            :standing_reach_in,
            :body_fat_pct,
            :hand_length_in,
            :hand_width_in,
            :standing_vertical_leap_in,
            :max_vertical_leap_in,
            :lane_agility_time_sec,
            :modified_lane_agility_sec,
            :three_quarter_sprint_sec,
            :bench_press_reps,
            :source_file
        )
        """
    )
    inserted = 0
    skipped_existing = 0
    with engine.begin() as connection:
        for record in records:
            if combine_exists(connection, record):
                skipped_existing += 1
                continue
            connection.execute(statement, record)
            inserted += 1
    return inserted, skipped_existing


def print_preview(records, skipped):
    print(f"Prepared {len(records)} draft combine measurements.")
    print(f"Skipped: {skipped}")
    for record in records[:10]:
        print(
            record["draft_year"],
            record["source_player_id"],
            record["player_id"],
            record["player_name"],
            record["position"],
            record["height_without_shoes_in"],
            record["height_with_shoes_in"],
            record["weight_lb"],
            record["wingspan_in"],
        )
    if len(records) > 10:
        print("...")
        for record in records[-8:]:
            print(
                record["draft_year"],
                record["source_player_id"],
                record["player_id"],
                record["player_name"],
                record["position"],
                record["height_without_shoes_in"],
                record["height_with_shoes_in"],
                record["weight_lb"],
                record["wingspan_in"],
            )


def main():
    parser = argparse.ArgumentParser(description="Clean and load NBA draft combine measurements.")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE_FILE), help="Path to draft_combine_stats.csv.")
    parser.add_argument("--dry-run", action="store_true", help="Only print cleaned combine rows.")
    args = parser.parse_args()

    source_path = Path(args.source)
    engine = make_engine()
    try:
        records, skipped = build_combine_measurements(engine, source_path)
    except FileNotFoundError as exc:
        print(exc)
        sys.exit(1)

    print_preview(records, skipped)
    if args.dry_run:
        return

    inserted, skipped_existing = insert_combine_measurements(engine, records)
    print(f"Inserted draft combine measurements: {inserted}. Skipped existing rows: {skipped_existing}.")


if __name__ == "__main__":
    main()
