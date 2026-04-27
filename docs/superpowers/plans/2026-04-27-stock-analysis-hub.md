# Stock Analysis Hub 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个 Web 应用，每天上传股票热榜截图，百度 OCR 提取文字 + 正则解析数据，生成日报/周报/月报看板。

**Architecture:** FastAPI 后端提供 REST API，SQLite 存储数据，前端单页应用使用 ECharts 图表。图片通过百度 OCR API 识别文字，再用正则规则解析为结构化数据。

**Tech Stack:** Python 3.10+, FastAPI, SQLite, ECharts, 百度 OCR API

---

## File Structure

```
stock-analysis-hub/
├── backend/
│   ├── __init__.py
│   ├── main.py              # FastAPI 入口 + 路由
│   ├── ocr.py               # 百度 OCR 调用 + 正则解析
│   ├── database.py          # SQLite CRUD
│   ├── models.py            # Pydantic 模型
│   └── config.py            # 配置（环境变量）
├── frontend/
│   ├── index.html           # SPA 入口
│   ├── app.js               # 前端逻辑
│   └── style.css            # 样式
├── tests/
│   ├── test_ocr_parser.py   # OCR 解析测试
│   ├── test_database.py     # 数据库测试
│   └── test_api.py          # API 端点测试
├── data/                    # SQLite 文件（gitignore）
├── uploads/                 # 上传图片（gitignore）
├── requirements.txt
├── run.py                   # 启动入口
└── .gitignore
```

---

### Task 1: 项目骨架搭建

**Files:**
- Create: `requirements.txt`
- Create: `run.py`
- Create: `.gitignore`
- Create: `backend/__init__.py`
- Create: `backend/config.py`
- Create: `data/.gitkeep`
- Create: `uploads/.gitkeep`

- [ ] **Step 1: 创建 requirements.txt**

```txt
fastapi==0.115.0
uvicorn==0.30.6
python-multipart==0.0.9
httpx==0.27.2
pydantic==2.9.2
pydantic-settings==2.5.2
pytest==8.3.3
```

- [ ] **Step 2: 创建 run.py**

```python
import uvicorn

if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
```

- [ ] **Step 3: 创建 .gitignore**

```
__pycache__/
*.pyc
data/*.db
uploads/*.png
uploads/*.jpg
uploads/*.jpeg
.env
```

- [ ] **Step 4: 创建 backend/__init__.py**（空文件）

- [ ] **Step 5: 创建 backend/config.py**

```python
from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    db_path: str = str(Path(__file__).parent.parent / "data" / "stock.db")
    upload_dir: str = str(Path(__file__).parent.parent / "uploads")

    # 百度 OCR
    baidu_ocr_api_key: str = ""
    baidu_ocr_secret_key: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
```

- [ ] **Step 6: 创建 data/.gitkeep 和 uploads/.gitkeep**（空文件）

- [ ] **Step 7: 安装依赖**

Run: `cd D:/claude-project/stock-analysis-hub && pip install -r requirements.txt`
Expected: Successfully installed

- [ ] **Step 8: Commit**

```bash
git init
git add .
git commit -m "feat: project skeleton with dependencies and config"
```

---

### Task 2: 数据模型

**Files:**
- Create: `backend/models.py`

- [ ] **Step 1: 创建 backend/models.py**

```python
from pydantic import BaseModel, Field
from typing import Optional


class StockRecord(BaseModel):
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    rank: int = Field(..., ge=1, le=10)
    stock_name: str
    stock_code: str = Field(..., pattern=r"^\d{6}$")
    heat_value: Optional[float] = None
    sector_tags: list[str] = Field(default_factory=list)
    price_change_pct: Optional[float] = None
    turnover_amount: Optional[float] = None
    holders_today: Optional[int] = None
    holders_yesterday: Optional[int] = None
    price_action: Optional[str] = None


class StockRecordResponse(StockRecord):
    id: int
    created_at: Optional[str] = None


class UploadResult(BaseModel):
    date: str
    records: list[StockRecord]


class DateInfo(BaseModel):
    dates: list[str]
```

- [ ] **Step 2: Commit**

```bash
git add backend/models.py
git commit -m "feat: add Pydantic data models for stock records"
```

---

### Task 3: 数据库层

**Files:**
- Create: `backend/database.py`
- Create: `tests/test_database.py`

- [ ] **Step 1: 写测试 tests/test_database.py**

```python
import os
import pytest
from backend.database import Database

TEST_DB = "data/test_stock.db"


@pytest.fixture
def db():
    os.makedirs("data", exist_ok=True)
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    database = Database(TEST_DB)
    yield database
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


def test_init_creates_table(db):
    result = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    assert ("stock_records",) in result


def test_insert_and_query_record(db):
    record = {
        "date": "2026-04-27",
        "rank": 1,
        "stock_name": "圣阳股份",
        "stock_code": "002580",
        "heat_value": 1358.33,
        "sector_tags": '["液冷储能"]',
        "price_change_pct": 4.0,
        "turnover_amount": 44.0,
        "holders_today": 42,
        "holders_yesterday": 35,
        "price_action": "上午震荡回落-下午大幅冲高回落",
    }
    db.insert_record(record)
    rows = db.query_by_date("2026-04-27")
    assert len(rows) == 1
    assert rows[0]["stock_name"] == "圣阳股份"
    assert rows[0]["stock_code"] == "002580"
    assert rows[0]["heat_value"] == 1358.33


def test_unique_constraint(db):
    record = {
        "date": "2026-04-27",
        "rank": 1,
        "stock_name": "圣阳股份",
        "stock_code": "002580",
        "heat_value": 1358.33,
        "sector_tags": '["液冷储能"]',
        "price_change_pct": 4.0,
        "turnover_amount": 44.0,
        "holders_today": 42,
        "holders_yesterday": 35,
        "price_action": "上午震荡回落",
    }
    db.insert_record(record)
    with pytest.raises(Exception):
        db.insert_record(record)


def test_query_date_range(db):
    for date in ["2026-04-25", "2026-04-26", "2026-04-27"]:
        db.insert_record({
            "date": date, "rank": 1, "stock_name": "测试",
            "stock_code": "000001", "heat_value": 100.0,
            "sector_tags": '[]', "price_change_pct": 1.0,
            "turnover_amount": 10.0, "holders_today": 10,
            "holders_yesterday": 8, "price_action": "",
        })
    rows = db.query_date_range("2026-04-25", "2026-04-26")
    assert len(rows) == 2


def test_get_all_dates(db):
    for date in ["2026-04-25", "2026-04-27"]:
        db.insert_record({
            "date": date, "rank": 1, "stock_name": "测试",
            "stock_code": "000001", "heat_value": 100.0,
            "sector_tags": '[]', "price_change_pct": 1.0,
            "turnover_amount": 10.0, "holders_today": 10,
            "holders_yesterday": 8, "price_action": "",
        })
    dates = db.get_all_dates()
    assert "2026-04-25" in dates
    assert "2026-04-27" in dates
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd D:/claude-project/stock-analysis-hub && python -m pytest tests/test_database.py -v`
Expected: FAIL - ModuleNotFoundError

