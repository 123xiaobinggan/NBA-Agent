import argparse
import os
import re
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

SOURCE_FILES = [
    {
        "path": PLAYER_ROOT / "NBA_Player_Stats.csv",
        "season_type": "REGULAR",
        "season_label": None,
    },
    {
        "path": PLAYER_ROOT / "2022-2023 NBA Player Stats - Regular.csv",
        "season_type": "REGULAR",
        "season_label": "2022-2023",
    },
    {
        "path": PLAYER_ROOT / "2021-2022 NBA Player Stats - Playoffs.csv",
        "season_type": "PLAYOFFS",
        "season_label": "2021-2022",
    },
    {
        "path": PLAYER_ROOT / "2022-2023 NBA Player Stats - Playoffs.csv",
        "season_type": "PLAYOFFS",
        "season_label": "2022-2023",
    },
]

SEASON_LABEL_RE = re.compile(r"^(?P<start>\d{4})-(?P<end>\d{4})$")


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


def clean_decimal(value, scale="0.001"):
    value = clean_text(value)
    if value is None:
        return None
    try:
        return Decimal(value).quantize(Decimal(scale))
    except InvalidOperation:
        return None


def clean_pct(value):
    return clean_decimal(value, "0.0001")


def clean_int(value):
    value = clean_text(value)
    if value is None:
        return None
    try:
        return int(Decimal(value))
    except InvalidOperation:
        return None


def season_id_from_label(label):
    match = SEASON_LABEL_RE.match(clean_text(label) or "")
    if not match:
        return None
    start_year = int(match.group("start"))
    end_year = int(match.group("end"))
    if end_year != start_year + 1:
        raise ValueError(f"Invalid season label: {label}")
    return int(f"2{start_year}")


def load_season_ids(engine):
    with engine.connect() as connection:
        return {int(value) for value in connection.execute(text("SELECT season_id FROM seasons")).scalars()}


def load_player_alias_map(engine, source_datasets):
    statement = text(
        """
        SELECT source_dataset, source_name, player_id
        FROM player_name_aliases
        WHERE source_dataset IN :source_datasets
        """
    ).bindparams(source_datasets=tuple(source_datasets))
    mapping = {}
    ambiguous = set()
    with engine.connect() as connection:
        rows = connection.execute(statement).mappings().all()

    for row in rows:
        key = (row["source_dataset"], row["source_name"])
        player_id = int(row["player_id"])
        existing = mapping.get(key)
        if existing is not None and existing != player_id:
            ambiguous.add(key)
        mapping[key] = player_id

    for key in ambiguous:
        mapping.pop(key, None)
    return mapping, ambiguous


def load_team_aliases(engine):
    statement = text(
        """
        SELECT team_id, alias_code, valid_from_season, valid_to_season
        FROM team_aliases
        """
    )
    aliases = {}
    with engine.connect() as connection:
        rows = connection.execute(statement).mappings().all()

    for row in rows:
        alias_code = clean_text(row["alias_code"])
        if not alias_code:
            continue
        aliases.setdefault(alias_code, []).append(
            {
                "team_id": int(row["team_id"]),
                "valid_from_season": int(row["valid_from_season"]) if row["valid_from_season"] is not None else None,
                "valid_to_season": int(row["valid_to_season"]) if row["valid_to_season"] is not None else None,
            }
        )
    return aliases


def team_id_for_code(team_aliases, source_team_code, season_id):
    source_team_code = clean_text(source_team_code)
    if source_team_code is None or source_team_code == "TOT":
        return None

    candidates = []
    for alias in team_aliases.get(source_team_code, []):
        valid_from = alias["valid_from_season"]
        valid_to = alias["valid_to_season"]
        if valid_from is not None and season_id < valid_from:
            continue
        if valid_to is not None and season_id > valid_to:
            continue
        candidates.append(alias)

    if not candidates:
        return None

    candidates.sort(
        key=lambda alias: (
            alias["valid_from_season"] is None,
            -(alias["valid_from_season"] or 0),
            alias["valid_to_season"] is None,
            alias["valid_to_season"] or 99999,
        )
    )
    return candidates[0]["team_id"]


