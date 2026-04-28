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

### Backend (FastAPI, Python)

- **`backend/main.py`** — FastAPI 应用入口，所有 API 路由（14 个端点）。模块级 `db` 实例，测试通过 `monkeypatch` 替换。静态文件服务优先从 `frontend/dist/`（构建产物），fallback 到 `frontend/`（开发模式）。
- **`backend/ocr.py`** — 核心解析逻辑。`ocr_image()` 调百度 OCR API（httpx 同步）；`parse_ocr_text()` 用"中文名+热度值(w)"锚点定位，不依赖排名数字。`KNOWN_SECTORS` 硬编码板块关键词列表。
- **`backend/database.py`** — SQLite 封装，三张表：`stock_records`（主数据，`UNIQUE(date, stock_code)`）、`season_daily_stats`（赛季每日盈亏，`ON CONFLICT DO UPDATE`）、`seasons`（命名时间范围）。每次操作新建连接（`_get_conn`）。`_init_db()` 含 `ALTER TABLE` 做字段迁移。
- **`backend/models.py`** — Pydantic v2 模型。`StockRecord`（13 字段，stock_code 必须正好 6 位数字）、`UploadResult`。
- **`backend/config.py`** — `pydantic-settings` 从 `.env` 加载，路径默认相对于项目根目录。

### Frontend (Vue 3 + TypeScript + Vite)

`frontend_legacy/` 保留了旧版 vanilla JS 前端作为备份，当前使用 `frontend/` 目录。

- **路由**：`/preview`（只读）和 `/admin`（含上传功能），通过 Vue Router meta + provide/inject 传递 `isAdmin`。
- **三个主视图**：`DailyView`（日报 Top10 卡片 + 涨跌幅/持价新增图表）、`PnlView`（赛季盈亏走势 + 仓位百分比，admin 可手动录入）、`RangeView`（跨日分析：板块热力图、个股频率、热度趋势、持仓趋势、综合热力网格）。
- **组件**：`StockCard`（排名徽章，涨红跌绿）、`UploadArea`（拖拽上传）、`ConfirmModal`（OCR 结果可编辑表格）、`ChartBox`（通用 ECharts 容器）、`TimeFilter`、`EmptyState`。
- **Composables**：`useApi`（类型化 fetch）、`useChart`（ECharts 生命周期：init/setOption/resize/dispose）。
- **Charts**（`frontend/src/charts/`）：四个图表模块，共享暗色主题（`theme.ts`），自定义调色板和 tooltip 样式。
- **CSS**：暗色主题 + 玻璃态导航栏，金银铜排名徽章，红涨绿跌（中国市场惯例），响应式断点 900px/600px。

### Key Design Decisions

- **OCR 锚点模式**：百度 OCR 经常漏识别排名数字，所以用"股票名+热度值"锚点定位，排名按锚点出现顺序推算。
- **两步保存**：上传返回识别结果供前端编辑确认，确认后才写入数据库，保证 OCR 数据准确性。
- **SQLite 零配置**：`data/stock.db` 自动创建，无 ORM，原生参数化 SQL。schema 迁移靠 `_init_db()` 里的 `ALTER TABLE`。
- **Admin vs Preview 角色**：纯 URL 路径区分，无认证。`/admin` 可上传和录入数据，`/preview` 只读。
- **红涨绿跌**：中国市场惯例，CSS 和图表配色一致使用。

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
| POST/GET | `/api/seasons/...` | 赛季 CRUD |
| POST/GET | `/api/season-daily-stats/...` | 赛季每日盈亏数据 |