- [ ] **Step 3: 实现 backend/database.py**

```python
import sqlite3
import json
from pathlib import Path
from backend.config import settings


class Database:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or settings.db_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS stock_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    rank INTEGER NOT NULL,
                    stock_name TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    heat_value REAL,
                    sector_tags TEXT,
                    price_change_pct REAL,
                    turnover_amount REAL,
                    holders_today INTEGER,
                    holders_yesterday INTEGER,
                    price_action TEXT,
                    created_at TEXT DEFAULT (datetime('now','localtime')),
                    UNIQUE(date, stock_code)
                )
            """)

    def insert_record(self, record: dict):
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO stock_records
                    (date, rank, stock_name, stock_code, heat_value,
                     sector_tags, price_change_pct, turnover_amount,
                     holders_today, holders_yesterday, price_action)
                VALUES
                    (:date, :rank, :stock_name, :stock_code, :heat_value,
                     :sector_tags, :price_change_pct, :turnover_amount,
                     :holders_today, :holders_yesterday, :price_action)
            """, record)

    def insert_records(self, records: list[dict]):
        with self._get_conn() as conn:
            conn.executemany("""
                INSERT INTO stock_records
                    (date, rank, stock_name, stock_code, heat_value,
                     sector_tags, price_change_pct, turnover_amount,
                     holders_today, holders_yesterday, price_action)
                VALUES
                    (:date, :rank, :stock_name, :stock_code, :heat_value,
                     :sector_tags, :price_change_pct, :turnover_amount,
                     :holders_today, :holders_yesterday, :price_action)
            """, records)

    def query_by_date(self, date: str) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM stock_records WHERE date = ? ORDER BY rank",
                (date,)
            ).fetchall()
            return [dict(r) for r in rows]

    def query_date_range(self, start: str, end: str) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM stock_records WHERE date BETWEEN ? AND ? ORDER BY date, rank",
                (start, end)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_all_dates(self) -> list[str]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT date FROM stock_records ORDER BY date DESC"
            ).fetchall()
            return [r["date"] for r in rows]

    def execute(self, sql: str, params=None):
        with self._get_conn() as conn:
            return conn.execute(sql, params or [])
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd D:/claude-project/stock-analysis-hub && python -m pytest tests/test_database.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add backend/database.py tests/test_database.py
git commit -m "feat: add SQLite database layer with tests"
```

---

### Task 4: 百度 OCR + 正则解析模块

**Files:**
- Create: `backend/ocr.py`
- Create: `tests/test_ocr_parser.py`

- [ ] **Step 1: 写解析器测试 tests/test_ocr_parser.py**

```python
import json
from backend.ocr import parse_ocr_text


def test_parse_full_text():
    """模拟百度 OCR 返回的文字，验证解析结果。"""
    mock_text = """
1
圣阳股份 1358.33w
002580 均内情指标-液冷储能-上午震荡回落-下午大幅冲高回落-收盘上涨4%-成交44亿
42人持仓
昨日：35

2
深圳华强 357.73w
000062 华为系-芯片分销龙头-今日二连板-上涨4天6板-盘中快速封-成交39亿
42人持仓
昨日：11
"""
    result = parse_ocr_text(mock_text, "2026-04-27")
    assert len(result) == 2

    r1 = result[0]
    assert r1["rank"] == 1
    assert r1["stock_name"] == "圣阳股份"
    assert r1["stock_code"] == "002580"
    assert r1["heat_value"] == 1358.33
    assert r1["holders_today"] == 42
    assert r1["holders_yesterday"] == 35
    assert r1["price_change_pct"] == 4.0
    assert r1["turnover_amount"] == 44.0
    assert "液冷储能" in r1["sector_tags"]

    r2 = result[1]
    assert r2["rank"] == 2
    assert r2["stock_name"] == "深圳华强"
    assert r2["stock_code"] == "000062"


def test_parse_negative_change():
    """测试下跌股票的涨跌幅解析。"""
    mock_text = """
5
博云新材 393.72w
002297 商业航天-硬质合金-上周五高位震荡-今日跌停被撬板-收盘下跌7.9%-成交33亿
27人持仓
昨日：23
"""
    result = parse_ocr_text(mock_text, "2026-04-27")
    assert len(result) == 1
    assert result[0]["price_change_pct"] == -7.9
    assert result[0]["turnover_amount"] == 33.0


def test_parse_slight_change():
    """测试微涨微跌的解析。"""
    mock_text = """
4
综艺股份 125.57w
600770 国产芯片-参股神州龙芯-今日三连板炸板跳水-收盘微跌-成交19亿
28人持仓
昨日：5
"""
    result = parse_ocr_text(mock_text, "2026-04-27")
    assert len(result) == 1
    assert result[0]["price_change_pct"] == -0.1


def test_parse_empty_text():
    """测试空文本返回空列表。"""
    result = parse_ocr_text("", "2026-04-27")
    assert result == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd D:/claude-project/stock-analysis-hub && python -m pytest tests/test_ocr_parser.py -v`
Expected: FAIL - ModuleNotFoundError

- [ ] **Step 3: 实现 backend/ocr.py**

