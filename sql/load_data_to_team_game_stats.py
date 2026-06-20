import argparse
import os
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
GAME_ROOT = PROJECT_ROOT / "data" / "game"


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


def clean_pct(value):
    number = clean_decimal(value)
    if number is None:
        return None
    return number.quantize(Decimal("0.0001"))


def load_db_game_ids(engine):
    with engine.connect() as connection:
        return {int(game_id) for game_id in connection.execute(text("SELECT game_id FROM games")).scalars()}


def load_db_team_ids(engine):
    with engine.connect() as connection:
        return {int(team_id) for team_id in connection.execute(text("SELECT team_id FROM teams")).scalars()}


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
            "home": {
                "points_in_paint": clean_int(row.get("pts_paint_home")),
                "second_chance_points": clean_int(row.get("pts_2nd_chance_home")),
                "fast_break_points": clean_int(row.get("pts_fb_home")),
                "largest_lead": clean_int(row.get("largest_lead_home")),
                "team_turnovers": clean_int(row.get("team_turnovers_home")),
                "total_turnovers": clean_int(row.get("total_turnovers_home")),
                "team_rebounds": clean_int(row.get("team_rebounds_home")),
                "points_off_turnovers": clean_int(row.get("pts_off_to_home")),
            },
            "away": {
                "points_in_paint": clean_int(row.get("pts_paint_away")),
                "second_chance_points": clean_int(row.get("pts_2nd_chance_away")),
                "fast_break_points": clean_int(row.get("pts_fb_away")),
                "largest_lead": clean_int(row.get("largest_lead_away")),
                "team_turnovers": clean_int(row.get("team_turnovers_away")),
                "total_turnovers": clean_int(row.get("total_turnovers_away")),
                "team_rebounds": clean_int(row.get("team_rebounds_away")),
                "points_off_turnovers": clean_int(row.get("pts_off_to_away")),
            },
        }
    return stats


def build_team_stat(row, side, opponent_side, other_stats):
    points = clean_int(row.get(f"pts_{side}"))
    opponent_points = clean_int(row.get(f"pts_{opponent_side}"))
    team_id = clean_int(row.get(f"team_id_{side}"))
    opponent_team_id = clean_int(row.get(f"team_id_{opponent_side}"))
    game_id = clean_int(row.get("game_id"))

    if None in (game_id, team_id, opponent_team_id, points, opponent_points):
        return None

    stat = {
        "game_id": game_id,
        "team_id": team_id,
        "opponent_team_id": opponent_team_id,
        "is_home": 1 if side == "home" else 0,
        "is_win": 1 if points > opponent_points else 0,
        "points": points,
        "field_goals_made": clean_int(row.get(f"fgm_{side}")),
        "field_goals_attempted": clean_int(row.get(f"fga_{side}")),
        "field_goal_pct": clean_pct(row.get(f"fg_pct_{side}")),
        "three_points_made": clean_int(row.get(f"fg3m_{side}")),
        "three_points_attempted": clean_int(row.get(f"fg3a_{side}")),
        "three_point_pct": clean_pct(row.get(f"fg3_pct_{side}")),
        "free_throws_made": clean_int(row.get(f"ftm_{side}")),
        "free_throws_attempted": clean_int(row.get(f"fta_{side}")),
        "free_throw_pct": clean_pct(row.get(f"ft_pct_{side}")),
        "offensive_rebounds": clean_int(row.get(f"oreb_{side}")),
        "defensive_rebounds": clean_int(row.get(f"dreb_{side}")),
        "rebounds": clean_int(row.get(f"reb_{side}")),
        "assists": clean_int(row.get(f"ast_{side}")),
        "steals": clean_int(row.get(f"stl_{side}")),
        "blocks": clean_int(row.get(f"blk_{side}")),
        "turnovers": clean_int(row.get(f"tov_{side}")),
        "personal_fouls": clean_int(row.get(f"pf_{side}")),
        "plus_minus": clean_int(row.get(f"plus_minus_{side}")),
        "points_in_paint": None,
        "second_chance_points": None,
        "fast_break_points": None,
        "largest_lead": None,
        "team_turnovers": None,
        "total_turnovers": None,
        "team_rebounds": None,
        "points_off_turnovers": None,
    }
    stat.update(other_stats.get(side, {}))
    return stat


def build_team_game_stats(game_ids):
    other_stats = load_other_stats()
    stats = {}
    skipped = {
        "game_not_loaded": 0,
        "missing_required": 0,
        "duplicate_team_game": 0,
    }

    for row in read_csv_rows(GAME_ROOT / "game.csv"):
        game_id = clean_int(row.get("game_id"))
        if game_id not in game_ids:
            skipped["game_not_loaded"] += 1
            continue

        game_other_stats = other_stats.get(game_id, {})
        for side, opponent_side in (("home", "away"), ("away", "home")):
            stat = build_team_stat(row, side, opponent_side, game_other_stats)
            if stat is None:
                skipped["missing_required"] += 1
                continue
            key = (stat["game_id"], stat["team_id"])
            if key in stats:
                skipped["duplicate_team_game"] += 1
                continue
            stats[key] = stat

    return sorted(stats.values(), key=lambda row: (row["game_id"], row["is_home"])), skipped


