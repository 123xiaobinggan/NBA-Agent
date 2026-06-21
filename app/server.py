from __future__ import annotations

import json
import os
import re
import time
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generator

from flask import Flask, Response, abort, jsonify, render_template, request, send_from_directory, stream_with_context

from agent.chart_service import CHART_DIR
from agent.nba_agent import build_agent, load_dotenv_file


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FINAL_MARKER = "最终答案："
FINAL_ANSWER_RE = re.compile(r"\*{0,2}\s*最终答案\s*[:：]\s*\*{0,2}\s*")
FINAL_MARKER_WAIT_CHARS = 120


@dataclass
class ChatSession:
    session_id: str
    title: str
    agent: Any | None = None
    agent_enable_charts: bool | None = None
    is_pinned: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    messages: list[dict[str, str]] = field(default_factory=list)
    display_messages: list[dict[str, Any]] = field(default_factory=list)
    charts: list[dict[str, str]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


app = Flask(
    __name__,
    template_folder=str(PROJECT_ROOT / "app" / "templates"),
    static_folder=str(PROJECT_ROOT / "app" / "static"),
)

_sessions: dict[str, ChatSession] = {}
_sessions_lock = threading.RLock()


def _sse(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def _chart_url_from_path(path_value: str) -> str | None:
    if not path_value:
        return None
    path = Path(path_value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        path.relative_to(CHART_DIR)
    except ValueError:
        return None
    return f"/charts/{path.name}"


def _chart_from_tool_payload(content: str, seen: set[str]) -> dict[str, str] | None:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or not payload.get("ok"):
        return None
    result = payload.get("result")
    if not isinstance(result, dict) or "path" not in result:
        return None
    url = _chart_url_from_path(str(result["path"]))
    if not url or url in seen:
        return None
    seen.add(url)
    return {
        "url": url,
        "path": str(Path(str(result["path"]))),
        "title": str(result.get("title") or "NBA 图表"),
    }


def _message_text(message: Any) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text" or "text" in item:
                    parts.append(str(item.get("text") or ""))
            elif hasattr(item, "text"):
                parts.append(str(getattr(item, "text") or ""))
        return "".join(parts)
    return ""


def _split_final_answer_marker(text: str) -> tuple[bool, str]:
    match = FINAL_ANSWER_RE.search(text)
    if not match:
        return False, ""
    return True, text[match.end() :]


def _extract_final_answer(text: str) -> str:
    found, answer = _split_final_answer_marker(text)
    if found:
        return answer.strip()
    return text.strip()


def _strip_empty_sql_notice(text: str) -> str:
    return re.sub(
        r"\n*\s*关键\s*SQL\s*[:：]\s*未检测到本轮\s*execute_sql\s*工具调用。?\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    ).rstrip()


def _wants_chart(question: str) -> bool:
    text = question.lower()
    chart_keywords = [
        "图表",
        "可视化",
        "柱状图",
        "折线图",
        "饼图",
        "趋势图",
        "画图",
        "绘图",
        "作图",
        "生成图",
        "画出",
        "chart",
        "plot",
        "bar chart",
        "line chart",
        "pie chart",
        "visualize",
        "visualization",
    ]
    return any(keyword in text for keyword in chart_keywords)


def _remember_execute_sql(message: Any, executed_sql: list[str]) -> None:
    def add_sql(sql: Any) -> None:
        if not isinstance(sql, str):
            return
        sql = sql.strip()
        if sql and sql not in executed_sql:
            executed_sql.append(sql)

    for call in getattr(message, "tool_calls", None) or []:
        name = call.get("name") if isinstance(call, dict) else getattr(call, "name", None)
        args = call.get("args") if isinstance(call, dict) else getattr(call, "args", None)
        if name == "execute_sql" and isinstance(args, dict):
            add_sql(args.get("sql"))

    raw_calls = getattr(message, "additional_kwargs", {}).get("tool_calls") or []
    for call in raw_calls:
        if not isinstance(call, dict):
            continue
        function = call.get("function") or {}
        if function.get("name") != "execute_sql":
            continue
        raw_args = function.get("arguments") or "{}"
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError:
            continue
        add_sql(args.get("sql"))


def _remember_execute_sql_chunk(message: Any, sql_chunks: dict[str, dict[str, str]]) -> None:
    chunks = getattr(message, "tool_call_chunks", None) or []
    for index, chunk in enumerate(chunks):
        if not isinstance(chunk, dict):
            continue
        key = str(chunk.get("id") or chunk.get("index") or index)
        current = sql_chunks.setdefault(key, {"name": "", "args": ""})
        if chunk.get("name"):
            current["name"] += str(chunk["name"])
        if chunk.get("args"):
            current["args"] += str(chunk["args"])


def _flush_execute_sql_chunks(sql_chunks: dict[str, dict[str, str]], executed_sql: list[str]) -> None:
    for chunk in sql_chunks.values():
        if chunk.get("name") != "execute_sql":
            continue
        raw_args = chunk.get("args") or "{}"
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError:
            continue
        sql = args.get("sql")
        if isinstance(sql, str):
            sql = sql.strip()
            if sql and sql not in executed_sql:
                executed_sql.append(sql)


def _get_or_create_agent(chat: ChatSession, enable_charts: bool) -> Any:
    if chat.agent is None or chat.agent_enable_charts != enable_charts:
        chat.agent = build_agent(enable_charts=enable_charts)
        chat.agent_enable_charts = enable_charts
    return chat.agent


def _new_session(title: str = "新会话") -> ChatSession:
    session_id = uuid.uuid4().hex[:12]
    chat = ChatSession(session_id=session_id, title=title)
    _sessions[session_id] = chat
    return chat


def _sorted_sessions() -> list[ChatSession]:
    return sorted(_sessions.values(), key=lambda chat: (chat.is_pinned, chat.updated_at), reverse=True)


def _session_summary(chat: ChatSession) -> dict[str, Any]:
    return {
        "session_id": chat.session_id,
        "title": chat.title,
        "message_count": len(chat.display_messages),
        "chart_count": len(chat.charts),
        "agent_ready": chat.agent is not None,
        "is_pinned": chat.is_pinned,
        "updated_at": chat.updated_at,
    }


def _clean_display_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for message in messages:
        item = dict(message)
        if item.get("role") == "assistant" and isinstance(item.get("content"), str):
            item["content"] = _strip_empty_sql_notice(item["content"])
        cleaned.append(item)
    return cleaned


def _stream_agent_answer(chat: ChatSession, question: str) -> Generator[str, None, None]:
    if not chat.lock.acquire(blocking=False):
        yield _sse("error", {"error": "当前会话正在回答上一个问题，请稍后再试。"})
        return

    answer_parts: list[str] = []
    fallback_ai_parts: list[str] = []
    current_charts: list[dict[str, str]] = []
    executed_sql: list[str] = []
    sql_chunks: dict[str, dict[str, str]] = {}
    seen_chart_urls: set[str] = set()
    waiting_for_final = True
    marker_buffer = ""
    response_error: str | None = None
    enable_charts = _wants_chart(question)

    with _sessions_lock:
        chat.display_messages.append({"role": "user", "content": question})
        chat.messages.append({"role": "user", "content": question})
        chat.updated_at = time.time()
        if chat.title == "新会话":
            chat.title = question[:24]
        run_messages = list(chat.messages)
        session_payload = _session_summary(chat)

    yield _sse("session", {"session": session_payload})
    yield _sse("status", {"message": "正在分析数据库..."})

    try:
        agent = _get_or_create_agent(chat, enable_charts=enable_charts)
        for chunk in agent.stream({"messages": run_messages}, stream_mode="messages"):
            message = chunk[0] if isinstance(chunk, tuple) else chunk
            message_type = getattr(message, "type", None)
            class_name = message.__class__.__name__.lower()
            text = _message_text(message)
            _remember_execute_sql(message, executed_sql)
            _remember_execute_sql_chunk(message, sql_chunks)

            if message_type == "tool" or "tool" in class_name:
                chart = _chart_from_tool_payload(text, seen_chart_urls)
                if chart:
                    current_charts.append(chart)
                    yield _sse("chart", {"chart": chart})
                continue

            if getattr(message, "tool_calls", None) or getattr(message, "tool_call_chunks", None):
                continue
            if getattr(message, "additional_kwargs", {}).get("tool_calls"):
                continue
            if not text:
                continue
            fallback_ai_parts.append(text)

            if waiting_for_final:
                marker_buffer += text
                found_marker, text = _split_final_answer_marker(marker_buffer)
                if not found_marker:
                    if len(marker_buffer) < FINAL_MARKER_WAIT_CHARS:
                        continue
                    waiting_for_final = False
                    text = marker_buffer
                    marker_buffer = ""
                else:
                    waiting_for_final = False
                    marker_buffer = ""

            if text:
                answer_parts.append(text)
                yield _sse("token", {"text": text})
    except Exception as exc:
        response_error = str(exc)
        yield _sse("error", {"error": response_error})
    finally:
        _flush_execute_sql_chunks(sql_chunks, executed_sql)
        answer = "".join(answer_parts).strip()
        if not answer and fallback_ai_parts:
            answer = _extract_final_answer("".join(fallback_ai_parts))
            if answer:
                yield _sse("token", {"text": answer})
        if response_error and not answer:
            answer = f"请求失败：{response_error}"
        if not answer and not response_error:
            answer = "没有收到模型的最终回答。"
        answer = _strip_empty_sql_notice(answer)
        if executed_sql:
            sql_body = "\n\n".join(f"```sql\n{sql}\n```" for sql in executed_sql)
            sql_section = f"\n\n关键 SQL：\n{sql_body}"
            answer += sql_section
            yield _sse("token", {"text": sql_section})

        with _sessions_lock:
            for chart in current_charts:
                if chart["url"] not in {item["url"] for item in chat.charts}:
                    chat.charts.append(chart)
            chat.messages.append({"role": "assistant", "content": f"{FINAL_MARKER}{answer}"})
            assistant_message = {"role": "assistant", "content": answer, "charts": current_charts}
            chat.display_messages.append(assistant_message)
            done_payload = {
                "session": _session_summary(chat),
                "message": assistant_message,
                "charts": chat.charts,
            }
        chat.lock.release()
        yield _sse("done", done_payload)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/sessions")
def list_sessions():
    with _sessions_lock:
        if not _sessions:
            _new_session()
        sessions = [_session_summary(chat) for chat in _sorted_sessions()]
    return jsonify({"sessions": sessions})


@app.post("/api/sessions")
def create_session():
    data = request.get_json(silent=True) or {}
    title = str(data.get("title") or "新会话").strip()[:40] or "新会话"
    with _sessions_lock:
        chat = _new_session(title=title)
        payload = _session_summary(chat)
    return jsonify({"session": payload})


@app.get("/api/sessions/<session_id>")
def get_session(session_id: str):
    with _sessions_lock:
        chat = _sessions.get(session_id)
        if not chat:
            abort(404)
        payload = {
            "session": _session_summary(chat),
            "messages": _clean_display_messages(chat.display_messages),
            "charts": list(chat.charts),
        }
    return jsonify(payload)


@app.patch("/api/sessions/<session_id>/pin")
def toggle_session_pin(session_id: str):
    data = request.get_json(silent=True) or {}
    with _sessions_lock:
        chat = _sessions.get(session_id)
        if not chat:
            abort(404)
        chat.is_pinned = bool(data.get("is_pinned"))
        chat.updated_at = time.time()
        sessions = [_session_summary(item) for item in _sorted_sessions()]
    return jsonify({"session": _session_summary(chat), "sessions": sessions})


@app.delete("/api/sessions/<session_id>")
def delete_session(session_id: str):
    with _sessions_lock:
        chat = _sessions.get(session_id)
        if not chat:
            abort(404)
        if chat.lock.locked():
            return jsonify({"ok": False, "error": "当前会话正在回答，暂时不能删除。"}), 409
        del _sessions[session_id]
        if not _sessions:
            _new_session()
        sessions = [_session_summary(item) for item in _sorted_sessions()]
    return jsonify({"ok": True, "sessions": sessions})


@app.post("/api/sessions/<session_id>/messages")
def send_message(session_id: str):
    data = request.get_json(silent=True) or {}
    question = str(data.get("message") or "").strip()
    if not question:
        return jsonify({"ok": False, "error": "消息不能为空。"}), 400

    with _sessions_lock:
        chat = _sessions.get(session_id)
        if not chat:
            abort(404)

    return Response(
        stream_with_context(_stream_agent_answer(chat, question)),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/charts/<path:filename>")
def chart_file(filename: str):
    return send_from_directory(CHART_DIR, filename)


def main() -> None:
    load_dotenv_file()
    debug = os.getenv("FLASK_DEBUG") == "1"
    app.run(host="127.0.0.1", port=5000, debug=debug, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