```python
import re
import base64
import httpx
from pathlib import Path
from datetime import date
from backend.config import settings


# 百度 OCR API
TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
OCR_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic"

# 已知板块概念关键词（可扩展）
KNOWN_SECTORS = [
    "液冷储能", "商业航天", "光模块", "华为系", "芯片", "国产芯片",
    "医药", "SpaceX", "江苏板块", "光通信", "人工智能", "新能源",
    "半导体", "军工", "消费电子", "汽车", "光伏", "锂电",
    "机器人", "算力", "数据中心", "网络安全", "区块链",
]


def _get_access_token() -> str:
    """获取百度 OCR access_token。"""
    resp = httpx.post(TOKEN_URL, params={
        "grant_type": "client_credentials",
        "client_id": settings.baidu_ocr_api_key,
        "client_secret": settings.baidu_ocr_secret_key,
    })
    return resp.json()["access_token"]


def ocr_image(image_path: str) -> str:
    """调用百度 OCR 识别图片，返回全部文字拼接结果。"""
    token = _get_access_token()
    image_data = base64.b64encode(Path(image_path).read_bytes()).decode()

    resp = httpx.post(
        OCR_URL,
        params={"access_token": token},
        data={"image": image_data},
    )
    resp.raise_for_status()
    result = resp.json()

    words = [item["words"] for item in result.get("words_result", [])]
    return "\n".join(words)


def parse_ocr_text(text: str, record_date: str) -> list[dict]:
    """将 OCR 文本解析为结构化股票记录列表。"""
    if not text.strip():
        return []

    # 按排名数字分割为块
    blocks = re.split(r'\n\s*(?=(?:10|[1-9])\s*\n)', text)
    records = []

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        record = {
            "date": record_date,
            "rank": None,
            "stock_name": "",
            "stock_code": "",
            "heat_value": None,
            "sector_tags": [],
            "price_change_pct": None,
            "turnover_amount": None,
            "holders_today": None,
            "holders_yesterday": None,
            "price_action": "",
        }

        # 排名
        rank_match = re.match(r'^(\d+)\s*$', block.split('\n')[0].strip())
        if rank_match:
            record["rank"] = int(rank_match.group(1))
        else:
            continue

        full_text = block

        # 股票名称 + 热度值
        heat_match = re.search(r'([\u4e00-\u9fa5]+)\s+([\d.]+)w', full_text)
        if heat_match:
            record["stock_name"] = heat_match.group(1)
            record["heat_value"] = float(heat_match.group(2))

        # 股票代码
        code_match = re.search(r'(?<!\d)(\d{6})(?!\d)', full_text)
        if code_match:
            record["stock_code"] = code_match.group(1)

        # 涨跌幅
        pct_match = re.search(r'上涨([\d.]+)%', full_text)
        if pct_match:
            record["price_change_pct"] = float(pct_match.group(1))
        else:
            pct_match = re.search(r'下跌([\d.]+)%', full_text)
            if pct_match:
                record["price_change_pct"] = -float(pct_match.group(1))
            elif '微跌' in full_text:
                record["price_change_pct"] = -0.1
            elif '大涨' in full_text:
                match = re.search(r'大涨([\d.]+)%', full_text)
                record["price_change_pct"] = float(match.group(1)) if match else 0.1

        # 成交额
        vol_match = re.search(r'(?:成交|爆量)([\d.]+)亿', full_text)
        if vol_match:
            record["turnover_amount"] = float(vol_match.group(1))

        # 今日持仓人数
        holders_match = re.search(r'(\d+)人持仓', full_text)
        if holders_match:
            record["holders_today"] = int(holders_match.group(1))

        # 昨日持仓人数
        yesterday_match = re.search(r'昨日[：:]\s*(\d+)', full_text)
        if yesterday_match:
            record["holders_yesterday"] = int(yesterday_match.group(1))

        # 板块概念标签
        for sector in KNOWN_SECTORS:
            if sector in full_text and sector not in record["sector_tags"]:
                record["sector_tags"].append(sector)

        # 走势描述：提取代码后面的描述部分
        desc_match = re.search(r'\d{6}\s*(.*)', full_text)
        if desc_match:
            desc = desc_match.group(1)
            # 去掉持仓相关部分
            desc = re.split(r'\d+人持仓', desc)[0].strip()
            record["price_action"] = desc

        records.append(record)

    # 按排名排序
    records.sort(key=lambda r: r["rank"] or 99)
    return records


async def analyze_image(image_path: str) -> dict:
    """完整的图片识别流程：OCR → 解析。"""
    text = ocr_image(image_path)
    today = date.today().isoformat()
    records = parse_ocr_text(text, today)
    return {"date": today, "records": records}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd D:/claude-project/stock-analysis-hub && python -m pytest tests/test_ocr_parser.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add backend/ocr.py tests/test_ocr_parser.py
git commit -m "feat: add Baidu OCR module with regex parser and tests"
```

---

### Task 5: FastAPI 后端路由

**Files:**
- Create: `backend/main.py`
- Create: `tests/test_api.py`

- [ ] **Step 1: 写 API 测试 tests/test_api.py**

