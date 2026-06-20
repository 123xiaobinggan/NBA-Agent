from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RelationSchema:
    name: str
    description: str
    columns: dict[str, str]


ALLOWED_SCHEMAS: dict[str, RelationSchema] = {
    "seasons": RelationSchema(
        "seasons",
        "NBA赛季维度表。",
        {
            "season_id": "赛季主键, 格式为 2 + start_year, 例如 22022 表示 2022-2023。",
            "season_label": "赛季标签, 例如 2022-2023。",
            "start_year": "赛季开始年份。",
            "end_year": "赛季结束年份。",
            "start_date": "赛季首场比赛日期。",
            "end_date": "赛季最后一场比赛日期。",
        },
    ),
    "teams": RelationSchema(
        "teams",
        "球队基础信息表。",
        {
            "team_id": "NBA球队ID。",
            "full_name": "球队全名。",
            "abbreviation": "当前球队缩写。",
            "nickname": "球队昵称。",
            "city": "城市或地区。",
            "state": "州、省或地区。",
            "year_founded": "球队成立年份。",
        },
    ),
    "team_aliases": RelationSchema(
        "team_aliases",
        "不同数据源及历史时期的球队代码映射。",
        {
            "team_id": "映射到 teams.team_id。",
            "alias_code": "历史或数据源球队代码, 例如 SEA、BRK、PHO。",
            "alias_name": "数据源中的球队名称。",
            "source_name": "别名来源文件或来源类型。",
            "valid_from_season": "别名开始适用赛季ID。",
            "valid_to_season": "别名结束适用赛季ID。",
        },
    ),
    "players": RelationSchema(
        "players",
        "球员基础档案表。",
        {
            "player_id": "数据库内部球员主键。",
            "nba_person_id": "NBA官方 person_id。",
            "full_name": "球员全名。",
            "birthdate": "出生日期。",
            "country": "国家或地区。",
            "height_cm": "身高厘米。",
            "weight_kg": "体重公斤。",
            "primary_position": "主要位置。",
            "from_year": "NBA生涯开始年份。",
            "to_year": "NBA生涯结束年份。",
            "is_active": "是否现役。",
            "draft_status": "选秀状态。",
        },
    ),
    "player_name_aliases": RelationSchema(
        "player_name_aliases",
        "不同数据源中的球员姓名映射。",
        {
            "player_id": "映射到 players.player_id。",
            "source_name": "源数据中的球员姓名。",
            "normalized_name": "标准化姓名。",
            "source_dataset": "来源数据集。",
            "is_manual_fix": "是否人工修正。",
        },
    ),
    "games": RelationSchema(
        "games",
        "NBA比赛主表, 包含1996年后的常规赛和季后赛。",
        {
            "game_id": "比赛ID。",
            "season_id": "赛季ID。",
            "game_date": "比赛日期。",
            "season_type": "REGULAR 或 PLAYOFFS。",
            "home_team_id": "主队ID。",
            "away_team_id": "客队ID。",
            "home_points": "主队得分。",
            "away_points": "客队得分。",
            "winner_team_id": "获胜球队ID。",
            "duration_minutes": "比赛时长分钟。",
            "overtime_count": "加时次数。",
            "lead_changes": "领先变化次数。",
            "times_tied": "平分次数。",
        },
    ),
    "team_game_stats": RelationSchema(
        "team_game_stats",
        "球队单场技术统计事实表, 每场比赛每队一行。",
        {
            "game_id": "比赛ID。",
            "team_id": "球队ID。",
            "opponent_team_id": "对手球队ID。",
            "is_home": "是否主场。",
            "is_win": "是否胜利。",
            "points": "球队得分。",
            "field_goal_pct": "投篮命中率。",
            "three_point_pct": "三分命中率。",
            "free_throw_pct": "罚球命中率。",
            "rebounds": "篮板。",
            "assists": "助攻。",
            "steals": "抢断。",
            "blocks": "盖帽。",
            "turnovers": "失误。",
            "plus_minus": "正负值。",
        },
    ),
    "period_scores": RelationSchema(
        "period_scores",
        "比赛各节和真实加时得分表。",
        {
            "game_id": "比赛ID。",
            "team_id": "球队ID。",
            "period_number": "节次, 1-4为四节, 5起为加时。",
            "period_type": "QUARTER 或 OVERTIME。",
            "points": "该节得分。",
        },
    ),
    "player_season_stats": RelationSchema(
        "player_season_stats",
        "球员赛季场均技术统计。",
        {
            "season_id": "赛季ID。",
            "season_type": "REGULAR 或 PLAYOFFS。",
            "player_id": "球员ID。",
            "team_id": "球队ID, TOT汇总行为空。",
            "source_team_code": "源数据球队代码, TOT表示多队汇总。",
            "is_total": "是否多队汇总行。",
            "position": "位置。",
            "age": "该赛季年龄。",
            "games_played": "出场数。",
            "games_started": "首发数。",
            "minutes_per_game": "场均分钟。",
            "field_goals_per_game": "场均投篮命中数。",
            "field_goal_attempts_pg": "场均投篮出手数。",
            "field_goal_pct": "投篮命中率。",
            "three_points_per_game": "场均三分命中数。可用 SUM(three_points_per_game * games_played) 估算赛季/生涯三分命中总数。",
            "three_point_attempts_pg": "场均三分出手数。",
            "three_point_pct": "三分命中率。",
            "two_points_per_game": "场均两分命中数。",
            "two_point_attempts_pg": "场均两分出手数。",
            "two_point_pct": "两分命中率。",
            "effective_fg_pct": "有效命中率。",
            "free_throws_per_game": "场均罚球命中数。",
            "free_throw_attempts_pg": "场均罚球出手数。",
            "free_throw_pct": "罚球命中率。",
            "offensive_rebounds_pg": "场均前场篮板。",
            "defensive_rebounds_pg": "场均后场篮板。",
            "points_per_game": "场均得分。",
            "rebounds_per_game": "场均篮板。",
            "assists_per_game": "场均助攻。",
            "steals_per_game": "场均抢断。",
            "blocks_per_game": "场均盖帽。",
            "turnovers_per_game": "场均失误。",
            "personal_fouls_pg": "场均犯规。",
        },
    ),
    "season_awards": RelationSchema(
        "season_awards",
        "赛季奖项, 目前包含MVP。",
        {
            "season_id": "赛季ID。",
            "award_type": "奖项类型, 例如 MVP。",
            "player_id": "获奖球员ID, 可为空。",
            "source_player_name": "源文件中的获奖球员姓名。",
        },
    ),
    "draft_records": RelationSchema(
        "draft_records",
        "NBA历年选秀记录。",
        {
            "source_person_id": "源数据球员ID。",
            "player_id": "映射到 players.player_id, 可为空。",
            "player_name": "球员姓名。",
            "draft_year": "选秀年份。",
            "round_number": "轮次。",
            "overall_pick": "总顺位。",
            "draft_team_id": "选中球队原始ID, 早期球队可能不在teams中。",
        },
    ),
    "draft_combine_measurements": RelationSchema(
        "draft_combine_measurements",
        "选秀联合试训身体和运动测量数据。",
        {
            "draft_year": "选秀年份。",
            "player_id": "映射到 players.player_id, 可为空。",
            "player_name": "球员姓名。",
            "position": "位置。",
            "height_without_shoes_in": "裸足身高, 英寸。",
            "weight_lb": "体重, 磅。",
            "wingspan_in": "臂展, 英寸。",
            "standing_vertical_leap_in": "原地垂直弹跳, 英寸。",
        },
    ),
    "v_team_game_analysis": RelationSchema(
        "v_team_game_analysis",
        "球队比赛分析视图, 已连接赛季、球队、对手和单场统计, 适合球队胜负、进攻防守、主客场分析。",
        {
            "game_id": "比赛ID。",
            "season_label": "赛季标签。",
            "game_date": "比赛日期。",
            "season_type": "REGULAR 或 PLAYOFFS。",
            "team_name": "球队名称。",
            "opponent_name": "对手名称。",
            "is_home": "是否主场。",
            "is_win": "是否胜利。",
            "points": "球队得分。",
            "opponent_points": "对手得分。",
            "plus_minus": "正负值。",
        },
    ),
    "v_player_season_overall": RelationSchema(
        "v_player_season_overall",
        "球员赛季汇总视图; 交易球员优先取TOT汇总行, 未交易球员取唯一球队行。",
        {
            "season_label": "赛季标签。",
            "season_type": "REGULAR 或 PLAYOFFS。",
            "player_name": "球员姓名。",
            "games_played": "出场数。",
            "minutes_per_game": "场均分钟。",
            "points_per_game": "场均得分。",
            "rebounds_per_game": "场均篮板。",
            "assists_per_game": "场均助攻。",
        },
    ),
    "v_player_team_season_stats": RelationSchema(
        "v_player_team_season_stats",
        "球员按球队拆分的赛季统计视图, 不含TOT汇总行。",
        {
            "season_label": "赛季标签。",
            "season_type": "REGULAR 或 PLAYOFFS。",
            "player_name": "球员姓名。",
            "team_name": "球队名称。",
            "source_team_code": "源数据球队代码。",
            "games_played": "出场数。",
            "points_per_game": "场均得分。",
        },
    ),
}


def allowed_relation_names() -> set[str]:
    return set(ALLOWED_SCHEMAS)


def get_schema_text() -> str:
    chunks = []
    for schema in ALLOWED_SCHEMAS.values():
        columns = "\n".join(f"  - {name}: {desc}" for name, desc in schema.columns.items())
        chunks.append(f"{schema.name}: {schema.description}\n{columns}")
    return "\n\n".join(chunks)


def get_schema_dict() -> dict[str, dict[str, object]]:
    return {
        name: {"description": schema.description, "columns": schema.columns}
        for name, schema in ALLOWED_SCHEMAS.items()
    }
