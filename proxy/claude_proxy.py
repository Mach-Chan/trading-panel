#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["fastapi", "uvicorn"]
# ///
"""
Claude Code → OpenAI 兼容代理

把本地 Claude Code 订阅包装成 OpenAI 的 /v1/chat/completions 接口，
让任何"只会说 OpenAI 协议"的程序（这里是 TradingAgents）直接用上你的订阅，
不需要任何 LLM API key。

核心难点是**工具调用（function calling）的双向翻译**：
  TradingAgents 用 LangChain 给模型绑定工具，并检查返回里的 tool_calls 字段来驱动它的状态图。
  Claude Code 无头模式只吐纯文本，所以本代理：
    去程：把 OpenAI 的 tools 数组渲染进系统提示词，并规定一种严格的回复格式
    回程：从回复里解析出工具调用，翻译回 OpenAI 的 tool_calls 结构

启动：./proxy/claude_proxy.py            （uv 会自动装依赖）
      PROXY_PORT=8788 ./proxy/claude_proxy.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

# ---------------------------------------------------------------- 配置

PORT = int(os.environ.get("PROXY_PORT", "8788"))
HOST = os.environ.get("PROXY_HOST", "127.0.0.1")
# 单次调用超时。分析师带工具调用时可能很慢，给足余量。
TIMEOUT = float(os.environ.get("PROXY_TIMEOUT", "900"))
# 同时最多几个 claude 进程。太多会互相抢资源且更容易触发限流。
MAX_CONCURRENCY = int(os.environ.get("PROXY_CONCURRENCY", "4"))
# 在中立目录里跑，避免 claude 加载某个项目的 CLAUDE.md 污染上下文
WORKDIR = os.environ.get("PROXY_WORKDIR", "/tmp")
LOG_PATH = os.environ.get("PROXY_LOG", os.path.join(os.path.dirname(__file__), "proxy-calls.log"))

# 把外部传来的模型名映射到 claude CLI 的别名
MODEL_ALIASES = {
    "claude-opus": "opus",
    "claude-sonnet": "sonnet",
    "claude-haiku": "haiku",
    "claude-fable": "fable",
    "opus": "opus",
    "sonnet": "sonnet",
    "haiku": "haiku",
    "fable": "fable",
    # 常见的"假装成 OpenAI 模型名"也一并接住，免得对方程序校验模型名时报错
    "gpt-4o": "sonnet",
    "gpt-4o-mini": "haiku",
    "gpt-5.5": "opus",
    "gpt-5.4-mini": "sonnet",
}

# 让 claude 变成"纯模型"：禁掉它自己的所有工具。
# 必须这样做——工具由调用方（TradingAgents）自己执行，模型只负责决定调哪个。
DISALLOWED_TOOLS = [
    "Bash", "Read", "Write", "Edit", "NotebookEdit", "Glob", "Grep",
    "WebSearch", "WebFetch", "Task", "Agent", "TodoWrite", "Monitor",
]

_sem = asyncio.Semaphore(MAX_CONCURRENCY)
app = FastAPI(title="claude-openai-proxy")


def log(line: str) -> None:
    stamp = time.strftime("%H:%M:%S")
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {line}\n")
    except OSError:
        pass
    print(f"[{stamp}] {line}", flush=True)


# ---------------------------------------------------------------- 去程：OpenAI → 提示词

TOOL_PROTOCOL = """\

# 可用工具

你可以调用下列工具来获取信息。工具由外部程序执行，不是你自己执行。

{tools_json}

# 调用工具的唯一格式

当且仅当你需要调用工具时，你的回复必须**只包含**下面这个代码块，前后不要有任何其他文字：

```tool_calls
[{{"name": "工具名", "arguments": {{"参数名": "参数值"}}}}]
```

