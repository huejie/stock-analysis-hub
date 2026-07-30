# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

股票热榜分析工具 (StockPulse) — 个人使用的股票热榜 Top10 数据采集与可视化看板。用户上传微信小程序股票热榜截图，通过百度 OCR 识别提取结构化数据，存储到 SQLite 后生成日报/周报/月报。中国 A 股市场数据，UI 全中文。

## Common Commands

```bash
# 安装依赖
pip install -r requirements.txt

# 前端开发（Vite dev server on :5173，代理 /api 到 :8888）
cd frontend && npm install && npm run dev

# 前端构建（输出到 frontend/dist/）
cd frontend && npm run build

# 启动后端开发服务器（端口 8888，带热重载）
python run.py

# Docker 启动
docker-compose up --build

# 手动运行顽主杯爬虫
python crawl.py              # 今天
python crawl.py 2026-04-30   # 指定日期

# 手动运行龙虎榜爬虫
python crawl_lhb.py              # 今天
python crawl_lhb.py 2026-04-30   # 指定日期

# Linux 安装 cron 定时任务（每天 20:00 自动爬取）
bash setup_crawler_cron.sh        # 安装
bash setup_crawler_cron.sh remove # 卸载

# 运行全部测试
pytest

# 运行单个测试文件 / 测试函数
pytest tests/test_ocr_parser.py
pytest tests/test_ocr_parser.py::test_parse_full_text
pytest -v  # 详细输出
```

## Architecture

### Data Flow

`图片上传` → `POST /api/upload` → `百度 OCR API` → `parse_ocr_text()` 正则解析 → `前端确认/编辑 Modal` → `POST /api/records` → `SQLite`

或自动爬取流程：

`cron 20:00` → `crawl.py` → `顽主杯 API`（股票热榜 + 收益数据） → `upsert_records` → `SQLite`

龙虎榜爬取流程：

`crawl_lhb.py` → `东方财富 API`（龙虎榜汇总 + 营业部买卖明细） → 识别信号股 → `SQLite`

### Backend (FastAPI, Python)

- **`backend/main.py`** — FastAPI 应用入口，所有 API 路由。模块级 `db` 实例，测试通过 `monkeypatch` 替换。静态文件服务优先从 `frontend/dist/`（构建产物），fallback 到 `frontend/`（开发模式）。
- **`backend/ocr.py`** — 核心解析逻辑。`ocr_image()` 调百度 OCR API（httpx 同步）；`parse_ocr_text()` 用"中文名+热度值(w)"锚点定位，不依赖排名数字。`KNOWN_SECTORS` 硬编码板块关键词列表。
- **`backend/crawler.py`** — 顽主杯数据爬虫。从 `api.hunanwanzhu.com` 爬取股票热榜和收益数据，映射到数据库字段后写入。`is_trade_day()` 含 2026 年节假日/调休日历。使用 `upsert_records` 支持重复执行。
- **`backend/lhb_crawler.py`** — 东方财富龙虎榜爬虫。从 `datacenter-web.eastmoney.com` 抓取龙虎榜日汇总和营业部买卖明细，识别境外机构/机构密集信号股，获取个股概念板块标签。
- **`backend/database.py`** — SQLite 封装。表：`stock_records`（主数据）、`season_daily_stats`（赛季每日盈亏）、`seasons`（命名时间范围）、`lhb_records`（龙虎榜日汇总）、`lhb_trading_desk`（营业部买卖明细）、`lhb_signals`（信号股）、`lhb_pool`（股池追踪，含 d1-d30 涨跌幅跟踪）。每次操作新建连接（`_get_conn`）。`_init_db()` 含 `ALTER TABLE` 做字段迁移。所有 upsert 操作使用 `COALESCE` 保护已有数据不被 NULL 覆盖。`_is_st()` 过滤 ST 股票。
- **`backend/models.py`** — Pydantic v2 模型。`StockRecord`（14 字段含 `total_fund`）、`StockRecordResponse`、`UploadResult`、`DateInfo`。
- **`backend/config.py`** — `pydantic-settings` 从 `.env` 加载，路径默认相对于项目根目录。
- **`crawl.py`** — 顽主杯爬虫入口脚本，支持 `python crawl.py [YYYY-MM-DD]`。
- **`crawl_lhb.py`** — 龙虎榜爬虫入口脚本，用法同 `crawl.py`。
- **`setup_crawler_cron.sh`** — Linux cron 安装/卸载脚本，每天 20:00 触发顽主杯爬虫。

