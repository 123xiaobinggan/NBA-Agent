from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain_deepseek import ChatDeepSeek

try:
    from .prompts import NBA_ANALYST_SYSTEM_PROMPT
    from .tools import NBA_QUERY_TOOLS, NBA_TOOLS
except ImportError:
    from prompts import NBA_ANALYST_SYSTEM_PROMPT
    from tools import NBA_QUERY_TOOLS, NBA_TOOLS


PROJECT_ROOT = Path(__file__).resolve().parents[1]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def load_dotenv_file(path: Path = PROJECT_ROOT / ".env") -> None:
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def build_model(model_name: str | None = None, temperature: float = 0) -> ChatDeepSeek:
    load_dotenv_file()
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY。请在 .env 中配置。")

    model = model_name or os.getenv("NBA_AGENT_MODEL") or "deepseek-chat"
    kwargs: dict[str, Any] = {
        "model": model,
        "api_key": api_key,
        "temperature": temperature,
    }
    base_url = os.getenv("DEEPSEEK_BASE_URL")
    if base_url:
        kwargs["base_url"] = base_url

    return ChatDeepSeek(**kwargs)


def build_agent(model_name: str | None = None, enable_charts: bool = True):
    model = build_model(model_name=model_name)
    return create_agent(
        model=model,
        tools=NBA_TOOLS if enable_charts else NBA_QUERY_TOOLS,
        system_prompt=NBA_ANALYST_SYSTEM_PROMPT,
    )


def ask_agent(agent: Any, messages: list[dict[str, str]]) -> Any:
    return agent.invoke({"messages": messages})


def ask(question: str, model_name: str | None = None) -> Any:
    agent = build_agent(model_name=model_name)
    return agent.invoke({"messages": [{"role": "user", "content": question}]})


def stream_answer(question: str, model_name: str | None = None) -> None:
    agent = build_agent(model_name=model_name)
    printed_anything = False
    final_marker = "最终答案："
    waiting_for_final = True
    marker_buffer = ""

    def emit_text(text: str) -> None:
        nonlocal printed_anything, waiting_for_final, marker_buffer
        if not text:
            return
        if waiting_for_final:
            marker_buffer += text
            marker_index = marker_buffer.find(final_marker)
            if marker_index < 0:
                marker_buffer = marker_buffer[-len(final_marker) :]
                return
            waiting_for_final = False
            text = marker_buffer[marker_index + len(final_marker) :]
            marker_buffer = ""

        if text:
            print(text, end="", flush=True)
            printed_anything = True

    for chunk in agent.stream(
        {"messages": [{"role": "user", "content": question}]},
        stream_mode="messages",
    ):
        message = chunk[0] if isinstance(chunk, tuple) else chunk
        message_type = getattr(message, "type", None)
        class_name = message.__class__.__name__.lower()
        if message_type == "tool" or "tool" in class_name:
            continue
        if getattr(message, "tool_calls", None) or getattr(message, "tool_call_chunks", None):
            continue
        if getattr(message, "additional_kwargs", {}).get("tool_calls"):
            continue

        content = getattr(message, "content", None)
        if isinstance(content, str):
            emit_text(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    emit_text(part.get("text") or "")

    if printed_anything:
        print()


def extract_final_text(response: Any) -> str:
    messages = response.get("messages", []) if isinstance(response, dict) else []
    if not messages:
        return str(response)
    last = messages[-1]
    content = getattr(last, "content", None)
    return content if isinstance(content, str) else str(content)


def main() -> None:
    parser = argparse.ArgumentParser(description="NBA data analysis agent CLI.")
    parser.add_argument("question", nargs="*", help="自然语言问题。")
    parser.add_argument("--model", default=None, help="模型名称，默认 deepseek-chat。")
    parser.add_argument("--no-stream", action="store_true", help="关闭流式输出，等待完整回答后一次性打印。")
    args = parser.parse_args()

    question = " ".join(args.question).strip()
    if not question:
        question = input("请输入NBA数据问题: ").strip()

    if args.no_stream:
        response = ask(question, model_name=args.model)
        print(extract_final_text(response))
    else:
        stream_answer(question, model_name=args.model)


if __name__ == "__main__":
    main()
