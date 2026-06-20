import argparse
import os
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
GAME_ROOT = PROJECT_ROOT / "data" / "game"
MIN_START_YEAR = 1996
ALLOWED_SEASON_TYPES = {
    "Regular Season": "REGULAR",
    "Playoffs": "PLAYOFFS",
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
    return value or None


def clean_int(value):
    value = clean_text(value)
    if value is None:
        return None
    return int(float(value))


def parse_game_date(value):
    value = clean_text(value)
    if value is None:
        return None
    for fmt in ("%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported game_date format: {value}")


def start_year_from_raw_season_id(raw_season_id):
    raw_season_id = clean_text(raw_season_id)
    if raw_season_id is None or len(raw_season_id) != 5 or not raw_season_id.isdigit():
        return None
    return int(raw_season_id[1:])


def season_id_from_start_year(start_year):
    if start_year is None:
        return None
    return int(f"2{start_year}")


def derive_duration_and_overtime(raw_min):
    source_minutes = clean_int(raw_min)
    if source_minutes is None or source_minutes <= 0:
        return None, 0

    duration_minutes = source_minutes // 5 if source_minutes >= 120 else source_minutes
    overtime_count = max(0, (duration_minutes - 48) // 5)
    return duration_minutes, overtime_count


def load_other_stats():
    path = GAME_ROOT / "other_stats.csv"
    stats = {}
    if not path.exists():
        return stats

    for row in read_csv_rows(path):
        game_id = clean_int(row.get("game_id"))
        if game_id is None or game_id in stats:
            continue
        stats[game_id] = {
            "lead_changes": clean_int(row.get("lead_changes")),
            "times_tied": clean_int(row.get("times_tied")),
        }
    return stats


def load_line_score_game_ids():
    path = GAME_ROOT / "line_score.csv"
    if not path.exists():
        return set()
    return {
        game_id
        for game_id in (clean_int(row.get("game_id")) for row in read_csv_rows(path))
        if game_id is not None
    }


def build_games():
    other_stats = load_other_stats()
    line_score_game_ids = load_line_score_game_ids()
    games = {}
    skipped = {
        "season_year": 0,
        "season_type": 0,
        "missing_required": 0,
        "duplicate_game_id": 0,
    }

    for row in read_csv_rows(GAME_ROOT / "game.csv"):
        start_year = start_year_from_raw_season_id(row.get("season_id"))
        if start_year is None or start_year < MIN_START_YEAR:
            skipped["season_year"] += 1
            continue

        season_type = ALLOWED_SEASON_TYPES.get(clean_text(row.get("season_type")))
        if season_type is None:
            skipped["season_type"] += 1
            continue

        game_id = clean_int(row.get("game_id"))
        if game_id in games:
            skipped["duplicate_game_id"] += 1
            continue

        home_points = clean_int(row.get("pts_home"))
        away_points = clean_int(row.get("pts_away"))
        home_team_id = clean_int(row.get("team_id_home"))
        away_team_id = clean_int(row.get("team_id_away"))
        game_date = parse_game_date(row.get("game_date"))
        if None in (game_id, home_points, away_points, home_team_id, away_team_id, game_date):
            skipped["missing_required"] += 1
            continue

        duration_minutes, overtime_count = derive_duration_and_overtime(row.get("min"))
        winner_team_id = None
        if home_points > away_points:
            winner_team_id = home_team_id
        elif away_points > home_points:
            winner_team_id = away_team_id

        supplemental_files = ["game.csv"]
        if game_id in other_stats:
            supplemental_files.append("other_stats.csv")
        if game_id in line_score_game_ids:
            supplemental_files.append("line_score.csv")

        game_stats = other_stats.get(game_id, {})
        games[game_id] = {
            "game_id": game_id,
            "season_id": season_id_from_start_year(start_year),
            "game_date": game_date,
            "season_type": season_type,
            "home_team_id": home_team_id,
            "away_team_id": away_team_id,
            "home_points": home_points,
            "away_points": away_points,
            "winner_team_id": winner_team_id,
            "duration_minutes": duration_minutes,
            "overtime_count": overtime_count,
            "lead_changes": game_stats.get("lead_changes"),
            "times_tied": game_stats.get("times_tied"),
            "source_file": "+".join(supplemental_files),
        }

    return sorted(games.values(), key=lambda row: row["game_id"]), skipped


def load_fk_sets(engine):
    with engine.connect() as connection:
        season_ids = set(connection.execute(text("SELECT season_id FROM seasons")).scalars().all())
        team_ids = set(connection.execute(text("SELECT team_id FROM teams")).scalars().all())
    return {int(value) for value in season_ids}, {int(value) for value in team_ids}


def validate_foreign_keys(games, season_ids, team_ids):
    missing_seasons = sorted({game["season_id"] for game in games if game["season_id"] not in season_ids})
    missing_teams = sorted(
        {
            team_id
            for game in games
            for team_id in (game["home_team_id"], game["away_team_id"], game["winner_team_id"])
            if team_id is not None and team_id not in team_ids
        }
    )
    if missing_seasons:
        raise ValueError(f"seasons table is missing season_id values required by games: {missing_seasons}")
    if missing_teams:
        raise ValueError(f"teams table is missing team_id values required by games: {missing_teams}")


def insert_games(engine, games, upsert=False):
    duplicate_clause = (
        """
        ON DUPLICATE KEY UPDATE
            season_id = VALUES(season_id),
            game_date = VALUES(game_date),
            season_type = VALUES(season_type),
            home_team_id = VALUES(home_team_id),
            away_team_id = VALUES(away_team_id),
            home_points = VALUES(home_points),
            away_points = VALUES(away_points),
            winner_team_id = VALUES(winner_team_id),
            duration_minutes = VALUES(duration_minutes),
            overtime_count = VALUES(overtime_count),
            lead_changes = VALUES(lead_changes),
            times_tied = VALUES(times_tied),
            source_file = VALUES(source_file)
        """
        if upsert
        else ""
    )
    insert_keyword = "INSERT" if upsert else "INSERT IGNORE"
    statement = text(
        f"""
        {insert_keyword} INTO games (
            game_id,
            season_id,
            game_date,
            season_type,
            home_team_id,
            away_team_id,
            home_points,
            away_points,
            winner_team_id,
            duration_minutes,
            overtime_count,
            lead_changes,
            times_tied,
            source_file
        )
        VALUES (
            :game_id,
            :season_id,
            :game_date,
            :season_type,
            :home_team_id,
            :away_team_id,
            :home_points,
            :away_points,
            :winner_team_id,
            :duration_minutes,
            :overtime_count,
            :lead_changes,
            :times_tied,
            :source_file
        )
        {duplicate_clause}
        """
    )
    with engine.begin() as connection:
        result = connection.execute(statement, games)
    return result.rowcount


def print_preview(games, skipped):
    print(f"Prepared {len(games)} games.")
    print(f"Skipped: {skipped}")
    for game in games[:8]:
        print(
            game["game_id"],
            game["season_id"],
            game["game_date"],
            game["season_type"],
            game["home_team_id"],
            game["away_team_id"],
            game["home_points"],
            game["away_points"],
            game["winner_team_id"],
            game["duration_minutes"],
            game["overtime_count"],
            game["lead_changes"],
            game["times_tied"],
            game["source_file"],
        )
    if len(games) > 8:
        print("...")
        for game in games[-5:]:
            print(
                game["game_id"],
                game["season_id"],
                game["game_date"],
                game["season_type"],
                game["home_team_id"],
                game["away_team_id"],
                game["home_points"],
                game["away_points"],
                game["winner_team_id"],
                game["duration_minutes"],
                game["overtime_count"],
                game["lead_changes"],
                game["times_tied"],
                game["source_file"],
            )


def main():
    parser = argparse.ArgumentParser(description="Clean and load NBA games.")
    parser.add_argument("--dry-run", action="store_true", help="Only print cleaned game rows.")
    parser.add_argument(
        "--upsert",
        action="store_true",
        help="Update existing games when duplicates are found. Requires UPDATE permission.",
    )
    args = parser.parse_args()

    games, skipped = build_games()
    print_preview(games, skipped)

    if args.dry_run:
        return

    engine = make_engine()
    validate_foreign_keys(games, *load_fk_sets(engine))
    affected_rows = insert_games(engine, games, upsert=args.upsert)
    print(f"Inserted games. MySQL affected rows: {affected_rows}")


if __name__ == "__main__":
    main()