### Frontend (Vue 3 + TypeScript + Vite)

`frontend_legacy/` 保留了旧版 vanilla JS 前端作为备份，当前使用 `frontend/` 目录。

- **路由**：`/preview`（只读）和 `/admin`（含上传功能），通过 Vue Router meta + provide/inject 传递 `isAdmin`。默认视图为日报。
- **视图**：`DailyView`（日报 Top10 卡片 + 连续上榜追踪 + 复盘报告面板 + 近5日对比表格 + 趋势折线图）、`PnlView`（赛季盈亏走势 + 仓位百分比）、`RangeView`（跨日分析）、`LhbView`（龙虎榜信号股 + 营业部明细展开 + 板块分析 + 股池追踪 + 胜率回测）、`AIView`（AI 分析：每日复盘 + 智能选股 + 信号诊断三个子 tab）。
- **组件**：`StockCard`、`StockDetail`（个股详情弹窗，含排名趋势折线图+龙虎榜关联）、`UploadArea`、`ConfirmModal`、`ChartBox`、`TimeFilter`、`EmptyState`。
- **Composables**：`useApi`（类型化 fetch）、`useChart`（ECharts 生命周期）。
- **Charts**（`frontend/src/charts/`）：图表模块，共享暗色主题（`theme.ts`）。
- **CSS**：暗色主题 + 玻璃态导航栏，金银铜排名徽章，红涨绿跌（中国市场惯例），响应式断点 900px/600px。

### Key Design Decisions

- **OCR 锚点模式**：百度 OCR 经常漏识别排名数字，所以用"股票名+热度值"锚点定位，排名按锚点出现顺序推算。
- **两步保存**：上传返回识别结果供前端编辑确认，确认后才写入数据库，保证 OCR 数据准确性。
- **SQLite 零配置**：`data/stock.db` 自动创建，无 ORM，原生参数化 SQL。schema 迁移靠 `_init_db()` 里的 `ALTER TABLE`。
- **数据双入口**：OCR 手动上传和 API 自动爬取都可以写入数据，`upsert_records` 处理冲突。
- **Admin vs Preview 角色**：纯 URL 路径区分，无认证。`/admin` 可上传和录入数据，`/preview` 只读。
- **红涨绿跌**：中国市场惯例，CSS 和图表配色一致使用。
- **COALESCE 保护**：所有 upsert 操作用 `COALESCE(excluded.field, existing.field)` 确保新数据为 NULL 时不覆盖已有值，特别用于股池信号同步不覆盖跟踪数据。
- **排除 ST 股**：数据库 `_is_st()` 方法和爬虫层面都过滤 ST 和非 A 股。
- **异步后台任务**：股池更新等耗时操作通过 `run_in_executor` 后台执行，前端轮询状态端点获取进度。

### 交易决策模块（trading，Phase 1）

- **`backend/trading/`** — 独立业务模块，通过 APIRouter 注册到 main.py（仅 `include_router` 一行），不向现有路由堆叠。
  - `router.py` — `/api/trading/*` 路由（股票池导入/查询、数据健康、数据任务）。
  - `repository.py` — 仅操作 `trade_*` 表，不向现有 `Database` 类堆叠 SQL。每次操作短连接 + try/finally close。
  - `migrations.py` — 独立迁移系统（显式 `trade_migrations` 版本表，幂等）。
  - `domain.py` — 领域数据类（`DailyBar`/`Instrument` 等），股票代码统一 `000001.SZ` 形式。
  - `clock.py` / `errors.py` / `schemas.py` — 交易日抽象、领域错误码、Pydantic 模型。
  - `providers/` — 行情适配：`eastmoney`(主源)、`akshare_provider`(备用,延迟加载)、`composite`(主备切换/重试/熔断)。
  - `services/` — `pool_service`(股票池)、`market_data_service`(行情+数据质量门禁)。
