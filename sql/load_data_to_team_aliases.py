import argparse
import os
import re
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

SEASON_LABEL_RE = re.compile(r"(?P<start>\d{4})-(?P<end>\d{4})")

BBREF_CODE_OVERRIDES = {
    "BRK": "BKN",
    "CHO": "CHA",
    "PHO": "PHX",
}

SKIP_PLAYER_CODES = {"TOT"}


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


def season_id_from_year(year):
    if year is None:
        return None
    return int(f"2{int(year)}")


def load_team_rows():
    teams = {}
    team_path = DATA_ROOT / "team" / "team.csv"
    for row in read_csv_rows(team_path):
        team_id = clean_int(row.get("id"))
        abbreviation = clean_text(row.get("abbreviation"))
        if team_id is None or not abbreviation:
            continue
        teams[team_id] = {
            "team_id": team_id,
            "full_name": clean_text(row.get("full_name")),
            "abbreviation": abbreviation,
            "nickname": clean_text(row.get("nickname")),
            "city": clean_text(row.get("city")),
            "year_founded": clean_int(row.get("year_founded")),
        }
    return teams


def load_db_team_ids(engine):
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT team_id FROM teams")).scalars().all()
    return {int(team_id) for team_id in rows}


def add_alias(aliases, team_id, alias_code, alias_name, source_name, valid_from=None, valid_to=None):
    team_id = clean_int(team_id)
    alias_code = clean_text(alias_code)
    source_name = clean_text(source_name)
    alias_name = clean_text(alias_name)

    if team_id is None or not alias_code or not source_name:
        return

    alias = {
        "team_id": team_id,
        "alias_code": alias_code,
        "alias_name": alias_name,
        "source_name": source_name,
        "valid_from_season": valid_from,
        "valid_to_season": valid_to,
    }
    key = (
        alias["team_id"],
        alias["alias_code"],
        alias["alias_name"],
        alias["source_name"],
        alias["valid_from_season"],
        alias["valid_to_season"],
    )
    aliases[key] = alias


def collect_current_team_aliases(aliases, teams):
    for team in teams.values():
        add_alias(
            aliases,
            team["team_id"],
            team["abbreviation"],
            team["full_name"],
            "team.csv",
            season_id_from_year(team["year_founded"]),
            None,
        )


def collect_team_details_aliases(aliases, teams):
    team_ids = set(teams)
    details_path = DATA_ROOT / "team" / "team_details.csv"
    if not details_path.exists():
        return

    for row in read_csv_rows(details_path):
        team_id = clean_int(row.get("team_id"))
        if team_id not in team_ids:
            continue

        city = clean_text(row.get("city"))
        nickname = clean_text(row.get("nickname"))
        add_alias(
            aliases,
            team_id,
            row.get("abbreviation"),
            " ".join(part for part in (city, nickname) if part),
            "team_details.csv",
            season_id_from_year(clean_int(row.get("yearfounded"))),
            None,
        )


def collect_game_aliases(aliases, teams):
    team_ids = set(teams)
    game_path = DATA_ROOT / "game" / "game.csv"
    if not game_path.exists():
        return []

    ranges = {}
    skipped = {}
    for row in read_csv_rows(game_path):
        raw_season_id = clean_text(row.get("season_id"))
        if not raw_season_id or len(raw_season_id) != 5:
            continue
        season_id = season_id_from_year(int(raw_season_id[1:]))

        for side in ("home", "away"):
            team_id = clean_int(row.get(f"team_id_{side}"))
            alias_code = clean_text(row.get(f"team_abbreviation_{side}"))
            alias_name = clean_text(row.get(f"team_name_{side}"))
            if team_id not in team_ids:
                skipped[(team_id, alias_code, alias_name)] = skipped.get((team_id, alias_code, alias_name), 0) + 1
                continue

            key = (team_id, alias_code, alias_name)
            current = ranges.get(key)
            if current is None:
                ranges[key] = [season_id, season_id]
            else:
                current[0] = min(current[0], season_id)
                current[1] = max(current[1], season_id)

    for (team_id, alias_code, alias_name), (valid_from, valid_to) in ranges.items():
        add_alias(aliases, team_id, alias_code, alias_name, "game.csv", valid_from, valid_to)

    return sorted(
        (
            {
                "team_id": team_id,
                "alias_code": alias_code,
                "alias_name": alias_name,
                "count": count,
            }
            for (team_id, alias_code, alias_name), count in skipped.items()
        ),
        key=lambda row: (row["team_id"] or 0, row["alias_code"] or ""),
    )


def player_file_season_id(csv_path):
    match = SEASON_LABEL_RE.search(csv_path.name)
    if not match:
        return None
    return season_id_from_year(int(match.group("start")))


