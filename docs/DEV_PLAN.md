# StockPulse v2.0 开发计划 — AI 驱动的个性化分析助手

> **核心理念**：不做同花顺/东方财富已有的功能。利用项目独有的数据（顽主杯热榜 + 自定义龙虎榜信号规则），
> 通过 AI 生成标准软件无法提供的个性化分析和交易建议。

---

## 目录

1. [系统架构](#系统架构)
2. [功能一：AI 每日复盘 + 交易建议](#功能一ai-每日复盘--交易建议)
3. [功能二：智能选股助手](#功能二智能选股助手)
4. [功能三：信号有效性诊断](#功能三信号有效性诊断)
5. [通用模块：LLM 接入层](#通用模块llm-接入层)
6. [前端设计](#前端设计)
7. [实施计划](#实施计划)

---

## 系统架构

```
                    ┌──────────────────────┐
                    │     用户（你）        │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │   前端 (Vue3)         │
                    │                      │
                    │  ┌─────────────────┐ │
                    │  │ AI 分析页        │ │
                    │  │ ├─ 每日复盘报告  │ │
                    │  │ ├─ 智能选股助手  │ │
                    │  │ └─ 信号诊断面板  │ │
                    │  └─────────────────┘ │
                    └──────────┬───────────┘
                               │ HTTP
                    ┌──────────▼───────────┐
                    │   后端 (FastAPI)      │
                    │                      │
                    │  ┌───────────────┐   │
                    │  │ AI 分析引擎    │   │
                    │  │ ├─ Prompt 组装 │   │
                    │  │ ├─ 数据预处理  │   │
                    │  │ └─ 结果解析    │   │
                    │  └───────┬───────┘   │
                    │          │            │
                    │  ┌───────▼───────┐   │
                    │  │ LLM 客户端     │   │
                    │  │ (OpenAI 兼容)  │   │
                    │  └───────┬───────┘   │
                    └──────────┼───────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
    ┌─────────▼──────┐ ┌──────▼───────┐ ┌──────▼───────┐
    │ Hermes (本地)   │ │ DeepSeek API │ │ 其他模型     │
    │ OpenAI 兼容     │ │ 云端备选     │ │ 可扩展       │
    └────────────────┘ └──────────────┘ └──────────────┘
```

### 数据流

```
已有数据（SQLite）              AI 分析所需数据
────────────────              ──────────────
stock_records (热榜)     →   今日热榜 Top10 + 近5日变化趋势
lhb_signals  (信号股)    →   今日境外机构/机构密集信号详情
lhb_pool     (股池追踪)  →   信号股 d1-d30 表现统计（胜率/均值）
lhb_trading_desk (营业部) →  境外机构买卖明细
limit_stats  (涨跌停)    →   （v2.1 可选）市场情绪辅助判断
```

---

## 通用模块：LLM 接入层

### 配置

```bash
# .env 新增
LLM_API_BASE=http://your-server:8000/v1   # Hermes OpenAI 兼容地址
LLM_API_KEY=                               # 本地模型留空，云端填 key
LLM_MODEL=Hermes                           # 模型名称
LLM_MAX_TOKENS=4096                        # 最大输出 token
LLM_TEMPERATURE=0.7                        # 温度
```

### 后端模块

```python
# backend/llm_client.py

class LLMClient:
    """OpenAI 兼容的 LLM 客户端，支持本地 Hermes / 云端 DeepSeek 等。"""

    def __init__(self):
        self.api_base = settings.llm_api_base
        self.api_key = settings.llm_api_key or "not-needed"
        self.model = settings.llm_model

    async def chat(self, system_prompt: str, user_prompt: str) -> str:
        """调用 LLM 生成回复。"""

    async def chat_json(self, system_prompt: str, user_prompt: str,
                        schema: dict) -> dict:
        """调用 LLM 并要求返回 JSON 格式（结构化输出）。"""
```

**设计决策**：
- 使用 `httpx.AsyncClient` 调用 `/v1/chat/completions`，不引入 `openai` SDK 减少依赖
- `chat_json()` 在 prompt 中明确要求 JSON 输出 + 用正则提取 JSON 块，兼容不支持 structured output 的模型
- 超时 60s，自动重试 1 次
- 所有 AI 请求记录到 `ai_analysis_log` 表，方便回溯

### 数据库表

```sql
-- AI 分析日志（缓存 + 历史）
CREATE TABLE IF NOT EXISTS ai_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_type TEXT NOT NULL,       -- 'daily_review' | 'stock_pick' | 'signal_diagnosis'
    date TEXT NOT NULL,                -- 分析日期
    input_summary TEXT,               -- 输入数据摘要（用于调试）
    prompt_hash TEXT,                 -- prompt 哈希（用于缓存判断）
    response TEXT NOT NULL,           -- AI 原始回复
    parsed_result TEXT,               -- 解析后的 JSON 结果
    model_name TEXT,                  -- 使用的模型
    tokens_used INTEGER,              -- token 消耗
    duration_ms INTEGER,              -- 耗时
    created_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(analysis_type, date, prompt_hash)
);
```

---

## 功能一：AI 每日复盘 + 交易建议

### 需求说明

每天爬完数据后，AI 自动生成个性化复盘报告：
- **今日解读**：热榜变化 + 龙虎榜信号的专业分析
- **趋势判断**：结合近5日数据判断市场风格（短线/趋势/冷清）
- **具体建议**：哪些股票值得关注、买入/观望/止损建议
- **风险提示**：信号失效、情绪过热等警告

### Prompt 模板

```
backend/prompts/daily_review.md
```

```markdown
你是一位资深 A 股短线交易分析师。基于以下数据生成今日复盘报告。

## 你的分析框架
1. 用「顽主杯热榜」数据判断散户关注方向（跟风/异动）
2. 用「龙虎榜境外机构/机构密集」信号判断主力资金方向
3. 用「信号股历史胜率」校准建议的可信度

## 今日数据

### 热榜 Top10 变化
{hot_list_data}

### 龙虎榜信号股
{lhb_signal_data}

### 信号股近30天胜率
{backtest_data}

### 近5日热榜趋势
{streak_data}

## 输出格式（严格遵守）

### 一、今日市场解读
（2-3 句话概括今日特征）

### 二、热榜分析
- 关注方向：xxx
- 异常信号：xxx
- 与昨日对比：xxx

### 三、龙虎榜信号点评
（逐只信号股点评，含买卖建议）

### 四、明日关注
（列出 3-5 只最值得关注的股票，给出具体理由和关注价位）

### 五、操作建议
- 适合策略：xxx（短线/趋势/观望）
- 仓位建议：xxx
- 风险提示：xxx
```

### API 设计

| Method | Path | 说明 |
|---|---|---|
| `GET` | `/api/ai/daily-review?date=` | 获取指定日期 AI 复盘（优先从缓存读取） |
| `POST` | `/api/ai/daily-review/generate?date=` | 强制重新生成（不读缓存） |
| `GET` | `/api/ai/history?type=daily_review&days=30` | 历史复盘列表 |

### 响应格式

```json
{
  "date": "2026-06-05",
  "generated_at": "2026-06-05 20:15:00",
  "model": "Hermes",
  "sections": {
    "market_summary": "今日市场特征...",
    "hot_list_analysis": "...",
    "lhb_commentary": [
      {
        "stock_name": "xxx",
        "stock_code": "600xxx",
        "signal_type": "foreign",
        "comment": "境外机构净买入1.2亿...",
        "suggestion": "关注",
        "target_price": 25.6
      }
    ],
    "watch_list": [
      {
        "stock_name": "xxx",
        "stock_code": "600xxx",
        "reason": "...",
        "strategy": "短线",
        "stop_loss": 23.5
      }
    ],
    "trading_advice": {
      "strategy": "短线为主",
      "position_pct": 50,
      "risk_warnings": ["连续3日北向净卖出", "涨停家数降至20以下"]
    }
  },
  "raw_text": "AI 原始输出文本..."
}
```

### 数据组装逻辑

```python
# backend/ai_engine.py

class AIEngine:
    """AI 分析引擎：组装数据 → 构建 prompt → 调用 LLM → 解析结果。"""

    def __init__(self, db: Database, llm: LLMClient):
        self.db = db
        self.llm = llm

    async def generate_daily_review(self, date_str: str) -> dict:
        """生成每日复盘报告。"""

        # 1. 从数据库收集数据
        hot_records = self.db.query_by_date(date_str)         # 今日热榜
        prev_records = self.db.query_by_date(prev_trade_date)  # 昨日热榜
        signals = self.db.query_lhb_signals(date_str)          # 今日信号
        backtest = self.db.query_backtest(months=1)            # 近月胜率
        streaks = self.db.query_streak_stats(days=5, min_streak=2)  # 近5日连板

        # 2. 格式化为 prompt 数据块
        hot_list_data = self._format_hot_list(hot_records, prev_records)
        lhb_signal_data = self._format_lhb_signals(signals)
        backtest_data = self._format_backtest(backtest)
        streak_data = self._format_streaks(streaks)

        # 3. 读取 prompt 模板并填充
        prompt = self._render_prompt("daily_review.md", {
            "hot_list_data": hot_list_data,
            "lhb_signal_data": lhb_signal_data,
            "backtest_data": backtest_data,
            "streak_data": streak_data,
        })

        # 4. 调用 LLM
        response = await self.llm.chat(
            system_prompt=DAILY_REVIEW_SYSTEM,
            user_prompt=prompt,
        )

        # 5. 解析结果
        parsed = self._parse_daily_review(response)

        # 6. 缓存到数据库
        self._save_analysis("daily_review", date_str, prompt, response, parsed)

        return parsed
```

### 前端设计

在 DailyView 现有"复盘报告"面板的基础上升级为 **AI 复盘报告**：

```
┌──────────────────────────────────────────────────┐
│  🤖 AI 复盘报告  2026-06-05          [重新生成]   │
├──────────────────────────────────────────────────┤
│                                                  │
│  📊 今日市场解读                                  │
│  今日市场呈现典型的短线轮动特征，热榜半导体...      │
│                                                  │
│  🔥 热榜分析                                      │
│  关注方向: 半导体 + AI                             │
│  异常信号: 中芯国际连续3日上榜，热度持续上升        │
│                                                  │
│  🐯 龙虎榜信号点评                                │
│  ┌────────────────────────────────────────────┐  │
│  │ 中芯国际  境外机构净买入 1.2亿              │  │
│  │ 💡 摩根大通+高盛同步买入，历史胜率 68%       │  │
│  │ 📌 建议：关注，短线目标 58.5，止损 53.0     │  │
│  └────────────────────────────────────────────┘  │
│                                                  │
│  ⭐ 明日关注                                      │
│  1. 中芯国际 — 境外机构+热榜共振，短线             │
│  2. 北方华创 — 龙虎榜连续2日机构密集              │
│  3. xxx                                        │
│                                                  │
│  📋 操作建议                                      │
│  适合策略: 短线为主      仓位: 50%                 │
│  ⚠️ 风险: 热榜半导体占比过高，注意轮动风险         │
│                                                  │
│  ─── 生成于 20:15 | Hermes | 2.1s ───             │
└──────────────────────────────────────────────────┘
```

---

## 功能二：智能选股助手

### 需求说明

用户输入偏好条件（或选择预设策略），AI 从热榜 + 信号股中筛选推荐，并给出具体理由。类似私人投顾。

### 交互设计

```
┌──────────────────────────────────────────────────┐
│  🤖 智能选股助手                                  │
│                                                  │
│  选择策略：                                       │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐          │
│  │ 🔴 激进   │ │ 🟡 均衡  │ │ 🟢 稳健  │          │
│  │ 短线打板  │ │ 信号跟风  │ │ 低吸潜伏  │          │
│  └──────────┘ └──────────┘ └──────────┘          │
│                                                  │
│  或自定义：                                       │
│  ┌────────────────────────────────────────────┐  │
│  │ 我想找最近3天境外机构持续买入的半导体股票    │  │
│  └────────────────────────────────────────────┘  │
│                                        [开始筛选] │
│                                                  │
│  ── AI 推荐结果 ──                                │
│                                                  │
│  ┌────────────────────────────────────────────┐  │
│  │ 1. 中芯国际 (688981)  ⭐⭐⭐⭐              │  │
│  │    匹配理由: 境外机构连续3日净买入            │  │
│  │    热榜排名: #2 (↑从昨日#5)                 │  │
│  │    信号胜率: 68% (近30天 d5)                │  │
│  │    建议: 短线关注，目标 58.5                │  │
│  ├────────────────────────────────────────────┤  │
│  │ 2. 北方华创 (002371)  ⭐⭐⭐               │  │
│  │    ...                                     │  │
│  └────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────┘
```

### Prompt 模板

```markdown
你是一位 A 股选股顾问。根据用户的偏好，从以下候选股票中筛选推荐。

## 用户偏好
{user_preference}

## 候选股票池（今日热榜 + 龙虎榜信号股）
{candidate_stocks}

## 信号股历史胜率数据
{backtest_data}

## 近5日热榜趋势数据
{streak_data}

## 输出格式
返回 JSON 数组，每个推荐包含：
- stock_name, stock_code
- score: 1-5 星推荐指数
- match_reason: 匹配用户偏好的具体理由
- signal_strength: 信号强度（强/中/弱）
- suggestion: 操作建议
- target_price: 参考目标价
- stop_loss: 止损参考价
- risk_note: 风险提示

最多推荐 5 只，按推荐指数降序。
```

### API 设计

| Method | Path | 说明 |
|---|---|---|
| `POST` | `/api/ai/stock-pick` | 提交选股请求 |
| `GET` | `/api/ai/stock-pick/history?days=7` | 历史选股记录 |

#### 请求体

```json
{
  "strategy": "aggressive",     // aggressive | balanced | conservative
  "custom_prompt": "",          // 自由文本偏好（可选）
  "sector_filter": ["半导体"],  // 板块过滤（可选）
  "max_results": 5
}
```

#### 响应体

```json
{
  "strategy": "aggressive",
  "picks": [
    {
      "stock_name": "中芯国际",
      "stock_code": "688981",
      "score": 4,
      "match_reason": "境外机构连续3日净买入，热榜排名上升",
      "signal_strength": "强",
      "suggestion": "短线关注",
      "target_price": 58.5,
      "stop_loss": 53.0,
      "risk_note": "半导体板块近期涨幅较大，注意回调"
    }
  ],
  "generated_at": "2026-06-05 20:30:00",
  "model": "Hermes"
}
```

### 数据组装逻辑

```python
async def stock_pick(self, strategy: str, custom_prompt: str = "",
                     sector_filter: list[str] = None) -> dict:
    # 1. 构建候选池
    #    - 今日热榜全部10只
    #    - 今日龙虎榜信号股
    #    - 股池追踪中 tracking_days < 10 的活跃股
    candidates = self._build_candidate_pool(date_str, sector_filter)

    # 2. 附加每只候选股的历史数据
    #    - 近5日热榜排名变化
    #    - 龙虎榜信号详情
    #    - d1-d30 跟踪表现（如有）

    # 3. 映射策略到 prompt 参数
    strategy_desc = {
        "aggressive": "偏好短线爆发力强的股票，敢于追高",
        "balanced": "偏好信号确认+趋势配合的股票，兼顾安全边际",
        "conservative": "偏好低位放量、信号刚触发的股票，低吸为主",
    }

    # 4. 调用 LLM
    response = await self.llm.chat_json(...)

    # 5. 解析并缓存
    return parsed_results
```

---

## 功能三：信号有效性诊断

### 需求说明

统计自定义信号规则在不同市场环境下的胜率变化，给出"当前是否应该跟信号"的客观建议。

### 诊断维度

| 维度 | 分析内容 | 数据来源 |
|---|---|---|
| **总体胜率趋势** | 月度胜率变化（上升/下降/稳定） | `lhb_pool` |
| **分信号类型** | 境外机构 vs 机构密集，哪个近期更准 | `lhb_pool` + `lhb_signals` |
| **分板块胜率** | 哪些板块信号效果好 | `lhb_pool` concept_tags |
| **持仓周期** | d1/d3/d5/d10/d20/d30 各周期胜率对比 | `lhb_pool` |
| **市场环境关联** | 热榜情绪热/冷时信号的胜率差异 | `stock_records` + `lhb_pool` |

### 诊断报告格式

```json
{
  "diagnosis_date": "2026-06-05",
  "period": "last_30_days",
  "signal_health": {
    "overall": {
      "win_rate_d5": 0.58,
      "trend": "declining",
      "sample_count": 45,
      "verdict": "⚠️ 近期信号胜率下降，建议降低仓位跟信号"
    },
    "by_type": {
      "foreign": {
        "win_rate_d5": 0.65,
        "trend": "stable",
        "verdict": "✅ 境外机构信号仍然有效"
      },
      "inst_dense": {
        "win_rate_d5": 0.48,
        "trend": "declining",
        "verdict": "⚠️ 机构密集信号近期胜率下滑"
      }
    },
    "by_sector": [
      { "sector": "半导体", "win_rate": 0.72, "count": 12, "verdict": "✅" },
      { "sector": "AI", "win_rate": 0.55, "count": 8, "verdict": "⚠️" }
    ],
    "by_horizon": [
      { "horizon": "d1", "win_rate": 0.52 },
      { "horizon": "d3", "win_rate": 0.55 },
      { "horizon": "d5", "win_rate": 0.58 },
      { "horizon": "d10", "win_rate": 0.60 },
      { "horizon": "d20", "win_rate": 0.55 }
    ]
  },
  "ai_commentary": "近期市场处于震荡阶段，境外机构信号表现稳定但机构密集信号...",
  "recommendations": [
    "优先跟境外机构信号，仓位控制在 50%",
    "机构密集信号暂停操作，观察一周",
    "半导体板块信号表现最佳，可重点关注"
  ]
}
```

### Prompt 模板

```markdown
你是一位量化策略分析师。根据以下信号统计数据，给出当前信号有效性的诊断和建议。

## 信号胜率统计（近30天）
{backtest_stats}

## 按信号类型统计
{type_stats}

## 按板块统计
{sector_stats}

## 按持仓周期统计
{horizon_stats}

## 近5日热榜数据（判断市场情绪环境）
{market_context}

## 输出要求
1. 对每个维度给出简短点评（1-2句）
2. 综合判断当前信号是否值得跟
3. 给出 3 条具体操作建议
```

### API 设计

| Method | Path | 说明 |
|---|---|---|
| `GET` | `/api/ai/signal-diagnosis?period=30` | 获取信号诊断报告 |
| `POST` | `/api/ai/signal-diagnosis/generate` | 重新生成诊断 |

### 前端设计

```
┌──────────────────────────────────────────────────┐
│  🩺 信号有效性诊断           [重新诊断]            │
│                                                  │
│  ┌─────────────────────────────────────────────┐ │
│  │ 总体健康度: ⚠️ 下降趋势                       │ │
│  │ D5 胜率: 58% (上月 65%) ↓                    │ │
│  │ 样本数: 45 只                                │ │
│  └─────────────────────────────────────────────┘ │
│                                                  │
│  分信号类型：                                     │
│  ┌──────────────┬──────────────┐                 │
│  │ 境外机构 ✅   │ 机构密集 ⚠️  │                 │
│  │ 胜率 65% ↗   │ 胜率 48% ↘   │                 │
│  │ 继续跟       │ 暂停观察     │                 │
│  └──────────────┴──────────────┘                 │
│                                                  │
│  板块胜率排行：                                   │
│  半导体 72% ████████████████ ✓                   │
│  AI     55% ███████████░░░░ ⚠                   │
│  新能源 40% ████████░░░░░░░ ✗                    │
│                                                  │
│  🤖 AI 建议：                                    │
│  • 优先跟境外机构信号，仓位控制在 50%             │
│  • 机构密集信号暂停操作，观察一周                 │
│  • 半导体板块信号表现最佳，可重点关注             │
└──────────────────────────────────────────────────┘
```

---

## 前端设计

### 新增 Tab

在 `App.vue` 导航栏新增 `AI 分析` tab：

```typescript
export type ViewTab = 'daily' | 'pnl' | 'range' | 'lhb' | 'ai'
```

```html
<!-- 导航栏 tabs -->
{ key: 'daily', label: '日报' },
{ key: 'pnl', label: '盈亏走势' },
{ key: 'range', label: '时段分析' },
{ key: 'lhb', label: '龙虎榜' },
{ key: 'ai', label: '🤖 AI 分析' },   // 新增
```

### 新增文件

| 文件 | 说明 |
|---|---|
| `frontend/src/views/AIView.vue` | AI 分析主页面 |
| `frontend/src/components/DailyReview.vue` | 每日复盘报告组件 |
| `frontend/src/components/StockPicker.vue` | 智能选股助手组件 |
| `frontend/src/components/SignalDiagnosis.vue` | 信号诊断面板组件 |
| `frontend/src/composables/useAI.ts` | AI API 调用封装 |

### AIView 布局

```
┌──────────────────────────────────────────────────┐
│  🤖 AI 分析                                      │
│                                                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐          │
│  │ 📋 每日   │ │ 🎯 选股  │ │ 🩺 诊断  │ ← 3个子tab│
│  │   复盘    │ │   助手   │ │   面板   │          │
│  └──────────┘ └──────────┘ └──────────┘          │
│                                                  │
│  ┌────────────────────────────────────────────┐  │
│  │                                            │  │
│  │         （当前选中 tab 的内容）              │  │
│  │                                            │  │
│  └────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────┘
```

### LLM 状态提示

AI 分析生成过程中需要友好提示：

- 生成中：`🤖 正在分析...（预计 10-30 秒）`
- 成功：显示结果 + 生成耗时
- 失败：`❌ 分析失败：[错误信息]`，提供重试按钮
- 无数据：`📊 今日数据尚未采集，请先拉取数据`

---

## 后端新增文件

| 文件 | 说明 |
|---|---|
| `backend/llm_client.py` | LLM 客户端（OpenAI 兼容 API） |
| `backend/ai_engine.py` | AI 分析引擎（数据组装 + prompt 模板 + 结果解析） |
| `backend/prompts/` | Prompt 模板目录 |
| `backend/prompts/daily_review.md` | 每日复盘 prompt |
| `backend/prompts/stock_pick.md` | 智能选股 prompt |
| `backend/prompts/signal_diagnosis.md` | 信号诊断 prompt |

---

## 实施计划

### Phase 1：基础设施（Day 1-2）

```
1. backend/config.py 新增 LLM 配置项
2. backend/llm_client.py LLM 客户端
3. database.py 新增 ai_analysis 表
4. 测试 LLM 连接
```

### Phase 2：AI 每日复盘（Day 3-4）

```
1. backend/ai_engine.py 数据组装逻辑
2. backend/prompts/daily_review.md
3. backend/main.py API 路由
4. 前端 AIView + DailyReview 组件
```

### Phase 3：智能选股（Day 5-6）

```
1. backend/prompts/stock_pick.md
2. 候选池构建逻辑
3. API 路由
4. 前端 StockPicker 组件
```

### Phase 4：信号诊断（Day 7）

```
1. backend/prompts/signal_diagnosis.md
2. 统计数据增强（市场环境关联）
3. API 路由
4. 前端 SignalDiagnosis 组件
```

### Phase 5：联调优化（Day 8）

```
1. 错误处理 + 重试机制
2. 缓存策略优化
3. Prompt 调优（根据实际输出效果调整）
4. 自动化：爬虫完成后自动触发 AI 分析
```

---

## 自动化集成

在现有爬虫流程末尾自动触发 AI 分析：

```python
# crawl_lhb.py 的 crawl_lhb() 函数末尾追加：

# 龙虎榜爬取完成 → 自动生成 AI 复盘
if records:
    logger.info("触发 AI 每日复盘生成...")
    engine = AIEngine(db, LLMClient())
    asyncio.run(engine.generate_daily_review(date_str))
    logger.info("AI 复盘生成完成")
```

**Cron 完整流程**：
```
20:00  顽主杯爬虫 → 热榜数据入库
20:05  龙虎榜爬虫 → 信号股 + 股池更新
20:10  AI 自动分析 → 每日复盘生成
```

---

## 后续可扩展方向（不在本期）

| 方向 | 说明 |
|---|---|
| 交易日志 | 记录实际买卖，AI 做交易复盘 |
| 模型对比 | 同一数据同时跑 Hermes + DeepSeek，对比分析质量 |
| 定时推送 | 接入飞书/钉钉，每天自动推送 AI 复盘 |
| 历史回放 | 选择任意历史日期，用 AI 分析"如果那天会怎么建议" |
| Prompt 自定义 | 用户可编辑 prompt 模板，定制分析风格 |
