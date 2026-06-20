import argparse
import os
from datetime import datetime
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
PLAYER_ROOT = PROJECT_ROOT / "data" / "player"

LB_TO_KG = Decimal("0.45359237")
INCH_TO_CM = Decimal("2.54")


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


def clean_decimal(value):
    value = clean_text(value)
    if value is None:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def clean_int(value):
    number = clean_decimal(value)
    if number is None:
        return None
    return int(number)


def clean_bool(value):
    value = clean_text(value)
    if value is None:
        return 0
    return 1 if value.upper() in {"1", "Y", "YES", "TRUE", "ACTIVE"} else 0


def clean_date(value):
    value = clean_text(value)
    if value is None:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported date format: {value}")


def clean_height_cm(height_text):
    height_text = clean_text(height_text)
    if not height_text or "-" not in height_text:
        return None
    feet, inches = height_text.split("-", 1)
    feet_int = clean_int(feet)
    inches_int = clean_int(inches)
    if feet_int is None or inches_int is None:
        return None
    total_inches = Decimal(feet_int * 12 + inches_int)
    return (total_inches * INCH_TO_CM).quantize(Decimal("0.01"))


def clean_weight_kg(weight_lb):
    if weight_lb is None:
        return None
    return (weight_lb * LB_TO_KG).quantize(Decimal("0.01"))


def clean_draft_value(value):
    value = clean_text(value)
    if value is None or value.lower() == "undrafted":
        return None
    return clean_int(value)


def draft_status_from_values(draft_year, draft_round, draft_number):
    raw_values = {clean_text(draft_year), clean_text(draft_round), clean_text(draft_number)}
    lowered = {value.lower() for value in raw_values if value}
    if "undrafted" in lowered:
        return "UNDRAFTED"
    if any(clean_draft_value(value) is not None for value in raw_values):
        return "DRAFTED"
    return "UNKNOWN"


def empty_player(nba_person_id):
    return {
        "nba_person_id": nba_person_id,
        "full_name": None,
        "first_name": None,
        "last_name": None,
        "player_slug": None,
        "birthdate": None,
        "school": None,
        "country": None,
        "last_affiliation": None,
        "height_text": None,
        "height_cm": None,
        "weight_lb": None,
        "weight_kg": None,
        "primary_position": None,
        "from_year": None,
        "to_year": None,
        "season_experience": None,
        "is_active": 0,
        "greatest_75_flag": 0,
        "draft_status": "UNKNOWN",
        "draft_year": None,
        "draft_round": None,
        "draft_number": None,
    }


def merge_value(player, key, value, overwrite=False):
    if value is None:
        return
    if overwrite or player.get(key) is None:
        player[key] = value