```python
import json
import os
import pytest
from httpx import AsyncClient, ASGITransport
from backend.main import app
from backend.database import Database

TEST_DB = "data/test_api.db"


@pytest.fixture(autouse=True)
def setup_test_db(monkeypatch):
    os.makedirs("data", exist_ok=True)
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    db = Database(TEST_DB)
    monkeypatch.setattr("backend.main.db", db)
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


@pytest.mark.asyncio
async def test_get_dates_empty():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dates")
    assert resp.status_code == 200
    assert resp.json()["dates"] == []


@pytest.mark.asyncio
async def test_save_and_query_records():
    payload = {
        "date": "2026-04-27",
        "records": [
            {
                "rank": 1,
                "stock_name": "圣阳股份",
                "stock_code": "002580",
                "heat_value": 1358.33,
                "sector_tags": ["液冷储能"],
                "price_change_pct": 4.0,
                "turnover_amount": 44.0,
                "holders_today": 42,
                "holders_yesterday": 35,
                "price_action": "上午震荡回落",
            }
        ]
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 保存
        resp = await ac.post("/api/records", json=payload)
        assert resp.status_code == 200

        # 查询
        resp = await ac.get("/api/records", params={"date": "2026-04-27"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["stock_name"] == "圣阳股份"

        # 日期列表
        resp = await ac.get("/api/dates")
        assert resp.status_code == 200
        assert "2026-04-27" in resp.json()["dates"]


@pytest.mark.asyncio
async def test_query_range():
    for date in ["2026-04-25", "2026-04-26"]:
        payload = {
            "date": date,
            "records": [{
                "rank": 1, "stock_name": "测试", "stock_code": "000001",
                "heat_value": 100.0, "sector_tags": [],
                "price_change_pct": 1.0, "turnover_amount": 10.0,
                "holders_today": 5, "holders_yesterday": 3, "price_action": "",
            }]
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            await ac.post("/api/records", json=payload)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/records/range", params={"start": "2026-04-25", "end": "2026-04-26"})
        assert resp.status_code == 200
        assert len(resp.json()) == 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd D:/claude-project/stock-analysis-hub && python -m pytest tests/test_api.py -v`
Expected: FAIL - ModuleNotFoundError

- [ ] **Step 3: 实现 backend/main.py**

```python
import os
import shutil
from datetime import date
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from backend.config import settings
from backend.database import Database
from backend.models import UploadResult
from backend.ocr import analyze_image
import json as json_lib

app = FastAPI(title="Stock Analysis Hub")

db = Database()

# 静态文件
frontend_dir = Path(__file__).parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(frontend_dir / "index.html"))


@app.post("/api/upload")
async def upload_image(file: UploadFile = File(...)):
    """上传图片并调用百度 OCR 识别。"""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "请上传图片文件")

    os.makedirs(settings.upload_dir, exist_ok=True)
    today = date.today().isoformat()
    file_path = os.path.join(settings.upload_dir, f"{today}_{file.filename}")

    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        result = await analyze_image(file_path)
        return result
    except Exception as e:
        raise HTTPException(500, f"图片识别失败: {str(e)}")


@app.post("/api/records")
async def save_records(data: UploadResult):
    """确认并保存识别结果到数据库。"""
    records = []
    for r in data.records:
        record = r.model_dump()
        record["date"] = data.date
        record["sector_tags"] = json_lib.dumps(
            record.get("sector_tags", []), ensure_ascii=False
        )
        records.append(record)
    try:
        db.insert_records(records)
    except Exception as e:
        raise HTTPException(400, f"保存失败（可能当日数据已存在）: {str(e)}")
    return {"status": "ok", "count": len(records)}


@app.get("/api/records")
async def get_records(date: str):
    """查询某日记录。"""
    rows = db.query_by_date(date)
    for row in rows:
        if row.get("sector_tags"):
            row["sector_tags"] = json_lib.loads(row["sector_tags"])
    return rows


@app.get("/api/records/range")
async def get_records_range(start: str, end: str):
    """查询日期范围内的记录。"""
    rows = db.query_date_range(start, end)
    for row in rows:
        if row.get("sector_tags"):
            row["sector_tags"] = json_lib.loads(row["sector_tags"])
    return rows


@app.get("/api/dates")
async def get_dates():
    """获取所有有数据的日期。"""
    return {"dates": db.get_all_dates()}


@app.get("/api/stats/daily")
async def daily_stats(date: str):
    """日报统计数据。"""
    rows = db.query_by_date(date)
    if not rows:
        raise HTTPException(404, "该日期无数据")
    for row in rows:
        if row.get("sector_tags"):
            row["sector_tags"] = json_lib.loads(row["sector_tags"])
    return {
        "date": date,
        "records": rows,
        "summary": {
            "total_stocks": len(rows),
            "avg_holders_change": sum(
                (r.get("holders_today") or 0) - (r.get("holders_yesterday") or 0)
                for r in rows
            ) / len(rows) if rows else 0,
        }
    }


@app.get("/api/stats/weekly")
async def weekly_stats(end_date: str):
    """周报统计数据（end_date 往前 7 天）。"""
    from datetime import datetime, timedelta
    end = datetime.strptime(end_date, "%Y-%m-%d")
    start = (end - timedelta(days=6)).isoformat()
    rows = db.query_date_range(start, end_date)
    for row in rows:
        if row.get("sector_tags"):
            row["sector_tags"] = json_lib.loads(row["sector_tags"])
    return {"start_date": start, "end_date": end_date, "records": rows}


@app.get("/api/stats/monthly")
async def monthly_stats(end_date: str):
    """月报统计数据（end_date 往前 30 天）。"""
    from datetime import datetime, timedelta
    end = datetime.strptime(end_date, "%Y-%m-%d")
    start = (end - timedelta(days=29)).isoformat()
    rows = db.query_date_range(start, end_date)
    for row in rows:
        if row.get("sector_tags"):
            row["sector_tags"] = json_lib.loads(row["sector_tags"])
    return {"start_date": start, "end_date": end_date, "records": rows}
```

- [ ] **Step 4: 安装测试依赖并运行**

Run: `cd D:/claude-project/stock-analysis-hub && pip install pytest pytest-asyncio && python -m pytest tests/test_api.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add backend/main.py tests/test_api.py
git commit -m "feat: add FastAPI routes with upload, query, and stats endpoints"
```

---

### Task 6: 前端 HTML + CSS

**Files:**
- Create: `frontend/index.html`
- Create: `frontend/style.css`

- [ ] **Step 1: 创建 frontend/style.css**