- **`trade_*` 表** — 股池版本/明细、日线行情、数据问题、任务，与现有表物理隔离。
- **Phase 1 范围**：骨架 + Provider + 股票池 + 数据健康 + 前端 Tab。
- **Phase 2 范围（已交付）**：账户/持仓/成交 CRUD + T+1 + 净值快照（peak/drawdown）+ 仓位计算纯函数 + 账户级风险限制。`position_sizing.py`/`account_service.py`/`execution_service.py`/`portfolio_service.py`。
- **Phase 3 范围（已交付）**：策略/计划引擎。`strategies/`(market_regime/scoring/entry_rules/exit_rules 纯函数)、`indicator_service`(MA/ATR 纯 Python 防未来函数)、`strategy_service`(版本+激活状态机)、`plan_service`(10 步生成+状态机+幂等键)。
- **Phase 4 范围（已交付）**：前端 7 个二级子页（TradingDashboard/DailyPlanPanel/StockPoolPanel/AccountPanel+PortfolioPanel/StrategySettingsPanel/DataHealthPanel，回测占位 P5）。`useFormat` 统一格式化。
- **Phase 5 范围（已交付）**：回测引擎（§14 防未来函数/保守止损/费用/指标）+ 复盘统计 + 激活门禁真校验（§14.4）+ Scheduler（纯 Python 循环）+ Docker/Nginx 模板 + `/api/health` + SQLite online backup。
- 不做（后续）：结构化日志/Provider 指标、分组表现、周备份、实际部署/恢复演练。

## 龙虎榜股池追踪

信号股产生后自动进入股池追踪系统：
1. 每次爬取龙虎榜时，信号股通过 `sync_lhb_pool_signals()` 同步到 `lhb_pool` 表（仅更新名称/价格/板块，不覆盖跟踪数据）
2. 股池更新通过 `POST /api/lhb/pool/update` 触发，后台异步执行，前端轮询 `GET /api/lhb/pool/status` 获取进度
3. `update_lhb_pool()` 获取未完成跟踪的记录（tracking_days < 30），从东方财富 API 拉取最新价格计算 d1-d30 涨跌幅
4. 股池查询按 `stock_code` 合并去重，只展示近 30 天内有上榜的股票，排除 ST 股

## 龙虎榜信号股规则

### 境外机构（第一梯队）
识别以下5家营业部的买卖操作：
- 国泰海通证券总部
- 中信证券上海分公司
- 瑞银证券上海花园石桥路
- 摩根大通证券（中国）上海银城中路
- 高盛（中国）证券上海浦东新区世纪大道

### 机构密集
买方+卖方共10个席位中，6个及以上为"机构专用"席位。

## Environment Variables

复制 `.env.example` 为 `.env`：

```
BAIDU_OCR_API_KEY=xxx
BAIDU_OCR_SECRET_KEY=xxx
```

## Testing

- 框架：pytest + pytest-asyncio
- API 测试用 `httpx.AsyncClient` + `ASGITransport`（无需启动服务器），`monkeypatch` 替换模块级 `db`
- 数据库和 API 测试各自用独立测试 DB（`data/test_*.db`）
- OCR 解析测试直接调用 `parse_ocr_text()`，不调百度 API

## Utility Scripts