def collect_common_player_info(players):
    path = PLAYER_ROOT / "common_player_info.csv"
    for row in read_csv_rows(path):
        nba_person_id = clean_int(row.get("person_id"))
        if nba_person_id is None:
            continue

        player = players.setdefault(nba_person_id, empty_player(nba_person_id))
        weight_lb = clean_decimal(row.get("weight"))
        draft_status = draft_status_from_values(row.get("draft_year"), row.get("draft_round"), row.get("draft_number"))

        merge_value(player, "first_name", clean_text(row.get("first_name")), overwrite=True)
        merge_value(player, "last_name", clean_text(row.get("last_name")), overwrite=True)
        merge_value(player, "full_name", clean_text(row.get("display_first_last")), overwrite=True)
        merge_value(player, "player_slug", clean_text(row.get("player_slug")), overwrite=True)
        merge_value(player, "birthdate", clean_date(row.get("birthdate")), overwrite=True)
        merge_value(player, "school", clean_text(row.get("school")), overwrite=True)
        merge_value(player, "country", clean_text(row.get("country")), overwrite=True)
        merge_value(player, "last_affiliation", clean_text(row.get("last_affiliation")), overwrite=True)
        merge_value(player, "height_text", clean_text(row.get("height")), overwrite=True)
        merge_value(player, "height_cm", clean_height_cm(row.get("height")), overwrite=True)
        merge_value(player, "weight_lb", weight_lb, overwrite=True)
        merge_value(player, "weight_kg", clean_weight_kg(weight_lb), overwrite=True)
        merge_value(player, "primary_position", clean_text(row.get("position")), overwrite=True)
        merge_value(player, "from_year", clean_int(row.get("from_year")), overwrite=True)
        merge_value(player, "to_year", clean_int(row.get("to_year")), overwrite=True)
        merge_value(player, "season_experience", clean_int(row.get("season_exp")), overwrite=True)
        merge_value(player, "draft_status", draft_status, overwrite=True)
        merge_value(player, "draft_year", clean_draft_value(row.get("draft_year")), overwrite=True)
        merge_value(player, "draft_round", clean_draft_value(row.get("draft_round")), overwrite=True)
        merge_value(player, "draft_number", clean_draft_value(row.get("draft_number")), overwrite=True)
        player["is_active"] = 1 if clean_text(row.get("rosterstatus")) == "Active" else player["is_active"]
        player["greatest_75_flag"] = clean_bool(row.get("greatest_75_flag"))


def collect_player_csv(players):
    path = PLAYER_ROOT / "player.csv"
    for row in read_csv_rows(path):
        nba_person_id = clean_int(row.get("id"))
        if nba_person_id is None:
            continue

        player = players.setdefault(nba_person_id, empty_player(nba_person_id))
        merge_value(player, "full_name", clean_text(row.get("full_name")))
        merge_value(player, "first_name", clean_text(row.get("first_name")))
        merge_value(player, "last_name", clean_text(row.get("last_name")))
        player["is_active"] = max(player["is_active"], clean_bool(row.get("is_active")))


def draft_row_quality(row):
    overall_pick = clean_int(row.get("overall_pick"))
    round_number = clean_int(row.get("round_number"))
    season = clean_int(row.get("season"))
    return (
        0 if overall_pick and overall_pick > 0 else 1,
        overall_pick if overall_pick and overall_pick > 0 else 9999,
        round_number if round_number is not None else 9999,
        season if season is not None else 9999,
    )


def collect_draft_history(players):
    path = PLAYER_ROOT / "draft_history.csv"
    best_rows = {}
    for row in read_csv_rows(path):
        nba_person_id = clean_int(row.get("person_id"))
        if nba_person_id is None:
            continue
        current = best_rows.get(nba_person_id)
        if current is None or draft_row_quality(row) < draft_row_quality(current):
            best_rows[nba_person_id] = row

    for nba_person_id, row in best_rows.items():
        player = players.setdefault(nba_person_id, empty_player(nba_person_id))
        draft_year = clean_int(row.get("season"))
        draft_round = clean_int(row.get("round_number"))
        draft_number = clean_int(row.get("overall_pick"))

        merge_value(player, "full_name", clean_text(row.get("player_name")))
        merge_value(player, "school", clean_text(row.get("organization")))
        merge_value(player, "draft_status", "DRAFTED", overwrite=player["draft_status"] == "UNKNOWN")
        merge_value(player, "draft_year", draft_year)
        merge_value(player, "draft_round", draft_round)
        merge_value(player, "draft_number", draft_number)


def split_missing_names(players):
    skipped = []
    for nba_person_id, player in list(players.items()):
        if not player["full_name"]:
            skipped.append(nba_person_id)
            del players[nba_person_id]
            continue
        if not player["first_name"] or not player["last_name"]:
            parts = player["full_name"].split(" ", 1)
            merge_value(player, "first_name", parts[0])
            merge_value(player, "last_name", parts[1] if len(parts) > 1 else None)
    return skipped