```css
:root {
    --bg: #0f172a;
    --card: #1e293b;
    --border: #334155;
    --text: #e2e8f0;
    --text-muted: #94a3b8;
    --primary: #3b82f6;
    --primary-hover: #2563eb;
    --red: #ef4444;
    --green: #22c55e;
    --yellow: #eab308;
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
}

/* Nav */
.nav {
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 16px 24px;
    background: var(--card);
    border-bottom: 1px solid var(--border);
}
.nav h1 { font-size: 20px; font-weight: 700; }
.nav .spacer { flex: 1; }

.btn {
    padding: 8px 16px;
    border-radius: 8px;
    border: none;
    cursor: pointer;
    font-size: 14px;
    font-weight: 500;
    transition: all 0.2s;
}
.btn-primary { background: var(--primary); color: #fff; }
.btn-primary:hover { background: var(--primary-hover); }
.btn-outline { background: transparent; color: var(--text); border: 1px solid var(--border); }
.btn-outline:hover { background: var(--border); }
.btn-outline.active { background: var(--primary); color: #fff; border-color: var(--primary); }

/* Tabs */
.tabs { display: flex; gap: 8px; }

/* Content */
.content { max-width: 1200px; margin: 0 auto; padding: 24px; }

/* Upload area */
.upload-area {
    border: 2px dashed var(--border);
    border-radius: 12px;
    padding: 40px;
    text-align: center;
    cursor: pointer;
    transition: border-color 0.2s;
    margin-bottom: 24px;
}
.upload-area:hover { border-color: var(--primary); }
.upload-area.dragover { border-color: var(--primary); background: rgba(59,130,246,0.1); }
.upload-area input { display: none; }

/* Cards grid */
.cards-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 16px;
    margin-bottom: 32px;
}

.stock-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px;
    transition: transform 0.2s;
}
.stock-card:hover { transform: translateY(-2px); }

.stock-card .rank {
    display: inline-block;
    width: 28px;
    height: 28px;
    line-height: 28px;
    text-align: center;
    border-radius: 8px;
    font-weight: 700;
    font-size: 14px;
    margin-right: 8px;
}
.rank-1 { background: var(--yellow); color: #000; }
.rank-2 { background: #94a3b8; color: #000; }
.rank-3 { background: #b45309; color: #fff; }

.stock-card .name { font-size: 16px; font-weight: 600; }
.stock-card .code { color: var(--text-muted); font-size: 13px; }
.stock-card .heat { color: var(--yellow); font-size: 13px; }

.stock-card .change {
    font-size: 18px;
    font-weight: 700;
    margin-top: 8px;
}
.change-up { color: var(--red); }
.change-down { color: var(--green); }

.stock-card .holders {
    display: flex;
    justify-content: space-between;
    margin-top: 8px;
    font-size: 13px;
    color: var(--text-muted);
}
.holders .arrow-up { color: var(--red); }
.holders .arrow-down { color: var(--green); }

.stock-card .tags {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin-top: 8px;
}
.tag {
    background: rgba(59,130,246,0.2);
    color: var(--primary);
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 12px;
}

/* Charts */
.charts-row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 24px;
    margin-bottom: 32px;
}
.chart-box {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px;
}
.chart-box h3 { margin-bottom: 12px; font-size: 15px; }
.chart-container { width: 100%; height: 350px; }

/* Modal */
.modal-overlay {
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.6);
    z-index: 100;
    justify-content: center;
    align-items: flex-start;
    padding-top: 60px;
    overflow-y: auto;
}
.modal-overlay.show { display: flex; }
.modal {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 16px;
    width: 90%;
    max-width: 900px;
    max-height: 80vh;
    overflow-y: auto;
    padding: 24px;
}
.modal h2 { margin-bottom: 16px; }
.modal .actions { display: flex; gap: 12px; justify-content: flex-end; margin-top: 16px; }

/* Edit table */
.edit-table { width: 100%; border-collapse: collapse; }
.edit-table th, .edit-table td {
    padding: 8px 12px;
    text-align: left;
    border-bottom: 1px solid var(--border);
    font-size: 13px;
}
.edit-table th { color: var(--text-muted); font-weight: 500; }
.edit-table input {
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 4px 8px;
    border-radius: 4px;
    width: 100%;
    font-size: 13px;
}

/* Date picker */
input[type="date"] {
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 8px 12px;
    border-radius: 8px;
    font-size: 14px;
}

/* Empty state */
.empty-state {
    text-align: center;
    padding: 60px 20px;
    color: var(--text-muted);
}
.empty-state p { margin-top: 8px; }

/* Section title */
.section-title {
    font-size: 16px;
    font-weight: 600;
    margin-bottom: 16px;
    display: flex;
    align-items: center;
    gap: 8px;
}

/* Responsive */
@media (max-width: 768px) {
    .charts-row { grid-template-columns: 1fr; }
    .nav { flex-wrap: wrap; }
}
```

- [ ] **Step 2: 创建 frontend/index.html**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Stock Analysis Hub</title>
    <link rel="stylesheet" href="/static/style.css">
    <script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
