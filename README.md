# Trading Panel

在 Claude Code 上复刻 [TradingAgents](https://github.com/TauricResearch/TradingAgents)
（Apache-2.0，arXiv 2412.20138）的多智能体投研编排——**不需要任何 API key，不需要 Python 环境，
不需要容器**，全部跑在 Claude Code 订阅上。

> ⚠️ 本项目产出的是**可审查的研究过程**，不是投资建议，也不执行任何交易操作。

---

## 这是什么

原版 TradingAgents 用 LangGraph 把投研拆成 12 个 AI 角色，让它们分工、辩论、层层把关。
本项目保留同一套编排范式，把执行层换成 Claude Code：

| 原版（Python + LangGraph） | 本项目（Claude Code） |
|---|---|
| `StateGraph` 状态图 | Workflow 脚本（JavaScript） |
| `add_node(agent)` | `agent(提示词, {schema})` |
| `add_edge` 固定边 | 脚本里的执行顺序 |
| `add_conditional_edges` 条件边 | `for` 循环与 `if` |
| `max_debate_rounds` 计数器 | `ROUNDS` 常量（硬封顶 1–3） |
| 共享 state 字典 | 阶段之间传递返回值 |
| Pydantic 结构化输出 | JSON Schema 校验（不合规自动重试） |
| checkpoint 断点续跑 | `resumeFromRunId` |
| OpenAI / DeepSeek API + key | **Claude Code 订阅，零 key** |
| Alpha Vantage 行情 key | 联网检索取证 |

## 流程

```
                 ┌── 技术面分析师 ──┐
   标的 + 基准日 ─┼── 基本面分析师 ──┼─→ 证据包
                 ├── 新闻面分析师 ──┤        （四路并发）
                 └── 情绪面分析师 ──┘
                          ↓
            看多研究员 ⇄ 看空研究员   （轮流交锋，轮数硬封顶）
                          ↓
                   研究经理裁决        （独立角色，逐条回应双方）
                          ↓
                   交易员出方案        （方向 + 分批 + 止损 + 仓位）
                          ↓
        激进派 ∥ 保守派 → 中性派       （两派并发挑刺，中性派专职抓矛盾）
                          ↓
                  组合经理拍板         （评级 + 执行框架 + 数据盲区）
                          ↓
                    决策台账 → 日后回填复盘
```

## 相对原版的三处改进

1. **数据盲区是强制字段。**
   每份分析报告的 schema 里 `数据盲区` 必填，最终裁决书必须汇总并说明"结论因此在哪些方面不可靠"。
   起因：原版实跑时 StockTwits 返回 403、Reddit 限流 429，情绪面实际是瞎的，
   但报告只字未提，照样输出了自信满满的裁决书。**静默降级是这类系统最大的失真来源。**

2. **风控三方改为「两派并发 + 中性派收口」。**
   原版是三方轮流转圈发言，后发言者会被前面带节奏。这里让激进派与保守派**独立并行**审查，
   互不可见，然后中性派拿到两份完整意见**专职找自相矛盾**——
   例如"一边反对宽止损、一边又要求把止损贴着现价设，在高波动标的上几乎必然误伤"。
   这类矛盾在原版实跑中是最高价值的产出，现在被制度化要求，而不是碰运气。

3. **全程结构化输出。**
   每个角色都有 JSON Schema 约束，组合经理被 schema 逼着必须同时填
   "为何不更激进"与"为何不更保守"——两边都要交代，堵住和稀泥。

## 用法

### 完整版（Workflow，12 个角色，确定性编排）

在 Claude Code 里说"用 trading-panel 工作流分析 RKLB"，或直接调用：

```
Workflow({
  name: 'trading-panel',
  args: {
    ticker: 'RKLB',
    name: 'Rocket Lab',
    asOf: '2026-08-17',
    rounds: 1,                    // 1–3，越大吵得越深，成本成倍
    stance: '纯研究（未持仓）'      // 或 '已持仓，考虑加减' / '空仓，考虑建仓'
  }
})
```

### 轻量版（Skill，同一套流程，模型自主编排）

直接说"分析一下 RKLB"或"交易评审团"即可触发。适合快速看一眼，
不需要完整 12 角色的场合。

## 目录结构

```
trading-panel/
├── README.md                       本文件
├── workflows/trading-panel.js      完整版编排脚本（软链到 ~/.claude/workflows/）
├── skills/trading-panel/SKILL.md   轻量版技能（软链到 ~/.claude/skills/）
└── docs/                           设计笔记与实跑记录
```

代码在本项目里，通过软链接被 Claude Code 发现——**这里是唯一的真实来源**，
改这里即时生效，也方便用 git 管理版本。

## 决策台账

分析结果写入 `~/Documents/ObsidianVault/笔记/学习/trading/`：
每次分析一个文件，外加一份 `LEDGER.md` 索引和一份 `LESSONS.md` 教训集。

放在 Obsidian 而不是本项目里，是因为台账是**要反复读、要和其他笔记互链**的知识，
而这里放的是代码。想改到本项目内也可以，改 SKILL.md 里的路径即可。

**回填复盘**：说"复盘持仓判断"或 `/trading-panel 复盘`，
会读出所有"待验证"条目、对照真实走势、更新状态、把教训写进 LESSONS.md。
分析同一标的时会先读历史判断——这是原版 `TradingMemoryLog` 的等价物，
让判断跨次连续、可追责。

## 已知限制

- **情绪面数据经常取不到**：机构持仓、做空比例、社区热度公开渠道有限，报告会如实标注。
- **结论随模型与数据变化**：同一只票不同时间、不同轮数跑出的结论可能不同。这是特性不是缺陷——
  本项目的价值在于论证过程可审查，而不是给出唯一正确答案。
- **不做实时行情**：分析基于检索到的公开数据，可能滞后。不适合日内交易场景。