def validate_foreign_keys(stats, game_ids, team_ids):
    missing_games = sorted({row["game_id"] for row in stats if row["game_id"] not in game_ids})
    missing_teams = sorted(
        {
            team_id
            for row in stats
            for team_id in (row["team_id"], row["opponent_team_id"])
            if team_id not in team_ids
        }
    )
    if missing_games:
        raise ValueError(f"games table is missing game_id values required by team_game_stats: {missing_games[:20]}")
    if missing_teams:
        raise ValueError(f"teams table is missing team_id values required by team_game_stats: {missing_teams}")


def insert_team_game_stats(engine, stats, upsert=False):
    duplicate_clause = (
        """
        ON DUPLICATE KEY UPDATE
            opponent_team_id = VALUES(opponent_team_id),
            is_home = VALUES(is_home),
            is_win = VALUES(is_win),
            points = VALUES(points),
            field_goals_made = VALUES(field_goals_made),
            field_goals_attempted = VALUES(field_goals_attempted),
            field_goal_pct = VALUES(field_goal_pct),
            three_points_made = VALUES(three_points_made),
            three_points_attempted = VALUES(three_points_attempted),
            three_point_pct = VALUES(three_point_pct),
            free_throws_made = VALUES(free_throws_made),
            free_throws_attempted = VALUES(free_throws_attempted),
            free_throw_pct = VALUES(free_throw_pct),
            offensive_rebounds = VALUES(offensive_rebounds),
            defensive_rebounds = VALUES(defensive_rebounds),
            rebounds = VALUES(rebounds),
            assists = VALUES(assists),
            steals = VALUES(steals),
            blocks = VALUES(blocks),
            turnovers = VALUES(turnovers),
            personal_fouls = VALUES(personal_fouls),
            plus_minus = VALUES(plus_minus),
            points_in_paint = VALUES(points_in_paint),
            second_chance_points = VALUES(second_chance_points),
            fast_break_points = VALUES(fast_break_points),
            largest_lead = VALUES(largest_lead),
            team_turnovers = VALUES(team_turnovers),
            total_turnovers = VALUES(total_turnovers),
            team_rebounds = VALUES(team_rebounds),
            points_off_turnovers = VALUES(points_off_turnovers)
        """
        if upsert
        else ""
    )
    insert_keyword = "INSERT" if upsert else "INSERT IGNORE"
    statement = text(
        f"""
        {insert_keyword} INTO team_game_stats (
            game_id,
            team_id,
            opponent_team_id,
            is_home,
            is_win,
            points,
            field_goals_made,
            field_goals_attempted,
            field_goal_pct,
            three_points_made,
            three_points_attempted,
            three_point_pct,
            free_throws_made,
            free_throws_attempted,
            free_throw_pct,
            offensive_rebounds,
            defensive_rebounds,
            rebounds,
            assists,
            steals,
            blocks,
            turnovers,
            personal_fouls,
            plus_minus,
            points_in_paint,
            second_chance_points,
            fast_break_points,
            largest_lead,
            team_turnovers,
            total_turnovers,
            team_rebounds,
            points_off_turnovers
        )
        VALUES (
            :game_id,
            :team_id,
            :opponent_team_id,
            :is_home,
            :is_win,
            :points,
            :field_goals_made,
            :field_goals_attempted,
            :field_goal_pct,
            :three_points_made,
            :three_points_attempted,
            :three_point_pct,
            :free_throws_made,
            :free_throws_attempted,
            :free_throw_pct,
            :offensive_rebounds,
            :defensive_rebounds,
            :rebounds,
            :assists,
            :steals,
            :blocks,
            :turnovers,
            :personal_fouls,
            :plus_minus,
            :points_in_paint,
            :second_chance_points,
            :fast_break_points,
            :largest_lead,
            :team_turnovers,
            :total_turnovers,
            :team_rebounds,
            :points_off_turnovers
        )
        {duplicate_clause}
        """
    )
    with engine.begin() as connection:
        result = connection.execute(statement, stats)
    return result.rowcount


def print_preview(stats, skipped):
    print(f"Prepared {len(stats)} team game stats.")
    print(f"Skipped: {skipped}")
    for row in stats[:8]:
        print(
            row["game_id"],
            row["team_id"],
            row["opponent_team_id"],
            row["is_home"],
            row["is_win"],
            row["points"],
            row["field_goals_made"],
            row["field_goals_attempted"],
            row["field_goal_pct"],
            row["plus_minus"],
            row["points_in_paint"],
            row["largest_lead"],
        )
    if len(stats) > 8:
        print("...")
        for row in stats[-5:]:
            print(
                row["game_id"],
                row["team_id"],
                row["opponent_team_id"],
                row["is_home"],
                row["is_win"],
                row["points"],
                row["field_goals_made"],
                row["field_goals_attempted"],
                row["field_goal_pct"],
                row["plus_minus"],
                row["points_in_paint"],
                row["largest_lead"],
            )


def main():
    parser = argparse.ArgumentParser(description="Clean and load NBA team game stats.")
    parser.add_argument("--dry-run", action="store_true", help="Only print cleaned team game stat rows.")
    parser.add_argument(
        "--upsert",
        action="store_true",
        help="Update existing team game stats when duplicates are found. Requires UPDATE permission.",
    )
    args = parser.parse_args()

    engine = make_engine()
    game_ids = load_db_game_ids(engine)
    stats, skipped = build_team_game_stats(game_ids)
    print_preview(stats, skipped)

    if args.dry_run:
        return

    validate_foreign_keys(stats, game_ids, load_db_team_ids(engine))
    affected_rows = insert_team_game_stats(engine, stats, upsert=args.upsert)
    print(f"Inserted team game stats. MySQL affected rows: {affected_rows}")


if __name__ == "__main__":
    main()
