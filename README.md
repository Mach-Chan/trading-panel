# Trading Panel

把 [TradingAgents](https://github.com/TauricResearch/TradingAgents)（Apache-2.0，arXiv 2412.20138）
接到本地 Claude Code 订阅上运行——**原版一行代码不改，不需要任何 LLM API key**。

> ⚠️ 本项目产出的是**可审查的研究过程**，不是投资建议，也不执行任何交易操作。

---

## 这个项目做了什么

原版 TradingAgents 用 LangGraph 把投研拆成 12 个 AI 角色分工辩论，质量很高，
但它只会说一种语言：**OpenAI 协议的 HTTP API**。要跑它就得有 OpenAI / DeepSeek 之类的 key，按 token 付费。

本项目写了一个**适配器（adapter）**：把本地 `claude -p` 包装成 OpenAI 兼容接口，
于是原版把它当成一个普通的模型服务，正常驱动它自己的状态图。

```
原版 TradingAgents  ──OpenAI 协议──▶  proxy/claude_proxy.py  ──▶  claude -p（你的订阅）
   （零改动）                            （本项目唯一自研代码）
```

自研部分只有 `proxy/claude_proxy.py` 一个文件，约 300 行。数据层、防未来数据泄漏、
记忆、反思、回测——全都是原版的，完整保留。

## 适配器解决的三个问题

1. **纯模型化**
   Claude Code 默认是个 agent，会自己去搜网、读文件。但工具必须由 TradingAgents 执行，
   所以代理会剥掉它的系统提示词和全部内置工具，让它退化成一个纯粹的大模型。

2. **工具调用（function calling）双向翻译**
   原版用 LangChain 给模型绑定工具，并检查返回里的 `tool_calls` 字段来驱动状态图。
   `claude -p` 只吐纯文本，所以代理去程把工具 schema 渲染进系统提示词并规定严格格式，
   回程再把模型的回复解析回 OpenAI 的 `tool_calls` 结构。

3. **严格 JSON 输出**
   部分调用方（如某些报告生成器）用极严格的解析器：JSON 之外出现任何字符就判失败。
   代理一方面在系统提示词里加了输出纪律，另一方面在回程做兜底清洗——
   只在确实能剥出一个完整合法 JSON 时才动手，剥不出来就原样返回，不做破坏性猜测。

## 用法

```bash
./run.sh                    # 打开原版交互式看板，自己选股票和分析师
./run.sh RKLB               # 直接分析某只票，输出完整报告
./run.sh RKLB 2026-05-01    # 时间旅行：以那天的视角分析（原版的防未来数据泄漏机制有效）
```

`run.sh` 会自动检查代理在不在、不在就拉起来，然后加载 `.env` 跑原版。

配置在 `.env`：把 `TRADINGAGENTS_LLM_PROVIDER` 设为 `openai`、
`TRADINGAGENTS_LLM_BACKEND_URL` 指向本地代理即可。行情数据仍需 Alpha Vantage key（免费档够用）。

## 目录结构

```
trading-panel/
├── proxy/claude_proxy.py    唯一自研代码：OpenAI 兼容代理
├── upstream/TradingAgents/  原版全量克隆，保持原样（gitignore，需自行 clone 并装依赖）
├── run.sh / analyze.py      启动脚本
├── workflows/               另一条路线，见下
└── skills/                  另一条路线，见下
```

## 关于 `workflows/` 与 `skills/`

这两个目录是**另一条路线**：不接原版，而是在 Claude Code 里用 subagent 直接重写
那套编排逻辑（四路分析师 → 多空辩论 → 研究经理 → 交易员 → 三方风控 → 组合经理）。

**必须说清楚它不是复刻。** 按代码量算，原版 7967 行 Python 里数据层占 37%、
角色与提示词占 30%、编排图占 15%；这条路线只重写了编排的**拓扑结构**（366 行），
数据层完全没有。最重要的缺失是：**原版在代码里强制过滤基准日之后的数据，
而这条路线只在提示词里要求模型"不要引用未来信息"——靠自觉不靠机制**。

后果很实际：**这条路线做不了可信的历史回测**，跑出的漂亮成绩单可能只是在背答案。
它适合"今天怎么看"的当下分析，不适合验证判断力。

要做回测，用 `./run.sh <代码> <历史日期>` 走原版。

## 已知限制

- **情绪面数据经常取不到**：机构持仓、做空比例、社区热度的公开渠道有限，报告会如实标注。
- **结论随模型与数据变化**：同一只票不同时间、不同轮数跑出的结论可能不同。这是特性不是缺陷——
  本项目的价值在于论证过程可审查，而不是给出唯一正确答案。
- **不做实时行情**：分析基于公开数据源，可能滞后，不适合日内交易场景。

## 致谢

编排范式与全部投研逻辑来自 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)（Apache-2.0）。
本项目只提供一个运行时适配层。
