import argparse
import os
import re
from datetime import datetime
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
DATA_ROOT = PROJECT_ROOT / "data"

SEASON_LABEL_RE = re.compile(r"^(?P<start>\d{4})-(?P<end>\d{4})$")
YEAR_RE = re.compile(r"^\d{4}$")


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


def season_id_from_start_year(start_year):
    return int(f"2{start_year}")


def label_from_start_year(start_year):
    return f"{start_year}-{start_year + 1}"


def start_year_from_season_id(raw_season_id):
    season_id = str(raw_season_id).strip()
    if len(season_id) != 5 or not season_id.isdigit():
        return None
    return int(season_id[1:])


def parse_game_date(raw_date):
    raw_date = (raw_date or "").strip()
    if not raw_date:
        return None

    for fmt in ("%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw_date, fmt).date()
        except ValueError:
            continue

    raise ValueError(f"Unsupported game_date format: {raw_date}")


def add_season(seasons, start_year, start_date=None, end_date=None):
    if start_year is None:
        return

    start_year = int(start_year)
    end_year = start_year + 1
    season_id = season_id_from_start_year(start_year)
    season = seasons.setdefault(
        season_id,
        {
            "season_id": season_id,
            "season_label": label_from_start_year(start_year),
            "start_year": start_year,
            "end_year": end_year,
            "start_date": None,
            "end_date": None,
        },
    )

    if start_date and (season["start_date"] is None or start_date < season["start_date"]):
        season["start_date"] = start_date
    if end_date and (season["end_date"] is None or end_date > season["end_date"]):
        season["end_date"] = end_date


def collect_game_seasons(seasons):
    game_path = DATA_ROOT / "game" / "game.csv"
    if not game_path.exists():
        return

    for row in read_csv_rows(game_path):
        start_year = start_year_from_season_id(row.get("season_id"))
        game_date = parse_game_date(row.get("game_date"))
        add_season(seasons, start_year, game_date, game_date)


def collect_player_stat_seasons(seasons):
    player_root = DATA_ROOT / "player"
    if not player_root.exists():
        return

    for csv_path in player_root.glob("*.csv"):
        file_match = re.search(r"(?P<label>\d{4}-\d{4})", csv_path.name)
        if file_match:
            add_season_label(seasons, file_match.group("label"))

        for row in read_csv_rows(csv_path):
            label = (row.get("Year") or "").strip()
            if label:
                add_season_label(seasons, label)

            draft_year = (row.get("season") or "").strip()
            if YEAR_RE.match(draft_year):
                add_season(seasons, int(draft_year))


def add_season_label(seasons, label):
    match = SEASON_LABEL_RE.match((label or "").strip())
    if not match:
        return

    start_year = int(match.group("start"))
    end_year = int(match.group("end"))
    if end_year != start_year + 1:
        raise ValueError(f"Invalid season label: {label}")

    add_season(seasons, start_year)


def build_seasons():
    seasons = {}
    collect_game_seasons(seasons)
    collect_player_stat_seasons(seasons)
    return sorted(seasons.values(), key=lambda row: row["season_id"])


def insert_seasons(engine, seasons, upsert=False):
    duplicate_clause = (
        """
        ON DUPLICATE KEY UPDATE
            season_label = VALUES(season_label),
            start_year = VALUES(start_year),
            end_year = VALUES(end_year),
            start_date = VALUES(start_date),
            end_date = VALUES(end_date)
        """
        if upsert
        else ""
    )
    insert_keyword = "INSERT" if upsert else "INSERT IGNORE"
    statement = text(
        f"""
        {insert_keyword} INTO seasons (
            season_id,
            season_label,
            start_year,
            end_year,
            start_date,
            end_date
        )
        VALUES (
            :season_id,
            :season_label,
            :start_year,
            :end_year,
            :start_date,
            :end_date
        )
        {duplicate_clause}
        """
    )
    with engine.begin() as connection:
        result = connection.execute(statement, seasons)
    return result.rowcount


def print_preview(seasons):
    print(f"Prepared {len(seasons)} seasons.")
    if not seasons:
        return

    preview_rows = seasons[:5] + ([{"season_id": "..."}] if len(seasons) > 10 else []) + seasons[-5:]
    for row in preview_rows:
        if row.get("season_id") == "...":
            print("...")
            continue
        print(
            row["season_id"],
            row["season_label"],
            row["start_year"],
            row["end_year"],
            row["start_date"],
            row["end_date"],
        )


def main():
    parser = argparse.ArgumentParser(description="Clean and load NBA season dimension data.")
    parser.add_argument("--dry-run", action="store_true", help="Only print cleaned season rows.")
    parser.add_argument(
        "--upsert",
        action="store_true",
        help="Update existing season rows when duplicates are found. Requires UPDATE permission.",
    )
    args = parser.parse_args()

    seasons = build_seasons()
    print_preview(seasons)

    if args.dry_run:
        return

    affected_rows = insert_seasons(make_engine(), seasons, upsert=args.upsert)
    print(f"Inserted or updated seasons. MySQL affected rows: {affected_rows}")


if __name__ == "__main__":
    main()
