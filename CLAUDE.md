# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

股票热榜分析工具 — 个人使用的股票热榜 Top10 数据采集与可视化看板。用户上传微信小程序股票热榜截图，通过百度 OCR 识别提取结构化数据，存储到 SQLite 后生成日报/周报/月报。

## Common Commands

```bash
# 安装依赖
pip install -r requirements.txt

# 启动开发服务器（带热重载）
python run.py
# 等效: uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

# 运行全部测试
pytest

# 运行单个测试文件
pytest tests/test_ocr_parser.py

# 运行单个测试函数
pytest tests/test_ocr_parser.py::test_parse_full_text

# 运行测试并显示详细输出
pytest -v
```

## Architecture

### Data Flow

`图片上传` → `POST /api/upload` → `百度 OCR API` → `parse_ocr_text()` 正则解析 → `前端确认/编辑 Modal` → `POST /api/records` → `SQLite`

### Backend (FastAPI)

- **`backend/main.py`** — FastAPI 应用入口，所有 API 路由。数据库实例 `db` 在模块级创建。测试通过 `monkeypatch` 替换为测试 DB。
- **`backend/ocr.py`** — 核心解析逻辑。`ocr_image()` 调百度 OCR API 获取文字；`parse_ocr_text()` 用"中文名+热度值(w)"作为锚点定位每只股票并提取字段。`KNOWN_SECTORS` 列表用于板块标签匹配。
- **`backend/database.py`** — SQLite 封装。每次操作新建连接（`_get_conn`），表有 `UNIQUE(date, stock_code)` 约束。
- **`backend/models.py`** — Pydantic v2 模型。`StockRecord` 用于数据验证，`UploadResult` 用于保存接口入参。
- **`backend/config.py`** — `pydantic-settings` 从 `.env` 加载配置。

### Frontend (Vanilla JS + ECharts)

- **`frontend/index.html`** — 单页应用，三个视图（日报/周报/月报）通过 tab 切换。
- **`frontend/app.js`** — 纯 JS 无框架，用 ECharts 渲染图表。`loadView()` 根据 `currentView` 调用对应 API 和渲染函数。
- **`frontend/style.css`** — 暗色主题，CSS 变量定义在 `:root`。

### Key Design Decisions

- **OCR 解析用锚点模式而非排名数字**：百度 OCR 经常漏识别排名数字，所以用"股票名+热度值"作为锚点定位，排名按锚点出现顺序推算。
- **两步保存**：上传返回识别结果供前端编辑确认，确认后才写入数据库，保证数据准确性。
- **SQLite 零配置**：个人工具不需要数据库服务器，`data/stock.db` 自动创建。

## Environment Variables

复制 `.env.example` 为 `.env`，填入百度 OCR 密钥：

```
BAIDU_OCR_API_KEY=xxx
BAIDU_OCR_SECRET_KEY=xxx
```

## Testing

- 测试框架：pytest + pytest-asyncio
- API 测试使用 `httpx.AsyncClient` + `ASGITransport`（无需启动服务器）
- 数据库测试和 API 测试各自创建独立的测试 DB 文件（`data/test_*.db`）
- OCR 解析测试用 `parse_ocr_text()` 直接测试，不调用百度 API

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/upload` | 上传图片 → OCR 识别 → 返回结构化 JSON |
| POST | `/api/records` | 确认保存识别结果 |
| GET | `/api/records?date=` | 查询单日记录 |
| GET | `/api/records/range?start=&end=` | 查询日期范围 |
| GET | `/api/stats/daily?date=` | 日报统计 |
| GET | `/api/stats/weekly?end_date=` | 周报（往前7天） |
| GET | `/api/stats/monthly?end_date=` | 月报（往前30天） |
| GET | `/api/dates` | 所有有数据的日期列表 |
