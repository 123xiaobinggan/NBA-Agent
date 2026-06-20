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
TEAM_CSV = PROJECT_ROOT / "data" / "team" / "team.csv"

STATE_FIXES = {
    "ATL": "Georgia",
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
    value = (value or "").strip()
    return value or None


def clean_int(value):
    value = clean_text(value)
    if value is None:
        return None
    return int(float(value))


def clean_team_row(row):
    abbreviation = clean_text(row.get("abbreviation"))
    team_id = clean_int(row.get("id"))
    full_name = clean_text(row.get("full_name"))

    if team_id is None:
        raise ValueError(f"Missing team_id: {row}")
    if not full_name:
        raise ValueError(f"Missing full_name for team_id={team_id}")
    if not abbreviation:
        raise ValueError(f"Missing abbreviation for team_id={team_id}")

    return {
        "team_id": team_id,
        "full_name": full_name,
        "abbreviation": abbreviation,
        "nickname": clean_text(row.get("nickname")),
        "city": clean_text(row.get("city")),
        "state": STATE_FIXES.get(abbreviation, clean_text(row.get("state"))),
        "year_founded": clean_int(row.get("year_founded")),
    }


def build_teams():
    teams = {}
    for row in read_csv_rows(TEAM_CSV):
        team = clean_team_row(row)
        existing = teams.get(team["team_id"])
        if existing and existing["abbreviation"] != team["abbreviation"]:
            raise ValueError(f"Duplicate team_id with different abbreviation: {team['team_id']}")
        teams[team["team_id"]] = team

    abbreviations = {}
    for team in teams.values():
        existing_team_id = abbreviations.get(team["abbreviation"])
        if existing_team_id and existing_team_id != team["team_id"]:
            raise ValueError(f"Duplicate abbreviation: {team['abbreviation']}")
        abbreviations[team["abbreviation"]] = team["team_id"]

    return sorted(teams.values(), key=lambda row: row["team_id"])


def insert_teams(engine, teams, upsert=False):
    duplicate_clause = (
        """
        ON DUPLICATE KEY UPDATE
            full_name = VALUES(full_name),
            abbreviation = VALUES(abbreviation),
            nickname = VALUES(nickname),
            city = VALUES(city),
            state = VALUES(state),
            year_founded = VALUES(year_founded)
        """
        if upsert
        else ""
    )
    insert_keyword = "INSERT" if upsert else "INSERT IGNORE"
    statement = text(
        f"""
        {insert_keyword} INTO teams (
            team_id,
            full_name,
            abbreviation,
            nickname,
            city,
            state,
            year_founded
        )
        VALUES (
            :team_id,
            :full_name,
            :abbreviation,
            :nickname,
            :city,
            :state,
            :year_founded
        )
        {duplicate_clause}
        """
    )
    with engine.begin() as connection:
        result = connection.execute(statement, teams)
    return result.rowcount


def print_preview(teams):
    print(f"Prepared {len(teams)} teams.")
    for team in teams:
        print(
            team["team_id"],
            team["abbreviation"],
            team["full_name"],
            team["city"],
            team["state"],
            team["year_founded"],
        )


def main():
    parser = argparse.ArgumentParser(description="Clean and load NBA team dimension data.")
    parser.add_argument("--dry-run", action="store_true", help="Only print cleaned team rows.")
    parser.add_argument(
        "--upsert",
        action="store_true",
        help="Update existing team rows when duplicates are found. Requires UPDATE permission.",
    )
    args = parser.parse_args()

    teams = build_teams()
    print_preview(teams)

    if args.dry_run:
        return

    affected_rows = insert_teams(make_engine(), teams, upsert=args.upsert)
    print(f"Inserted teams. MySQL affected rows: {affected_rows}")


if __name__ == "__main__":
    main()