</head>
<body>
    <!-- Nav -->
    <nav class="nav">
        <h1>Stock Analysis Hub</h1>
        <div class="spacer"></div>
        <input type="date" id="datePicker">
        <div class="tabs">
            <button class="btn btn-outline active" data-view="daily">日报</button>
            <button class="btn btn-outline" data-view="weekly">周报</button>
            <button class="btn btn-outline" data-view="monthly">月报</button>
        </div>
        <button class="btn btn-primary" id="uploadBtn">上传图片</button>
    </nav>

    <div class="content">
        <!-- Upload Area (hidden by default) -->
        <div class="upload-area" id="uploadArea" style="display:none">
            <p>点击或拖拽图片到此处上传</p>
            <p style="color:var(--text-muted);font-size:13px;margin-top:8px">支持 PNG、JPG 格式的股票热榜截图</p>
            <input type="file" id="fileInput" accept="image/*">
        </div>

        <!-- Main content -->
        <div id="mainContent">
            <div class="empty-state" id="emptyState">
                <h2>暂无数据</h2>
                <p>点击右上角"上传图片"开始录入股票热榜数据</p>
            </div>

            <!-- Daily View -->
            <div id="dailyView" style="display:none">
                <div class="section-title">热榜 Top 10</div>
                <div class="cards-grid" id="cardsGrid"></div>
                <div class="charts-row">
                    <div class="chart-box">
                        <h3>涨跌幅排行</h3>
                        <div class="chart-container" id="changeChart"></div>
                    </div>
                    <div class="chart-box">
                        <h3>持仓人数变化</h3>
                        <div class="chart-container" id="holdersChart"></div>
                    </div>
                </div>
            </div>

            <!-- Weekly View -->
            <div id="weeklyView" style="display:none">
                <div class="charts-row">
                    <div class="chart-box">
                        <h3>板块热度 Top 10</h3>
                        <div class="chart-container" id="sectorChart"></div>
                    </div>
                    <div class="chart-box">
                        <h3>个股上榜频次</h3>
                        <div class="chart-container" id="frequencyChart"></div>
                    </div>
                </div>
                <div class="chart-box">
                    <h3>热度趋势</h3>
                    <div class="chart-container" id="trendChart" style="height:400px"></div>
                </div>
            </div>

            <!-- Monthly View -->
            <div id="monthlyView" style="display:none">
                <div class="charts-row">
                    <div class="chart-box">
                        <h3>月度板块轮动</h3>
                        <div class="chart-container" id="rotationChart"></div>
                    </div>
                    <div class="chart-box">
                        <h3>持仓长线趋势</h3>
                        <div class="chart-container" id="monthlyHoldersChart"></div>
                    </div>
                </div>
                <div class="chart-box">
                    <h3>月度综合热力图</h3>
                    <div class="chart-container" id="monthlyHeatChart" style="height:400px"></div>
                </div>
            </div>
        </div>
    </div>

    <!-- Confirm Modal -->
    <div class="modal-overlay" id="confirmModal">
        <div class="modal">
            <h2>确认识别结果</h2>
            <p style="color:var(--text-muted);margin-bottom:16px">请检查以下数据是否正确，可点击单元格编辑</p>
            <table class="edit-table">
                <thead>
                    <tr>
                        <th>#</th><th>股票名称</th><th>代码</th><th>热度(万)</th>
                        <th>涨跌%</th><th>成交(亿)</th><th>今持仓</th><th>昨持仓</th>
                    </tr>
                </thead>
                <tbody id="editTableBody"></tbody>
            </table>
            <div class="actions">
                <button class="btn btn-outline" id="cancelBtn">取消</button>
                <button class="btn btn-primary" id="confirmBtn">确认保存</button>
            </div>
        </div>
    </div>

    <script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 3: Commit**

```bash
git add frontend/index.html frontend/style.css
git commit -m "feat: add frontend HTML structure and dark theme CSS"
```

---

### Task 7: 前端 JavaScript - 上传与日报

**Files:**
- Create: `frontend/app.js`

- [ ] **Step 1: 创建 frontend/app.js**

```javascript
// ========== State ==========
let currentView = 'daily';
let parsedData = null;

// ========== DOM ==========
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const datePicker = $('#datePicker');
const uploadArea = $('#uploadArea');
const fileInput = $('#fileInput');
const uploadBtn = $('#uploadBtn');
const confirmModal = $('#confirmModal');
const editTableBody = $('#editTableBody');
const confirmBtn = $('#confirmBtn');
const cancelBtn = $('#cancelBtn');

// ========== Init ==========
document.addEventListener('DOMContentLoaded', () => {
    datePicker.value = new Date().toISOString().slice(0, 10);
    loadDates();
    loadView();
});

// ========== Nav ==========
$$('.tabs .btn').forEach(btn => {
    btn.addEventListener('click', () => {
        $$('.tabs .btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentView = btn.dataset.view;
        loadView();
    });
});

datePicker.addEventListener('change', loadView);

uploadBtn.addEventListener('click', () => {
    const visible = uploadArea.style.display !== 'none';
    uploadArea.style.display = visible ? 'none' : 'block';
});

// ========== Upload ==========
uploadArea.addEventListener('click', () => fileInput.click());
uploadArea.addEventListener('dragover', (e) => { e.preventDefault(); uploadArea.classList.add('dragover'); });
uploadArea.addEventListener('dragleave', () => uploadArea.classList.remove('dragover'));
uploadArea.addEventListener('drop', (e) => {
    e.preventDefault();
    uploadArea.classList.remove('dragover');
    if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener('change', () => { if (fileInput.files.length) handleFile(fileInput.files[0]); });

async function handleFile(file) {
    const formData = new FormData();
    formData.append('file', file);
    uploadBtn.textContent = '识别中...';
    uploadBtn.disabled = true;

    try {
        const resp = await fetch('/api/upload', { method: 'POST', body: formData });
        if (!resp.ok) throw new Error(await resp.text());
        parsedData = await resp.json();
        showConfirmModal(parsedData);
    } catch (e) {
        alert('识别失败: ' + e.message);
    } finally {
        uploadBtn.textContent = '上传图片';
        uploadBtn.disabled = false;
    }
}

// ========== Confirm Modal ==========
function showConfirmModal(data) {
    editTableBody.innerHTML = '';
    data.records.forEach((r, i) => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>${r.rank}</td>
            <td><input value="${r.stock_name}" data-field="stock_name"></td>
            <td><input value="${r.stock_code}" data-field="stock_code" maxlength="6"></td>
            <td><input type="number" value="${r.heat_value}" data-field="heat_value" step="0.01"></td>
            <td><input type="number" value="${r.price_change_pct}" data-field="price_change_pct" step="0.1"></td>
            <td><input type="number" value="${r.turnover_amount}" data-field="turnover_amount" step="0.1"></td>
            <td><input type="number" value="${r.holders_today}" data-field="holders_today"></td>
            <td><input type="number" value="${r.holders_yesterday}" data-field="holders_yesterday"></td>
        `;
        tr.querySelectorAll('input').forEach(inp => {
            inp.addEventListener('change', () => {
                const field = inp.dataset.field;
                parsedData.records[i][field] = inp.type === 'number' ? parseFloat(inp.value) : inp.value;
            });
        });
        editTableBody.appendChild(tr);
    });
    confirmModal.classList.add('show');
}

cancelBtn.addEventListener('click', () => { confirmModal.classList.remove('show'); parsedData = null; });

confirmBtn.addEventListener('click', async () => {
    try {
        const resp = await fetch('/api/records', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(parsedData),
        });
        if (!resp.ok) throw new Error(await resp.text());
        confirmModal.classList.remove('show');
        uploadArea.style.display = 'none';
        datePicker.value = parsedData.date;
        loadView();
        loadDates();
    } catch (e) {
        alert('保存失败: ' + e.message);
    }
});

