from __future__ import annotations

import html
import json
import math
import re
import uuid
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHART_DIR = PROJECT_ROOT / "outputs" / "charts"


def _safe_filename(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip())
    return value[:60].strip("_") or "nba_chart"


def _parse_rows(data_json: str) -> list[dict[str, Any]]:
    data = json.loads(data_json)
    if isinstance(data, dict):
        if "result" in data and isinstance(data["result"], dict):
            data = data["result"]
        if "rows" in data:
            data = data["rows"]
    if not isinstance(data, list):
        raise ValueError("data_json 必须是行对象列表，或包含 rows/result.rows 的 JSON。")
    return [row for row in data if isinstance(row, dict)]


def _parse_y_fields(y_fields: str) -> list[str]:
    fields = [field.strip() for field in y_fields.split(",") if field.strip()]
    if not fields:
        raise ValueError("至少需要一个 y 字段。")
    return fields


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _svg_header(width: int, height: int, title: str) -> list[str]:
    title = html.escape(title)
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<style>",
        "text{font-family:Arial,'Microsoft YaHei',sans-serif;fill:#172033}",
        ".axis{stroke:#738091;stroke-width:1}",
        ".grid{stroke:#d9dee7;stroke-width:1;stroke-dasharray:4 4}",
        ".label{font-size:12px}.title{font-size:20px;font-weight:700}",
        "</style>",
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{width / 2}" y="34" text-anchor="middle" class="title">{title}</text>',
    ]


def _scale(values: list[float], height: int, top: int, bottom: int) -> tuple[float, float, float]:
    min_value = min(0, min(values))
    max_value = max(values)
    if max_value == min_value:
        max_value += 1
    plot_height = height - top - bottom
    return min_value, max_value, plot_height


def _y(value: float, min_value: float, max_value: float, height: int, top: int, bottom: int) -> float:
    plot_height = height - top - bottom
    return top + (max_value - value) / (max_value - min_value) * plot_height


def _draw_axes(parts: list[str], width: int, height: int, left: int, right: int, top: int, bottom: int) -> None:
    x0, y0 = left, height - bottom
    x1, y1 = width - right, top
    parts.append(f'<line x1="{x0}" y1="{y0}" x2="{width - right}" y2="{y0}" class="axis"/>')
    parts.append(f'<line x1="{x0}" y1="{y0}" x2="{x0}" y2="{y1}" class="axis"/>')
    for i in range(1, 5):
        y = y0 - i * (y0 - y1) / 4
        parts.append(f'<line x1="{x0}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}" class="grid"/>')