- **`seed_data.py`** — 生成模拟数据填充数据库（开发测试用）
- **`backup.py`** — 数据库备份脚本，配合 `setup_backup_task.bat` 做定时备份
- **`start.bat`** / **`start.sh`** — 一键启动脚本（安装依赖 + 启动后端）

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/upload` | 上传图片 → OCR 识别 → 返回结构化 JSON |
| POST | `/api/records` | 确认保存识别结果 |
| GET | `/api/records?date=` | 查询单日记录 |
| GET | `/api/records/range?start=&end=` | 查询日期范围 |
| GET | `/api/stats/daily?date=` | 日报统计（含前一交易日对比） |
| GET | `/api/stats/weekly?end_date=` | 周报（往前7天） |
| GET | `/api/stats/monthly?end_date=` | 月报（往前30天） |
| GET | `/api/dates` | 所有有数据的日期列表 |
| POST/GET | `/api/season-stats` | 赛季每日盈亏数据（批量保存 / 按范围查询） |
| GET | `/api/season-stats/dates` | 赛季数据所有日期列表 |
| GET/POST/PUT | `/api/seasons` | 赛季 CRUD（列表 / 创建 / 更新） |
| POST | `/api/crawl` | 手动触发顽主杯爬虫 |
| POST | `/api/crawl-lhb?date=` | 手动触发龙虎榜爬虫（可选指定日期） |
| POST | `/api/crawl-lhb-batch?start_date=&end_date=` | 批量抓取龙虎榜 |
| GET | `/api/lhb/signals?date=` | 查询龙虎榜信号股（境外机构/机构密集） |
| GET | `/api/lhb/signal-dates` | 信号股所有日期列表 |
| GET | `/api/lhb/trading-desk?date=&stock_code=` | 指定股票买卖营业部明细 |
| GET | `/api/lhb/analysis?months=3` | 龙虎榜板块分析（按月统计） |
| GET | `/api/stats/streak?days=&min_streak=` | 热榜连续上榜统计（连续上榜天数、黑马追踪） |
| GET | `/api/stocks/{code}/history` | 个股热榜历史 + 龙虎榜关联数据 |
| GET | `/api/reports/daily?date=` | 每日复盘报告（Top3变动+连续上榜+信号+板块） |
| GET | `/api/lhb/backtest?signal_type=&months=&group_by=` | 信号股胜率回测（按月/按板块） |
| GET | `/api/lhb/pool?signal_type=` | 股池查询（近30天上榜，按 stock_code 合并去重） |
| GET | `/api/lhb/pool/status` | 股池后台更新状态 |
| POST | `/api/lhb/pool/update` | 触发股池数据更新（后台异步，轮询 status 查进度） |
| GET | `/api/ai/daily-review?date=` | 获取 AI 每日复盘（从数据库读） |
| GET | `/api/ai/stock-pick?date=` | 获取智能选股结果（从数据库读） |
| GET | `/api/ai/signal-diagnosis?period=` | 获取信号有效性诊断（从数据库读） |
| POST | `/api/ai/import` | 导入 Hermes 生成的分析结果（请求体含 analysis_type/date/content） |
| GET | `/api/ai/history?analysis_type=&days=` | AI 分析历史记录 |

> AI 分析采用"离线生成 + 在线读取"模式：`export_ai_data.py` 导出数据，由 Hermes（或 LLM）生成分析后通过 `POST /api/ai/import` 写入数据库；前端只通过 GET 读取。在线生成路径（ai_engine.py / llm_client.py）已实现但未接入路由。

### 交易决策 API（Phase 1-5）

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/trading/stock-pools/import` | 导入股票池（文本/CSV，自动去重+版本化） |
| GET | `/api/trading/stock-pools?pool_name=` | 股票池版本列表 |
| GET | `/api/trading/stock-pools/{version_id}` | 查询股票池版本明细 |
| GET | `/api/trading/data-health?trade_date=` | 数据质量门禁报告（OK/PARTIAL/BLOCKED） |
| POST | `/api/trading/data-jobs` | 创建数据任务（202，Phase 1 仅记录） |
| GET | `/api/trading/data-jobs/{job_id}` | 查询数据任务状态 |

**Phase 2（账户/持仓/成交/净值）：**

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/trading/accounts` | 创建账户（含风险配置） |
| GET | `/api/trading/accounts?active_only=` | 账户列表 |
| GET | `/api/trading/accounts/{id}` | 账户详情 |
| PUT | `/api/trading/accounts/{id}` | 更新风险配置（initial_equity 不可改） |
| GET | `/api/trading/positions?account_id=` | 持仓列表 |
| PUT | `/api/trading/positions/{stock_code}` | 人工校正持仓 |
| POST | `/api/trading/executions` | 录入成交（原子更新 + client_execution_id 幂等） |
| GET | `/api/trading/executions?account_id=&start=&end=` | 成交记录 |
| GET | `/api/trading/equity-snapshots/{account_id}?trade_date=` | 净值快照（peak/drawdown） |

**Phase 3（策略/计划）：**

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/trading/strategies` | 策略版本列表 |
| POST | `/api/trading/strategies` | 创建草稿版本 |
| POST | `/api/trading/strategies/{id}/activate` | 激活策略（门禁 stub） |
| POST | `/api/trading/plan-runs` | 创建/复用计划（幂等键） |
| GET | `/api/trading/plan-runs/{id}` | 计划详情 |
| POST | `/api/trading/plan-runs/{id}/publish` | 发布计划 |

**Phase 5（回测/复盘）：**

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/trading/backtests` | 运行回测 |
| GET | `/api/trading/backtests/{id}` | 回测结果 |
| GET | `/api/trading/reviews/summary?account_id=&period=` | 复盘摘要 |