// ========== Data Loading ==========
async function loadDates() {
    const resp = await fetch('/api/dates');
    const data = await resp.json();
    // Could populate a dropdown later
}

async function loadView() {
    const date = datePicker.value;
    $('#emptyState').style.display = 'none';
    $('#dailyView').style.display = 'none';
    $('#weeklyView').style.display = 'none';
    $('#monthlyView').style.display = 'none';

    try {
        if (currentView === 'daily') {
            const resp = await fetch(`/api/stats/daily?date=${date}`);
            if (!resp.ok) throw new Error('no data');
            const data = await resp.json();
            renderDaily(data);
            $('#dailyView').style.display = 'block';
        } else if (currentView === 'weekly') {
            const resp = await fetch(`/api/stats/weekly?end_date=${date}`);
            if (!resp.ok) throw new Error('no data');
            const data = await resp.json();
            renderWeekly(data);
            $('#weeklyView').style.display = 'block';
        } else {
            const resp = await fetch(`/api/stats/monthly?end_date=${date}`);
            if (!resp.ok) throw new Error('no data');
            const data = await resp.json();
            renderMonthly(data);
            $('#monthlyView').style.display = 'block';
        }
    } catch {
        $('#emptyState').style.display = 'block';
    }
}

// ========== Daily Render ==========
function renderDaily(data) {
    renderCards(data.records);
    renderChangeChart(data.records);
    renderHoldersChart(data.records);
}

function renderCards(records) {
    const grid = $('#cardsGrid');
    grid.innerHTML = records.map(r => {
        const changeClass = r.price_change_pct >= 0 ? 'change-up' : 'change-down';
        const changeSign = r.price_change_pct >= 0 ? '+' : '';
        const diff = (r.holders_today || 0) - (r.holders_yesterday || 0);
        const arrowClass = diff >= 0 ? 'arrow-up' : 'arrow-down';
        const arrow = diff >= 0 ? '↑' : '↓';
        const rankClass = r.rank <= 3 ? `rank-${r.rank}` : '';
        return `
            <div class="stock-card">
                <div style="display:flex;align-items:center;margin-bottom:8px">
                    <span class="rank ${rankClass}">${r.rank}</span>
                    <div>
                        <div class="name">${r.stock_name}</div>
                        <div class="code">${r.stock_code}</div>
                    </div>
                    <div class="spacer"></div>
                    <div class="heat">${r.heat_value}w</div>
                </div>
                <div class="change ${changeClass}">${changeSign}${r.price_change_pct}%</div>
                <div class="holders">
                    <span>今: ${r.holders_today}人</span>
                    <span>昨: ${r.holders_yesterday}人</span>
                    <span class="${arrowClass}">${arrow}${Math.abs(diff)}</span>
                </div>
                <div class="tags">${(r.sector_tags || []).map(t => `<span class="tag">${t}</span>`).join('')}</div>
            </div>
        `;
    }).join('');
}

function renderChangeChart(records) {
    const chart = echarts.init($('#changeChart'), 'dark');
    const sorted = [...records].sort((a, b) => b.price_change_pct - a.price_change_pct);
    chart.setOption({
        backgroundColor: 'transparent',
        grid: { left: 80, right: 20, top: 10, bottom: 30 },
        xAxis: { type: 'value', axisLabel: { formatter: '{value}%' } },
        yAxis: { type: 'category', data: sorted.map(r => r.stock_name), inverse: true },
        series: [{
            type: 'bar',
            data: sorted.map(r => ({
                value: r.price_change_pct,
                itemStyle: { color: r.price_change_pct >= 0 ? '#ef4444' : '#22c55e' }
            })),
            label: { show: true, position: 'right', formatter: '{c}%' },
        }],
    });
    window.addEventListener('resize', () => chart.resize());
}

function renderHoldersChart(records) {
    const chart = echarts.init($('#holdersChart'), 'dark');
    chart.setOption({
        backgroundColor: 'transparent',
        grid: { left: 80, right: 20, top: 30, bottom: 30 },
        legend: { data: ['今日', '昨日'], top: 0 },
        xAxis: { type: 'category', data: records.map(r => r.stock_name), axisLabel: { rotate: 30 } },
        yAxis: { type: 'value' },
        series: [
            { name: '今日', type: 'bar', data: records.map(r => r.holders_today), itemStyle: { color: '#3b82f6' } },
            { name: '昨日', type: 'bar', data: records.map(r => r.holders_yesterday), itemStyle: { color: '#64748b' } },
        ],
    });
    window.addEventListener('resize', () => chart.resize());
}

// ========== Weekly Render ==========
function renderWeekly(data) {
    renderSectorChart(data.records);
    renderFrequencyChart(data.records);
    renderTrendChart(data.records);
}

function renderSectorChart(records) {
    const chart = echarts.init($('#sectorChart'), 'dark');
    const count = {};
    records.forEach(r => (r.sector_tags || []).forEach(t => { count[t] = (count[t] || 0) + 1; }));
    const sorted = Object.entries(count).sort((a, b) => b[1] - a[1]).slice(0, 10);
    chart.setOption({
        backgroundColor: 'transparent',
        grid: { left: 80, right: 20, top: 10, bottom: 30 },
        xAxis: { type: 'value' },
        yAxis: { type: 'category', data: sorted.map(s => s[0]).reverse() },
        series: [{ type: 'bar', data: sorted.map(s => s[1]).reverse(), itemStyle: { color: '#3b82f6' } }],
    });
    window.addEventListener('resize', () => chart.resize());
}

function renderFrequencyChart(records) {
    const chart = echarts.init($('#frequencyChart'), 'dark');
    const count = {};
    records.forEach(r => { count[r.stock_name] = (count[r.stock_name] || 0) + 1; });
    const sorted = Object.entries(count).sort((a, b) => b[1] - a[1]).slice(0, 10);
    chart.setOption({
        backgroundColor: 'transparent',
        grid: { left: 80, right: 20, top: 10, bottom: 30 },
        xAxis: { type: 'value' },
        yAxis: { type: 'category', data: sorted.map(s => s[0]).reverse() },
        series: [{ type: 'bar', data: sorted.map(s => s[1]).reverse(), itemStyle: { color: '#eab308' } }],
    });
    window.addEventListener('resize', () => chart.resize());
}