def collect_player_stat_aliases(aliases, teams):
    code_to_team = {team["abbreviation"]: team for team in teams.values()}

    for alias in list(aliases.values()):
        code_to_team.setdefault(alias["alias_code"], teams.get(alias["team_id"]))

    for source_code, target_code in BBREF_CODE_OVERRIDES.items():
        if target_code in code_to_team:
            code_to_team[source_code] = code_to_team[target_code]

    player_root = DATA_ROOT / "player"
    if not player_root.exists():
        return []

    ranges = {}
    skipped = {}
    for csv_path in player_root.glob("*.csv"):
        file_season_id = player_file_season_id(csv_path)
        for row in read_csv_rows(csv_path):
            code = clean_text(row.get("Tm"))
            if not code or code in SKIP_PLAYER_CODES:
                continue

            team = code_to_team.get(code)
            if team is None:
                skipped[code] = skipped.get(code, 0) + 1
                continue

            label = clean_text(row.get("Year"))
            row_season_id = None
            if label:
                match = SEASON_LABEL_RE.search(label)
                if match:
                    row_season_id = season_id_from_year(int(match.group("start")))
            season_id = row_season_id or file_season_id

            key = (team["team_id"], code, team["full_name"])
            current = ranges.get(key)
            if current is None:
                ranges[key] = [season_id, season_id]
            else:
                known_values = [value for value in (*current, season_id) if value is not None]
                current[0] = min(known_values) if known_values else None
                current[1] = max(known_values) if known_values else None

    for (team_id, alias_code, alias_name), (valid_from, valid_to) in ranges.items():
        add_alias(aliases, team_id, alias_code, alias_name, "player_stats.csv", valid_from, valid_to)

    return [{"alias_code": code, "count": count} for code, count in sorted(skipped.items())]


def build_team_aliases():
    teams = load_team_rows()
    aliases = {}
    collect_current_team_aliases(aliases, teams)
    collect_team_details_aliases(aliases, teams)
    skipped_game_aliases = collect_game_aliases(aliases, teams)
    skipped_player_codes = collect_player_stat_aliases(aliases, teams)
    return sorted(aliases.values(), key=lambda row: (row["team_id"], row["source_name"], row["alias_code"])), skipped_game_aliases, skipped_player_codes


def alias_exists(connection, alias):
    statement = text(
        """
        SELECT 1
        FROM team_aliases
        WHERE team_id = :team_id
          AND alias_code = :alias_code
          AND source_name = :source_name
          AND alias_name <=> :alias_name
          AND valid_from_season <=> :valid_from_season
          AND valid_to_season <=> :valid_to_season
        LIMIT 1
        """
    )
    return connection.execute(statement, alias).first() is not None


def insert_team_aliases(engine, aliases):
    statement = text(
        """
        INSERT INTO team_aliases (
            team_id,
            alias_code,
            alias_name,
            source_name,
            valid_from_season,
            valid_to_season
        )
        VALUES (
            :team_id,
            :alias_code,
            :alias_name,
            :source_name,
            :valid_from_season,
            :valid_to_season
        )
        """
    )
    inserted = 0
    skipped_existing = 0
    with engine.begin() as connection:
        for alias in aliases:
            if alias_exists(connection, alias):
                skipped_existing += 1
                continue
            connection.execute(statement, alias)
            inserted += 1
    return inserted, skipped_existing


def validate_foreign_keys(aliases, db_team_ids):
    missing = sorted({alias["team_id"] for alias in aliases if alias["team_id"] not in db_team_ids})
    if missing:
        raise ValueError(f"teams table is missing team_id values required by team_aliases: {missing}")


def print_preview(aliases, skipped_game_aliases, skipped_player_codes):
    print(f"Prepared {len(aliases)} team aliases.")
    for alias in aliases[:12]:
        print(
            alias["team_id"],
            alias["alias_code"],
            alias["alias_name"],
            alias["source_name"],
            alias["valid_from_season"],
            alias["valid_to_season"],
        )
    if len(aliases) > 12:
        print("...")
        for alias in aliases[-8:]:
            print(
                alias["team_id"],
                alias["alias_code"],
                alias["alias_name"],
                alias["source_name"],
                alias["valid_from_season"],
                alias["valid_to_season"],
            )

    print(f"Skipped {len(skipped_game_aliases)} game aliases because team_id is not in teams.")
    print(f"Skipped {len(skipped_player_codes)} player-stat codes because no team mapping was found.")


def main():
    parser = argparse.ArgumentParser(description="Clean and load NBA team alias mapping data.")
    parser.add_argument("--dry-run", action="store_true", help="Only print cleaned team alias rows.")
    args = parser.parse_args()

    aliases, skipped_game_aliases, skipped_player_codes = build_team_aliases()
    print_preview(aliases, skipped_game_aliases, skipped_player_codes)

    if args.dry_run:
        return

    engine = make_engine()
    validate_foreign_keys(aliases, load_db_team_ids(engine))
    inserted, skipped_existing = insert_team_aliases(engine, aliases)
    print(f"Inserted team aliases: {inserted}. Skipped existing rows: {skipped_existing}.")


if __name__ == "__main__":
    main()
