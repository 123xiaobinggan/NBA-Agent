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
LINE_SCORE_CSV = PROJECT_ROOT / "data" / "game" / "line_score.csv"


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


def load_games(engine):
    statement = text(
        """
        SELECT
            game_id,
            home_team_id,
            away_team_id,
            home_points,
            away_points,
            overtime_count
        FROM games
        """
    )
    with engine.connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return {int(row["game_id"]): dict(row) for row in rows}


def load_team_ids(engine):
    with engine.connect() as connection:
        return {int(team_id) for team_id in connection.execute(text("SELECT team_id FROM teams")).scalars()}


def add_period(periods, game_id, team_id, period_number, period_type, points):
    if points is None:
        return
    periods[(game_id, team_id, period_number)] = {
        "game_id": game_id,
        "team_id": team_id,
        "period_number": period_number,
        "period_type": period_type,
        "points": points,
    }


def build_team_periods(row, side, game, skipped):
    game_id = clean_int(row.get("game_id"))
    team_id = clean_int(row.get(f"team_id_{side}"))
    final_points = clean_int(row.get(f"pts_{side}"))

    if team_id == int(game["home_team_id"]):
        expected_points = int(game["home_points"])
    elif team_id == int(game["away_team_id"]):
        expected_points = int(game["away_points"])
    else:
        skipped["team_mismatch"] += 1
        return []

    if final_points != expected_points:
        skipped["final_score_mismatch"] += 1
        return []

    periods = []
    total_points = 0
    for quarter in range(1, 5):
        points = clean_int(row.get(f"pts_qtr{quarter}_{side}"))
        if points is None:
            skipped["missing_quarter_score"] += 1
            return []
        total_points += points
        periods.append(
            {
                "game_id": game_id,
                "team_id": team_id,
                "period_number": quarter,
                "period_type": "QUARTER",
                "points": points,
            }
        )

    overtime_count = int(game["overtime_count"] or 0)
    for overtime in range(1, overtime_count + 1):
        points = clean_int(row.get(f"pts_ot{overtime}_{side}"))
        if points is None:
            skipped["missing_overtime_score"] += 1
            return []
        total_points += points
        periods.append(
            {
                "game_id": game_id,
                "team_id": team_id,
                "period_number": 4 + overtime,
                "period_type": "OVERTIME",
                "points": points,
            }
        )

    if total_points != final_points:
        skipped["period_sum_mismatch"] += 1
        return []

    return periods


def build_period_scores(games):
    period_scores = {}
    skipped = {
        "game_not_loaded": 0,
        "duplicate_game_id": 0,
        "team_mismatch": 0,
        "final_score_mismatch": 0,
        "missing_quarter_score": 0,
        "missing_overtime_score": 0,
        "period_sum_mismatch": 0,
        "duplicate_period": 0,
        "loaded_game_missing_line_score": 0,
    }
    seen_games = set()

    for row in read_csv_rows(LINE_SCORE_CSV):
        game_id = clean_int(row.get("game_id"))
        game = games.get(game_id)
        if game is None:
            skipped["game_not_loaded"] += 1
            continue
        if game_id in seen_games:
            skipped["duplicate_game_id"] += 1
            continue
        seen_games.add(game_id)

        for side in ("home", "away"):
            for period in build_team_periods(row, side, game, skipped):
                key = (period["game_id"], period["team_id"], period["period_number"])
                if key in period_scores:
                    skipped["duplicate_period"] += 1
                    continue
                period_scores[key] = period

    skipped["loaded_game_missing_line_score"] = len(set(games) - seen_games)
    return sorted(period_scores.values(), key=lambda row: (row["game_id"], row["team_id"], row["period_number"])), skipped


def validate_foreign_keys(period_scores, games, team_ids):
    missing_games = sorted({row["game_id"] for row in period_scores if row["game_id"] not in games})
    missing_teams = sorted({row["team_id"] for row in period_scores if row["team_id"] not in team_ids})
    if missing_games:
        raise ValueError(f"games table is missing game_id values required by period_scores: {missing_games[:20]}")
    if missing_teams:
        raise ValueError(f"teams table is missing team_id values required by period_scores: {missing_teams}")


def insert_period_scores(engine, period_scores, upsert=False):
    duplicate_clause = (
        """
        ON DUPLICATE KEY UPDATE
            period_type = VALUES(period_type),
            points = VALUES(points)
        """
        if upsert
        else ""
    )
    insert_keyword = "INSERT" if upsert else "INSERT IGNORE"
    statement = text(
        f"""
        {insert_keyword} INTO period_scores (
            game_id,
            team_id,
            period_number,
            period_type,
            points
        )
        VALUES (
            :game_id,
            :team_id,
            :period_number,
            :period_type,
            :points
        )
        {duplicate_clause}
        """
    )
    with engine.begin() as connection:
        result = connection.execute(statement, period_scores)
    return result.rowcount


def print_preview(period_scores, skipped):
    print(f"Prepared {len(period_scores)} period scores.")
    print(f"Skipped: {skipped}")
    for row in period_scores[:12]:
        print(row["game_id"], row["team_id"], row["period_number"], row["period_type"], row["points"])
    if len(period_scores) > 12:
        print("...")
        for row in period_scores[-8:]:
            print(row["game_id"], row["team_id"], row["period_number"], row["period_type"], row["points"])


def main():
    parser = argparse.ArgumentParser(description="Clean and load NBA period scores.")
    parser.add_argument("--dry-run", action="store_true", help="Only print cleaned period score rows.")
    parser.add_argument(
        "--upsert",
        action="store_true",
        help="Update existing period scores when duplicates are found. Requires UPDATE permission.",
    )
    args = parser.parse_args()

    engine = make_engine()
    games = load_games(engine)
    period_scores, skipped = build_period_scores(games)
    print_preview(period_scores, skipped)

    if args.dry_run:
        return

    validate_foreign_keys(period_scores, games, load_team_ids(engine))
    affected_rows = insert_period_scores(engine, period_scores, upsert=args.upsert)
    print(f"Inserted period scores. MySQL affected rows: {affected_rows}")


if __name__ == "__main__":
    main()
