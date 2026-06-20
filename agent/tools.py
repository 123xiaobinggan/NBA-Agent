from __future__ import annotations

try:
    from langchain_core.tools import tool
except ImportError:  # pragma: no cover
    from langchain.tools import tool

try:
    from .chart_service import generate_chart_file
    from .database import get_database
    from .result_formatter import format_query_result, to_json
    from .schema_service import get_schema_text
    from .sql_guard import validate_readonly_sql, ensure_readonly_sql
except ImportError:
    from chart_service import generate_chart_file
    from database import get_database
    from result_formatter import format_query_result, to_json
    from schema_service import get_schema_text
    from sql_guard import validate_readonly_sql, ensure_readonly_sql


@tool
def get_nba_schema() -> str:
    """返回NBA数据库允许访问的表、视图和字段说明。"""
    return get_schema_text()


@tool
def validate_sql(sql: str) -> str:
    """检查SQL是否为安全的只读查询, 并确认只访问白名单表或视图。"""
    result = validate_readonly_sql(sql)
    return to_json({"ok": result.ok, "error": result.error})


@tool
def execute_sql(sql: str, limit: int = 200) -> str:
    """执行安全只读SQL并以JSON返回结果。limit用于限制最多返回的行数。"""
    try:
        safe_sql = ensure_readonly_sql(sql)
        if limit <= 0:
            limit = 200
        limit = min(limit, 500)
        result = get_database().execute_read(safe_sql, limit=limit)
        return to_json({"ok": True, "result": format_query_result(result)})
    except Exception as exc:
        return to_json(
            {
                "ok": False,
                "error": str(exc),
                "hint": "请改写为只访问 get_nba_schema 白名单中表/视图的单条只读SQL。",
            }
        )


@tool
def generate_chart(
    chart_type: str,
    data_json: str,
    x_field: str,
    y_fields: str,
    title: str = "NBA Chart",
    filename_prefix: str = "nba_chart",
) -> str:
    """根据查询结果生成本地SVG图表。chart_type支持bar、line、pie; y_fields用逗号分隔。"""
    try:
        result = generate_chart_file(
            chart_type=chart_type,
            data_json=data_json,
            x_field=x_field,
            y_fields=y_fields,
            title=title,
            filename_prefix=filename_prefix,
        )
        return to_json({"ok": True, "result": result})
    except Exception as exc:
        return to_json({"ok": False, "error": str(exc)})


NBA_QUERY_TOOLS = [get_nba_schema, validate_sql, execute_sql]
NBA_TOOLS = [*NBA_QUERY_TOOLS, generate_chart]