规则：
- 可以在数组里放多个工具调用，它们会被并行执行。
- arguments 必须是合法 JSON 对象，参数名严格照抄工具定义。
- 不需要调用工具时，正常用自然语言回答，不要输出这个代码块。
- 不要解释你要调用什么，直接输出代码块。
"""


def render_tools(tools: list[dict] | None) -> str:
    if not tools:
        return ""
    specs = []
    for t in tools:
        fn = t.get("function", t)
        specs.append(
            {
                "name": fn.get("name"),
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {}),
            }
        )
    return TOOL_PROTOCOL.format(tools_json=json.dumps(specs, ensure_ascii=False, indent=2))


def flatten(messages: list[dict], tools: list[dict] | None) -> tuple[str, str]:
    """把 OpenAI 的消息数组压成 (系统提示词, 用户提示词) 两段。

    claude -p 是一次性的，没有会话状态，所以历史必须完整渲染进提示词里，
    包括助手先前发起的工具调用和工具返回的结果——否则模型会重复调用同一个工具。
    """
    system_parts: list[str] = []
    convo: list[str] = []

    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, list):  # 多模态数组，取其中的文本块
            content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
        content = content or ""

        if role == "system":
            system_parts.append(content)
        elif role == "user":
            convo.append(f"## 用户\n{content}")
        elif role == "assistant":
            calls = m.get("tool_calls")
            if calls:
                rendered = "\n".join(
                    f"- {c.get('function', {}).get('name')}({c.get('function', {}).get('arguments')})"
                    for c in calls
                )
                convo.append(f"## 你（先前发起了工具调用）\n{rendered}")
            if content:
                convo.append(f"## 你\n{content}")
        elif role == "tool":
            name = m.get("name") or m.get("tool_call_id") or "工具"
            convo.append(f"## 工具返回：{name}\n{content}")

    system = "\n\n".join(p for p in system_parts if p.strip())
    if not system:
        system = "You are a helpful assistant. Answer the user directly and completely."
    system += render_tools(tools)

    user = "\n\n".join(convo) if convo else "（无内容）"
    if any(m.get("role") == "tool" for m in messages):
        user += "\n\n---\n请基于上面的工具返回结果继续。如果信息已经足够，直接给出最终答复，不要重复调用同一个工具。"

    return system, user


# ---------------------------------------------------------------- 调用 claude

async def run_claude(system: str, user: str, model: str) -> dict[str, Any]:
    alias = MODEL_ALIASES.get(model, "sonnet")
    cmd = [
        "claude", "-p", user,
        "--output-format", "json",
        "--model", alias,
        "--system-prompt", system,
        "--disallowed-tools", *DISALLOWED_TOOLS,
    ]

    async with _sem:
        started = time.time()
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=WORKDIR,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError(f"claude 调用超过 {TIMEOUT} 秒未返回")

    elapsed = time.time() - started
    if proc.returncode != 0:
        raise RuntimeError(f"claude 退出码 {proc.returncode}：{err.decode(errors='replace')[:500]}")

    text = out.decode(errors="replace").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise RuntimeError(f"claude 返回了非 JSON 内容：{text[:500]}")

    if data.get("is_error"):
        raise RuntimeError(f"claude 报错：{str(data.get('result'))[:500]}")

    data["_elapsed"] = elapsed
    return data


# ---------------------------------------------------------------- 回程：文本 → tool_calls

FENCE = re.compile(r"```(?:tool_calls|json)?\s*(\[.*?\]|\{.*?\})\s*```", re.DOTALL)


def parse_tool_calls(text: str) -> tuple[str | None, list[dict] | None]:
    """从模型回复里抽出工具调用。抽不出来就当作普通文本回复。"""
    if not text:
        return "", None

    candidates: list[str] = [m.group(1) for m in FENCE.finditer(text)]
    # 整段就是一个裸 JSON 数组的情况也接住
    stripped = text.strip()
    if not candidates and stripped.startswith("["):
        candidates.append(stripped)

    for raw in candidates:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        items = parsed if isinstance(parsed, list) else [parsed]
        calls = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("function")
            if not name or not isinstance(name, str):
                continue
            arguments = item.get("arguments", item.get("parameters", {}))
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments, ensure_ascii=False)
            calls.append(
                {
                    "id": f"call_{uuid.uuid4().hex[:24]}",
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
            )
        if calls:
            return None, calls

    return text, None


def usage_of(data: dict) -> dict:
    u = data.get("usage", {}) or {}
    pt = (
        u.get("input_tokens", 0)
        + u.get("cache_read_input_tokens", 0)
        + u.get("cache_creation_input_tokens", 0)
    )
    ct = u.get("output_tokens", 0)
    return {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct}


# ---------------------------------------------------------------- 接口

@app.get("/health")
async def health():
    return {"status": "ok", "backend": "claude-code", "concurrency": MAX_CONCURRENCY}


@app.get("/v1/models")
async def models():
    now = int(time.time())
    return {
        "object": "list",
        "data": [
            {"id": mid, "object": "model", "created": now, "owned_by": "claude-code"}
            for mid in ("claude-opus", "claude-sonnet", "claude-haiku", "claude-fable")
        ],
    }


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    tools = body.get("tools") or (
        [{"type": "function", "function": f} for f in body.get("functions", [])] or None
    )
    model = body.get("model", "claude-sonnet")
    stream = bool(body.get("stream"))

    system, user = flatten(messages, tools)
    tool_names = [t.get("function", t).get("name") for t in (tools or [])]
    log(f"→ {model} | 消息 {len(messages)} 条 | 工具 {len(tool_names)} 个 | 提示词 {len(system)+len(user)} 字符")

    try:
        data = await run_claude(system, user, model)
    except RuntimeError as exc:
        log(f"✗ 失败：{exc}")
        return JSONResponse(
            status_code=502,
            content={"error": {"message": str(exc), "type": "proxy_error"}},
        )

    content, tool_calls = parse_tool_calls(data.get("result", ""))
    if tool_calls:
        log(f"← 工具调用 {[c['function']['name'] for c in tool_calls]} | {data['_elapsed']:.1f}s")
    else:
        log(f"← 文本 {len(content or '')} 字符 | {data['_elapsed']:.1f}s")

    created = int(time.time())
    comp_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    message: dict[str, Any] = {"role": "assistant", "content": content}
    finish = "stop"
    if tool_calls:
        message["content"] = None
        message["tool_calls"] = tool_calls
        finish = "tool_calls"

    payload = {
        "id": comp_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": usage_of(data),
    }

    if not stream:
        return payload

    async def sse():
        def chunk(delta, finish_reason=None):
            return (
                "data: "
                + json.dumps(
                    {
                        "id": comp_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [
                            {"index": 0, "delta": delta, "finish_reason": finish_reason}
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            )

        yield chunk({"role": "assistant"})
        if tool_calls:
            for i, tc in enumerate(tool_calls):
                yield chunk({"tool_calls": [{"index": i, **tc}]})
        else:
            text = content or ""
            for i in range(0, len(text), 400):
                yield chunk({"content": text[i : i + 400]})
        yield chunk({}, finish)
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    log(f"启动：http://{HOST}:{PORT}/v1  | 并发上限 {MAX_CONCURRENCY} | 超时 {TIMEOUT:.0f}s")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
