# Stock Analysis Hub - 设计文档

## 概述

一个个人使用的股票热榜数据分析工具。每天通过 Web 上传微信小程序比赛的股票热榜截图（Top 10），使用 AI 视觉模型提取结构化数据，存储后生成日报/周报/月报看板。

## 技术栈

- **后端**: Python (FastAPI)
- **数据库**: SQLite（零配置）
- **前端**: HTML + JavaScript + ECharts
- **图片识别**: 百度 OCR API + 正则规则解析

## 数据模型

### stock_records 表

```sql
CREATE TABLE stock_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,              -- 采集日期 YYYY-MM-DD
    rank INTEGER NOT NULL,           -- 排名 1-10
    stock_name TEXT NOT NULL,        -- 股票名称
    stock_code TEXT NOT NULL,        -- 股票代码
    heat_value REAL,                 -- 热度值(万)
    sector_tags TEXT,                -- 板块概念标签 JSON array
    price_change_pct REAL,           -- 涨跌幅(%)
    turnover_amount REAL,            -- 成交额(亿)
    holders_today INTEGER,           -- 今日持仓人数
    holders_yesterday INTEGER,       -- 昨日持仓人数
    price_action TEXT,               -- 走势描述(原文)
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, stock_code)
);
```

## 项目结构

```
stock-analysis-hub/
├── backend/
│   ├── main.py              # FastAPI 入口
│   ├── ocr.py               # 图片识别 + 数据解析
│   ├── database.py          # SQLite 操作
│   ├── models.py            # Pydantic 模型
│   └── config.py            # 配置
├── frontend/
│   ├── index.html           # 单页应用
│   ├── app.js               # 前端逻辑
│   └── style.css            # 样式
├── data/                    # SQLite 文件
├── uploads/                 # 上传图片
└── requirements.txt
```

## 核心流程

1. 用户在 Web 页面上传图片
2. 后端接收图片 → AI 视觉模型识别 → 返回结构化 JSON
3. 展示解析结果供用户确认/修正
4. 确认后存入 SQLite
5. 前端刷新展示当天数据和历史看板

## 看板设计

### 日报（1天数据即可）
- 热榜 Top10 卡片列表（排名、名称、涨跌幅、热度、持仓变化）
- 涨跌幅横向柱状图（红涨绿跌）
- 持仓人数今日 vs 昨日对比柱状图

### 周报（>=7天数据后可用）
- 板块热力图（概念板块出现频次）
- 个股上榜频次排行
- 热度趋势 7 天折线图

### 月报（>=30天数据后可用）
- 月度综合看板
- 个股持仓人数长线趋势
- 板块轮动图

## OCR 识别策略

使用百度 OCR 高精度版提取图片文字，再用正则规则解析为结构化数据：

**OCR 调用**: 百度通用文字识别（高精度版）API，获取文字块列表。

**解析规则**:
- 排名：按数字分割文本块
- 股票名称+热度值：`中文名 数字w` 模式
- 股票代码：6位数字
- 涨跌幅：`上涨/下跌 X%` 模式
- 成交额：`成交/爆量 X亿` 模式
- 持仓人数：`X人持仓` 模式
- 昨日持仓：`昨日：X` 模式
- 板块概念：从已知关键词列表匹配

**容错**: 解析结果展示给用户确认，支持手动编辑修正。保留原始图片用于回溯。

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/upload | 上传图片并识别 |
| POST | /api/records | 确认并保存记录 |
| GET | /api/records?date= | 查询某日记录 |
| GET | /api/records/range?start=&end= | 查询日期范围 |
| GET | /api/stats/daily | 日报统计数据 |
| GET | /api/stats/weekly | 周报统计数据 |
| GET | /api/stats/monthly | 月报统计数据 |
| GET | /api/dates | 获取所有有数据的日期列表 |