def build_stat_row(row, source, player_alias_map, team_aliases):
    source_file = source["path"].name
    source_name = clean_text(row.get("Player"))
    player_id = player_alias_map.get((source_file, source_name))
    if player_id is None:
        return None, "unmatched_player"

    season_label = clean_text(row.get("Year")) or source["season_label"]
    season_id = season_id_from_label(season_label)
    if season_id is None:
        return None, "missing_season"

    source_team_code = clean_text(row.get("Tm"))
    if source_team_code is None:
        return None, "missing_team_code"

    games_played = clean_int(row.get("G"))
    if games_played is None:
        return None, "missing_games_played"

    return {
        "season_id": season_id,
        "season_type": source["season_type"],
        "player_id": player_id,
        "team_id": team_id_for_code(team_aliases, source_team_code, season_id),
        "source_team_code": source_team_code,
        "is_total": 1 if source_team_code == "TOT" else 0,
        "position": clean_text(row.get("Pos")),
        "age": clean_int(row.get("Age")),
        "games_played": games_played,
        "games_started": clean_int(row.get("GS")),
        "minutes_per_game": clean_decimal(row.get("MP")),
        "field_goals_per_game": clean_decimal(row.get("FG")),
        "field_goal_attempts_pg": clean_decimal(row.get("FGA")),
        "field_goal_pct": clean_pct(row.get("FG%")),
        "three_points_per_game": clean_decimal(row.get("3P")),
        "three_point_attempts_pg": clean_decimal(row.get("3PA")),
        "three_point_pct": clean_pct(row.get("3P%")),
        "two_points_per_game": clean_decimal(row.get("2P")),
        "two_point_attempts_pg": clean_decimal(row.get("2PA")),
        "two_point_pct": clean_pct(row.get("2P%")),
        "effective_fg_pct": clean_pct(row.get("eFG%")),
        "free_throws_per_game": clean_decimal(row.get("FT")),
        "free_throw_attempts_pg": clean_decimal(row.get("FTA")),
        "free_throw_pct": clean_pct(row.get("FT%")),
        "offensive_rebounds_pg": clean_decimal(row.get("ORB")),
        "defensive_rebounds_pg": clean_decimal(row.get("DRB")),
        "rebounds_per_game": clean_decimal(row.get("TRB")),
        "assists_per_game": clean_decimal(row.get("AST")),
        "steals_per_game": clean_decimal(row.get("STL")),
        "blocks_per_game": clean_decimal(row.get("BLK")),
        "turnovers_per_game": clean_decimal(row.get("TOV")),
        "personal_fouls_pg": clean_decimal(row.get("PF")),
        "points_per_game": clean_decimal(row.get("PTS")),
        "source_rank": clean_int(row.get("Rk")),
        "source_file": source_file,
    }, None


def build_player_season_stats(engine):
    source_datasets = [source["path"].name for source in SOURCE_FILES]
    player_alias_map, ambiguous_aliases = load_player_alias_map(engine, source_datasets)
    team_aliases = load_team_aliases(engine)
    stats = {}
    skipped = {
        "unmatched_player": 0,
        "ambiguous_player_alias": len(ambiguous_aliases),
        "missing_season": 0,
        "missing_team_code": 0,
        "missing_games_played": 0,
        "duplicate_stat": 0,
        "header_rows": 0,
    }

    for source in SOURCE_FILES:
        if not source["path"].exists():
            continue
        for row in read_csv_rows(source["path"]):
            if clean_text(row.get("Rk")) == "Rk":
                skipped["header_rows"] += 1
                continue
            stat, skip_reason = build_stat_row(row, source, player_alias_map, team_aliases)
            if stat is None:
                skipped[skip_reason] = skipped.get(skip_reason, 0) + 1
                continue

            key = (stat["season_id"], stat["season_type"], stat["player_id"], stat["source_team_code"])
            if key in stats:
                skipped["duplicate_stat"] += 1
                continue
            stats[key] = stat

    return sorted(
        stats.values(),
        key=lambda row: (row["season_id"], row["season_type"], row["player_id"], row["source_team_code"]),
    ), skipped


def validate_foreign_keys(stats, season_ids):
    missing_seasons = sorted({row["season_id"] for row in stats if row["season_id"] not in season_ids})
    if missing_seasons:
        raise ValueError(f"seasons table is missing season_id values required by player_season_stats: {missing_seasons}")