function renderTrendChart(records) {
    const chart = echarts.init($('#trendChart'), 'dark');
    // Group by stock, get top 5 by frequency
    const stockCount = {};
    records.forEach(r => { stockCount[r.stock_name] = (stockCount[r.stock_name] || 0) + 1; });
    const top5 = Object.entries(stockCount).sort((a, b) => b[1] - a[1]).slice(0, 5).map(s => s[0]);
    const dates = [...new Set(records.map(r => r.date))].sort();
    const series = top5.map(name => ({
        name,
        type: 'line',
        data: dates.map(d => {
            const r = records.find(r => r.date === d && r.stock_name === name);
            return r ? r.heat_value : null;
        }),
        connectNulls: true,
    }));
    chart.setOption({
        backgroundColor: 'transparent',
        grid: { left: 60, right: 20, top: 40, bottom: 30 },
        legend: { data: top5, top: 0 },
        xAxis: { type: 'category', data: dates },
        yAxis: { type: 'value', name: '热度(万)' },
        series,
    });
    window.addEventListener('resize', () => chart.resize());
}

// ========== Monthly Render ==========
function renderMonthly(data) {
    renderRotationChart(data.records);
    renderMonthlyHoldersChart(data.records);
    renderMonthlyHeatChart(data.records);
}

function renderRotationChart(records) {
    const chart = echarts.init($('#rotationChart'), 'dark');
    // Count sector tags per week
    const weekSectors = {};
    records.forEach(r => {
        const weekStart = r.date;
        if (!weekSectors[weekStart]) weekSectors[weekStart] = {};
        (r.sector_tags || []).forEach(t => { weekSectors[weekStart][t] = (weekSectors[weekStart][t] || 0) + 1; });
    });
    const allSectors = [...new Set(records.flatMap(r => r.sector_tags || []))];
    const dates = Object.keys(weekSectors).sort();
    const top10 = allSectors.slice(0, 10);
    chart.setOption({
        backgroundColor: 'transparent',
        grid: { left: 80, right: 20, top: 40, bottom: 30 },
        legend: { data: top10, top: 0, type: 'scroll' },
        xAxis: { type: 'category', data: dates },
        yAxis: { type: 'value' },
        series: top10.map(name => ({
            name, type: 'line', stack: 'total',
            data: dates.map(d => weekSectors[d]?.[name] || 0),
            areaStyle: { opacity: 0.3 },
        })),
    });
    window.addEventListener('resize', () => chart.resize());
}

function renderMonthlyHoldersChart(records) {
    const chart = echarts.init($('#monthlyHoldersChart'), 'dark');
    const stockCount = {};
    records.forEach(r => { stockCount[r.stock_name] = (stockCount[r.stock_name] || 0) + 1; });
    const top5 = Object.entries(stockCount).sort((a, b) => b[1] - a[1]).slice(0, 5).map(s => s[0]);
    const dates = [...new Set(records.map(r => r.date))].sort();
    chart.setOption({
        backgroundColor: 'transparent',
        grid: { left: 60, right: 20, top: 40, bottom: 30 },
        legend: { data: top5, top: 0 },
        xAxis: { type: 'category', data: dates },
        yAxis: { type: 'value', name: '持仓人数' },
        series: top5.map(name => ({
            name, type: 'line',
            data: dates.map(d => {
                const r = records.find(r => r.date === d && r.stock_name === name);
                return r ? r.holders_today : null;
            }),
            connectNulls: true,
        })),
    });
    window.addEventListener('resize', () => chart.resize());
}

function renderMonthlyHeatChart(records) {
    const chart = echarts.init($('#monthlyHeatChart'), 'dark');
    const stocks = [...new Set(records.map(r => r.stock_name))];
    const dates = [...new Set(records.map(r => r.date))].sort();
    const heatData = [];
    records.forEach(r => {
        const x = dates.indexOf(r.date);
        const y = stocks.indexOf(r.stock_name);
        heatData.push([x, y, r.heat_value || 0]);
    });
    chart.setOption({
        backgroundColor: 'transparent',
        grid: { left: 100, right: 60, top: 10, bottom: 40 },
        xAxis: { type: 'category', data: dates, splitArea: { show: true } },
        yAxis: { type: 'category', data: stocks, splitArea: { show: true } },
        visualMap: { min: 0, max: 1500, right: 0, top: 'center', orient: 'vertical', inRange: { color: ['#1e293b', '#3b82f6', '#eab308', '#ef4444'] } },
        series: [{
            type: 'heatmap', data: heatData,
            label: { show: true, formatter: (p) => p.value[2] ? p.value[2] + 'w' : '' },
        }],
    });
    window.addEventListener('resize', () => chart.resize());
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/app.js
git commit -m "feat: add frontend JS with upload, daily/weekly/monthly views"
```

---

### Task 8: 集成测试与启动验证

- [ ] **Step 1: 运行全部测试**

Run: `cd D:/claude-project/stock-analysis-hub && python -m pytest tests/ -v`
Expected: all passed

- [ ] **Step 2: 创建 .env 文件配置百度 OCR 密钥**

```env
BAIDU_OCR_API_KEY=你的API_KEY
BAIDU_OCR_SECRET_KEY=你的SECRET_KEY
```

- [ ] **Step 3: 启动服务验证**

Run: `cd D:/claude-project/stock-analysis-hub && python run.py`
Expected: `Uvicorn running on http://0.0.0.0:8000`

在浏览器打开 `http://localhost:8000` 验证前端页面加载正常。

- [ ] **Step 4: 最终 Commit**

```bash
git add -A
git commit -m "feat: complete Stock Analysis Hub integration"
```

---

## Self-Review Checklist

- [x] Spec coverage: 数据模型 (Task 2-3), 百度 OCR (Task 4), API 路由 (Task 5), 前端 (Task 6-7), 集成 (Task 8)
- [x] Placeholder scan: 无 TBD/TODO
- [x] Type consistency: Pydantic model 字段名在前后端一致
