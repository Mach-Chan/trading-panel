import sys
from datetime import date
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

ticker = sys.argv[1]
as_of = sys.argv[2] if len(sys.argv) > 2 else date.today().isoformat()

config = DEFAULT_CONFIG.copy()
config["output_language"] = "zh"

ta = TradingAgentsGraph(debug=False, config=config)
state, decision = ta.propagate(ticker, as_of)

print("\n" + "=" * 70)
print(f"【{ticker} 最终决策 / {as_of}】")
print("=" * 70)
print(decision)

for label, key in [
    ("市场技术面", "market_report"),
    ("情绪面", "sentiment_report"),
    ("新闻面", "news_report"),
    ("基本面", "fundamentals_report"),
    ("投资计划(研究经理裁决)", "investment_plan"),
    ("交易员方案", "trader_investment_plan"),
]:
    body = state.get(key)
    if body:
        print("\n" + "-" * 70)
        print(f"◆ {label}")
        print("-" * 70)
        print(body)

dbg = state.get("investment_debate_state") or {}
if dbg.get("bull_history") or dbg.get("bear_history"):
    print("\n" + "-" * 70)
    print("◆ 多空辩论实录")
    print("-" * 70)
    print("【看多】\n" + str(dbg.get("bull_history", "")))
    print("\n【看空】\n" + str(dbg.get("bear_history", "")))

risk = state.get("risk_debate_state") or {}
if risk.get("judge_decision"):
    print("\n" + "-" * 70)
    print("◆ 风控三方与最终裁决")
    print("-" * 70)
    print(str(risk.get("judge_decision", "")))