def _bar_chart(rows: list[dict[str, Any]], x_field: str, y_field: str, title: str) -> str:
    width, height = 960, 540
    left, right, top, bottom = 70, 35, 60, 110
    data = [(str(row.get(x_field, "")), _num(row.get(y_field))) for row in rows]
    data = [(label, value) for label, value in data if value is not None]
    if not data:
        raise ValueError("没有可绘制的数值数据。")

    values = [value for _, value in data]
    min_value, max_value, _ = _scale(values, height, top, bottom)
    plot_width = width - left - right
    step = plot_width / len(data)
    bar_width = min(48, step * 0.66)
    baseline = _y(0, min_value, max_value, height, top, bottom)

    parts = _svg_header(width, height, title)
    _draw_axes(parts, width, height, left, right, top, bottom)
    for index, (label, value) in enumerate(data):
        cx = left + step * index + step / 2
        y = _y(value, min_value, max_value, height, top, bottom)
        bar_y = min(y, baseline)
        bar_h = abs(baseline - y)
        parts.append(f'<rect x="{cx - bar_width / 2:.1f}" y="{bar_y:.1f}" width="{bar_width:.1f}" height="{bar_h:.1f}" fill="#2d6cdf" rx="3"/>')
        parts.append(f'<text x="{cx:.1f}" y="{bar_y - 6:.1f}" text-anchor="middle" class="label">{value:g}</text>')
        escaped_label = html.escape(label[:18])
        parts.append(f'<text x="{cx:.1f}" y="{height - bottom + 20}" text-anchor="end" class="label" transform="rotate(-35 {cx:.1f} {height - bottom + 20})">{escaped_label}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def _line_chart(rows: list[dict[str, Any]], x_field: str, y_field: str, title: str) -> str:
    width, height = 960, 540
    left, right, top, bottom = 70, 35, 60, 100
    data = [(str(row.get(x_field, "")), _num(row.get(y_field))) for row in rows]
    data = [(label, value) for label, value in data if value is not None]
    if len(data) < 2:
        raise ValueError("折线图至少需要两条数值数据。")

    values = [value for _, value in data]
    min_value, max_value, _ = _scale(values, height, top, bottom)
    plot_width = width - left - right
    step = plot_width / (len(data) - 1)

    points = []
    for index, (_, value) in enumerate(data):
        x = left + step * index
        y = _y(value, min_value, max_value, height, top, bottom)
        points.append((x, y, value))

    parts = _svg_header(width, height, title)
    _draw_axes(parts, width, height, left, right, top, bottom)
    path = " ".join(f"{x:.1f},{y:.1f}" for x, y, _ in points)
    parts.append(f'<polyline points="{path}" fill="none" stroke="#d94f3d" stroke-width="3"/>')
    for index, (x, y, value) in enumerate(points):
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="#d94f3d"/>')
        parts.append(f'<text x="{x:.1f}" y="{y - 8:.1f}" text-anchor="middle" class="label">{value:g}</text>')
        if index % max(1, len(points) // 12) == 0 or index == len(points) - 1:
            label = html.escape(data[index][0][:18])
            parts.append(f'<text x="{x:.1f}" y="{height - bottom + 22}" text-anchor="end" class="label" transform="rotate(-35 {x:.1f} {height - bottom + 22})">{label}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def _pie_chart(rows: list[dict[str, Any]], x_field: str, y_field: str, title: str) -> str:
    width, height = 760, 520
    cx, cy, radius = 260, 270, 170
    data = [(str(row.get(x_field, "")), _num(row.get(y_field))) for row in rows]
    data = [(label, value) for label, value in data if value is not None and value > 0]
    if not data:
        raise ValueError("饼图需要正数值数据。")

    total = sum(value for _, value in data)
    colors = ["#2d6cdf", "#d94f3d", "#2f9e44", "#f59f00", "#845ef7", "#15aabf", "#e64980", "#868e96"]
    parts = _svg_header(width, height, title)
    start_angle = -math.pi / 2
    for index, (label, value) in enumerate(data):
        angle = value / total * math.tau
        end_angle = start_angle + angle
        x1 = cx + radius * math.cos(start_angle)
        y1 = cy + radius * math.sin(start_angle)
        x2 = cx + radius * math.cos(end_angle)
        y2 = cy + radius * math.sin(end_angle)
        large_arc = 1 if angle > math.pi else 0
        color = colors[index % len(colors)]
        parts.append(
            f'<path d="M {cx},{cy} L {x1:.1f},{y1:.1f} A {radius},{radius} 0 {large_arc},1 {x2:.1f},{y2:.1f} Z" fill="{color}"/>'
        )
        legend_y = 105 + index * 24
        parts.append(f'<rect x="520" y="{legend_y - 12}" width="14" height="14" fill="{color}"/>')
        pct = value / total * 100
        parts.append(f'<text x="542" y="{legend_y}" class="label">{html.escape(label[:22])}: {pct:.1f}%</text>')
        start_angle = end_angle
    parts.append("</svg>")
    return "\n".join(parts)


def generate_chart_file(
    *,
    chart_type: str,
    data_json: str,
    x_field: str,
    y_fields: str,
    title: str = "NBA Chart",
    filename_prefix: str = "nba_chart",
) -> dict[str, Any]:
    rows = _parse_rows(data_json)
    y_field = _parse_y_fields(y_fields)[0]
    chart_type = chart_type.lower().strip()

    if chart_type == "bar":
        svg = _bar_chart(rows, x_field, y_field, title)
    elif chart_type == "line":
        svg = _line_chart(rows, x_field, y_field, title)
    elif chart_type == "pie":
        svg = _pie_chart(rows, x_field, y_field, title)
    else:
        raise ValueError("chart_type 只支持 bar、line、pie。")

    CHART_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{_safe_filename(filename_prefix)}_{uuid.uuid4().hex[:8]}.svg"
    path = CHART_DIR / filename
    path.write_text(svg, encoding="utf-8")
    return {
        "chart_type": chart_type,
        "path": str(path),
        "rows_used": len(rows),
        "x_field": x_field,
        "y_field": y_field,
        "title": title,
    }
