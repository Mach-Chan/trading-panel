"""代理两个核心解析函数的测试。

这两个函数是整个适配器的成败所在：
- parse_tool_calls: 把模型的文本回复翻译回 OpenAI 的 tool_calls 结构，
  翻译失败会让调用方的 agent 循环彻底停摆。
- unwrap_json: 剥掉模型多说的解释文字，让严格 JSON 解析器能接受。
  剥过头会破坏正常回复，剥不够则调用方报 ambiguous_json。

用例大多来自真实踩坑，而非臆想的边界情况。
"""

import importlib.util
import json
from pathlib import Path

import pytest

# 代理是个可执行脚本而非包，按路径加载
_PROXY_PATH = Path(__file__).resolve().parents[1] / "proxy" / "claude_proxy.py"
_spec = importlib.util.spec_from_file_location("claude_proxy", _PROXY_PATH)
proxy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(proxy)


# ---------------------------------------------------------------- parse_tool_calls

def test_parses_tool_calls_fence():
    """约定的 ```tool_calls 围栏——最常见的正常路径。"""
    text = '```tool_calls\n[{"name": "get_stock_data", "arguments": {"symbol": "RKLB"}}]\n```'
    content, calls = proxy.parse_tool_calls(text)

    assert content is None
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "get_stock_data"
    assert json.loads(calls[0]["function"]["arguments"]) == {"symbol": "RKLB"}
    assert calls[0]["type"] == "function"
    assert calls[0]["id"].startswith("call_")


def test_parses_json_fence_as_fallback():
    """模型经常把围栏标成 json 而不是 tool_calls，也要认。"""
    text = '```json\n[{"name": "get_news", "arguments": {"q": "RKLB"}}]\n```'
    _, calls = proxy.parse_tool_calls(text)

    assert calls is not None and calls[0]["function"]["name"] == "get_news"


def test_parses_multiple_tool_calls():
    """一次返回多个工具调用（并行执行），实测中真实出现过。"""
    text = (
        '```tool_calls\n'
        '[{"name": "get_stock_data", "arguments": {"symbol": "RKLB"}},'
        ' {"name": "get_verified_market_snapshot", "arguments": {}}]\n'
        '```'
    )
    _, calls = proxy.parse_tool_calls(text)

    assert [c["function"]["name"] for c in calls] == [
        "get_stock_data",
        "get_verified_market_snapshot",
    ]


def test_parses_bare_json_array_without_fence():
    """模型偶尔省掉围栏直接吐数组。"""
    text = '[{"name": "get_price", "arguments": {"symbol": "TSLA"}}]'
    _, calls = proxy.parse_tool_calls(text)

    assert calls is not None and calls[0]["function"]["name"] == "get_price"


def test_plain_prose_is_not_mistaken_for_tool_call():
    """普通文本回复必须原样返回，不能被误判成工具调用。"""
    text = "RKLB 近 5 个交易日收盘价从 78.20 上行至 80.25，累计涨约 2.6%。"
    content, calls = proxy.parse_tool_calls(text)

    assert calls is None
    assert content == text


def test_json_without_name_field_is_not_a_tool_call():
    """分析结果之类的 JSON 里没有 name 字段，不能被当成工具调用吞掉。

    这是最危险的误判：一旦发生，调用方拿到的是空 content 和一个假工具调用。
    """
    text = '```json\n{"rating": "Hold", "score": 58}\n```'
    content, calls = proxy.parse_tool_calls(text)

    assert calls is None
    assert content == text


def test_arguments_are_serialized_to_string():
    """OpenAI 协议要求 arguments 是字符串，不是对象。"""
    text = '```tool_calls\n[{"name": "f", "arguments": {"a": 1, "b": [2, 3]}}]\n```'
    _, calls = proxy.parse_tool_calls(text)

    assert isinstance(calls[0]["function"]["arguments"], str)
    assert json.loads(calls[0]["function"]["arguments"]) == {"a": 1, "b": [2, 3]}


def test_empty_response_returns_empty_content():
    content, calls = proxy.parse_tool_calls("")

    assert calls is None
    assert content == ""


# ---------------------------------------------------------------- unwrap_json