def insert_player_season_stats(engine, stats, upsert=False):
    duplicate_clause = (
        """
        ON DUPLICATE KEY UPDATE
            team_id = VALUES(team_id),
            is_total = VALUES(is_total),
            position = VALUES(position),
            age = VALUES(age),
            games_played = VALUES(games_played),
            games_started = VALUES(games_started),
            minutes_per_game = VALUES(minutes_per_game),
            field_goals_per_game = VALUES(field_goals_per_game),
            field_goal_attempts_pg = VALUES(field_goal_attempts_pg),
            field_goal_pct = VALUES(field_goal_pct),
            three_points_per_game = VALUES(three_points_per_game),
            three_point_attempts_pg = VALUES(three_point_attempts_pg),
            three_point_pct = VALUES(three_point_pct),
            two_points_per_game = VALUES(two_points_per_game),
            two_point_attempts_pg = VALUES(two_point_attempts_pg),
            two_point_pct = VALUES(two_point_pct),
            effective_fg_pct = VALUES(effective_fg_pct),
            free_throws_per_game = VALUES(free_throws_per_game),
            free_throw_attempts_pg = VALUES(free_throw_attempts_pg),
            free_throw_pct = VALUES(free_throw_pct),
            offensive_rebounds_pg = VALUES(offensive_rebounds_pg),
            defensive_rebounds_pg = VALUES(defensive_rebounds_pg),
            rebounds_per_game = VALUES(rebounds_per_game),
            assists_per_game = VALUES(assists_per_game),
            steals_per_game = VALUES(steals_per_game),
            blocks_per_game = VALUES(blocks_per_game),
            turnovers_per_game = VALUES(turnovers_per_game),
            personal_fouls_pg = VALUES(personal_fouls_pg),
            points_per_game = VALUES(points_per_game),
            source_rank = VALUES(source_rank),
            source_file = VALUES(source_file)
        """
        if upsert
        else ""
    )
    insert_keyword = "INSERT" if upsert else "INSERT IGNORE"
    statement = text(
        f"""
        {insert_keyword} INTO player_season_stats (
            season_id,
            season_type,
            player_id,
            team_id,
            source_team_code,
            is_total,
            position,
            age,
            games_played,
            games_started,
            minutes_per_game,
            field_goals_per_game,
            field_goal_attempts_pg,
            field_goal_pct,
            three_points_per_game,
            three_point_attempts_pg,
            three_point_pct,
            two_points_per_game,
            two_point_attempts_pg,
            two_point_pct,
            effective_fg_pct,
            free_throws_per_game,
            free_throw_attempts_pg,
            free_throw_pct,
            offensive_rebounds_pg,
            defensive_rebounds_pg,
            rebounds_per_game,
            assists_per_game,
            steals_per_game,
            blocks_per_game,
            turnovers_per_game,
            personal_fouls_pg,
            points_per_game,
            source_rank,
            source_file
        )
        VALUES (
            :season_id,
            :season_type,
            :player_id,
            :team_id,
            :source_team_code,
            :is_total,
            :position,
            :age,
            :games_played,
            :games_started,
            :minutes_per_game,
            :field_goals_per_game,
            :field_goal_attempts_pg,
            :field_goal_pct,
            :three_points_per_game,
            :three_point_attempts_pg,
            :three_point_pct,
            :two_points_per_game,
            :two_point_attempts_pg,
            :two_point_pct,
            :effective_fg_pct,
            :free_throws_per_game,
            :free_throw_attempts_pg,
            :free_throw_pct,
            :offensive_rebounds_pg,
            :defensive_rebounds_pg,
            :rebounds_per_game,
            :assists_per_game,
            :steals_per_game,
            :blocks_per_game,
            :turnovers_per_game,
            :personal_fouls_pg,
            :points_per_game,
            :source_rank,
            :source_file
        )
        {duplicate_clause}
        """
    )
    with engine.begin() as connection:
        result = connection.execute(statement, stats)
    return result.rowcount


def print_preview(stats, skipped):
    print(f"Prepared {len(stats)} player season stats.")
    print(f"Skipped: {skipped}")
    for row in stats[:10]:
        print(
            row["season_id"],
            row["season_type"],
            row["player_id"],
            row["source_team_code"],
            row["team_id"],
            row["is_total"],
            row["games_played"],
            row["points_per_game"],
            row["source_file"],
        )
    if len(stats) > 10:
        print("...")
        for row in stats[-8:]:
            print(
                row["season_id"],
                row["season_type"],
                row["player_id"],
                row["source_team_code"],
                row["team_id"],
                row["is_total"],
                row["games_played"],
                row["points_per_game"],
                row["source_file"],
            )


def main():
    parser = argparse.ArgumentParser(description="Clean and load NBA player season stats.")
    parser.add_argument("--dry-run", action="store_true", help="Only print cleaned player season stat rows.")
    parser.add_argument(
        "--upsert",
        action="store_true",
        help="Update existing player season stats when duplicates are found. Requires UPDATE permission.",
    )
    args = parser.parse_args()

    engine = make_engine()
    stats, skipped = build_player_season_stats(engine)
    print_preview(stats, skipped)

    if args.dry_run:
        return

    validate_foreign_keys(stats, load_season_ids(engine))
    affected_rows = insert_player_season_stats(engine, stats, upsert=args.upsert)
    print(f"Inserted player season stats. MySQL affected rows: {affected_rows}")


if __name__ == "__main__":
    main()