def build_players():
    players = {}
    collect_common_player_info(players)
    collect_player_csv(players)
    collect_draft_history(players)
    skipped_missing_names = split_missing_names(players)
    return sorted(players.values(), key=lambda row: row["nba_person_id"]), skipped_missing_names


def insert_players(engine, players, upsert=False):
    duplicate_clause = (
        """
        ON DUPLICATE KEY UPDATE
            full_name = VALUES(full_name),
            first_name = VALUES(first_name),
            last_name = VALUES(last_name),
            player_slug = VALUES(player_slug),
            birthdate = VALUES(birthdate),
            school = VALUES(school),
            country = VALUES(country),
            last_affiliation = VALUES(last_affiliation),
            height_text = VALUES(height_text),
            height_cm = VALUES(height_cm),
            weight_lb = VALUES(weight_lb),
            weight_kg = VALUES(weight_kg),
            primary_position = VALUES(primary_position),
            from_year = VALUES(from_year),
            to_year = VALUES(to_year),
            season_experience = VALUES(season_experience),
            is_active = VALUES(is_active),
            greatest_75_flag = VALUES(greatest_75_flag),
            draft_status = VALUES(draft_status),
            draft_year = VALUES(draft_year),
            draft_round = VALUES(draft_round),
            draft_number = VALUES(draft_number)
        """
        if upsert
        else ""
    )
    insert_keyword = "INSERT" if upsert else "INSERT IGNORE"
    statement = text(
        f"""
        {insert_keyword} INTO players (
            nba_person_id,
            full_name,
            first_name,
            last_name,
            player_slug,
            birthdate,
            school,
            country,
            last_affiliation,
            height_text,
            height_cm,
            weight_lb,
            weight_kg,
            primary_position,
            from_year,
            to_year,
            season_experience,
            is_active,
            greatest_75_flag,
            draft_status,
            draft_year,
            draft_round,
            draft_number
        )
        VALUES (
            :nba_person_id,
            :full_name,
            :first_name,
            :last_name,
            :player_slug,
            :birthdate,
            :school,
            :country,
            :last_affiliation,
            :height_text,
            :height_cm,
            :weight_lb,
            :weight_kg,
            :primary_position,
            :from_year,
            :to_year,
            :season_experience,
            :is_active,
            :greatest_75_flag,
            :draft_status,
            :draft_year,
            :draft_round,
            :draft_number
        )
        {duplicate_clause}
        """
    )
    with engine.begin() as connection:
        result = connection.execute(statement, players)
    return result.rowcount


def print_preview(players, skipped_missing_names):
    print(f"Prepared {len(players)} players.")
    print(f"Skipped {len(skipped_missing_names)} rows because full_name is missing.")
    for player in players[:8]:
        print(
            player["nba_person_id"],
            player["full_name"],
            player["height_text"],
            player["height_cm"],
            player["weight_lb"],
            player["weight_kg"],
            player["draft_status"],
            player["draft_year"],
            player["draft_round"],
            player["draft_number"],
        )
    if len(players) > 8:
        print("...")
        for player in players[-5:]:
            print(
                player["nba_person_id"],
                player["full_name"],
                player["height_text"],
                player["height_cm"],
                player["weight_lb"],
                player["weight_kg"],
                player["draft_status"],
                player["draft_year"],
                player["draft_round"],
                player["draft_number"],
            )


def main():
    parser = argparse.ArgumentParser(description="Clean and load NBA player profile data.")
    parser.add_argument("--dry-run", action="store_true", help="Only print cleaned player rows.")
    parser.add_argument(
        "--upsert",
        action="store_true",
        help="Update existing player rows when duplicates are found. Requires UPDATE permission.",
    )
    args = parser.parse_args()

    players, skipped_missing_names = build_players()
    print_preview(players, skipped_missing_names)

    if args.dry_run:
        return

    affected_rows = insert_players(make_engine(), players, upsert=args.upsert)
    print(f"Inserted players. MySQL affected rows: {affected_rows}")


if __name__ == "__main__":
    main()