def test_unwrap_strips_leading_prose():
    """真实踩坑：模型在 JSON 前加一句解释，调用方报 ambiguous_json。"""
    text = '好的，以下是分析结果：\n\n{"rating": "Hold", "score": 58}'
    assert json.loads(proxy.unwrap_json(text)) == {"rating": "Hold", "score": 58}


def test_unwrap_strips_fence():
    text = '```json\n{"rating": "Buy"}\n```'
    assert json.loads(proxy.unwrap_json(text)) == {"rating": "Buy"}


def test_unwrap_strips_trailing_prose():
    text = '{"rating": "Sell"}\n\n以上就是本次分析。'
    assert json.loads(proxy.unwrap_json(text)) == {"rating": "Sell"}


def test_unwrap_leaves_clean_json_untouched():
    """已经干净的 JSON 不做任何改动，避免无谓的重新序列化。"""
    text = '{"rating": "Hold", "score": 58}'
    assert proxy.unwrap_json(text) == text


def test_unwrap_does_not_damage_prose_without_json():
    """剥不出合法 JSON 时必须原样返回——不做破坏性猜测。

    大盘复盘之类的正常文本回复里也可能出现花括号，
    这里绝不能因为看到 { } 就乱切。
    """
    text = "本次分析未使用 {占位符} 模板，结论如下：维持观望。"
    assert proxy.unwrap_json(text) == text


def test_unwrap_handles_nested_objects():
    """嵌套结构不能被截断——按第一个 { 到最后一个 } 取。"""
    payload = {"dashboard": {"core": {"one_sentence": "维持观望"}, "score": 58}}
    text = f"分析完成：\n{json.dumps(payload, ensure_ascii=False)}\n请查收。"
    assert json.loads(proxy.unwrap_json(text)) == payload


def test_unwrap_of_empty_string_is_safe():
    assert proxy.unwrap_json("") == ""


# ---------------------------------------------------------------- flatten

def test_flatten_merges_system_messages():
    system, _ = proxy.flatten(
        [
            {"role": "system", "content": "你是分析师。"},
            {"role": "system", "content": "只用中文回答。"},
            {"role": "user", "content": "分析 RKLB"},
        ],
        None,
    )

    assert "你是分析师。" in system
    assert "只用中文回答。" in system


def test_flatten_renders_tool_results_back_into_prompt():
    """claude -p 无会话状态，工具结果必须回渲染进提示词，
    否则模型看不到返回值会重复调用同一个工具。"""
    _, user = proxy.flatten(
        [
            {"role": "user", "content": "查一下 RKLB"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_stock_data", "arguments": '{"symbol":"RKLB"}'},
                    }
                ],
            },
            {"role": "tool", "name": "get_stock_data", "content": "08-15,80.25"},
        ],
        None,
    )

    assert "get_stock_data" in user
    assert "08-15,80.25" in user
    assert "不要重复调用同一个工具" in user


def test_flatten_renders_tool_schemas_into_system_prompt():
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_stock_data",
                "description": "获取股价",
                "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}}},
            },
        }
    ]
    system, _ = proxy.flatten([{"role": "user", "content": "hi"}], tools)

    assert "get_stock_data" in system
    assert "tool_calls" in system


def test_flatten_handles_multimodal_content_array():
    """content 可能是数组形式的内容块，取其中文本，不能崩。"""
    _, user = proxy.flatten(
        [{"role": "user", "content": [{"type": "text", "text": "分析 TSLA"}]}],
        None,
    )

    assert "分析 TSLA" in user


# ---------------------------------------------------------------- 模型别名

@pytest.mark.parametrize(
    "incoming,expected",
    [
        ("claude-opus", "opus"),
        ("claude-sonnet", "sonnet"),
        ("sonnet", "sonnet"),
        ("gpt-4o", "sonnet"),          # 假装成 OpenAI 模型名的调用方
        ("完全没见过的名字", "sonnet"),  # 未知一律回落到 sonnet，不报错
    ],
)
def test_model_alias_resolution(incoming, expected):
    assert proxy.MODEL_ALIASES.get(incoming, "sonnet") == expected
