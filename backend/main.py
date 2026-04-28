import json
import os
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse

from backend.config import settings
from backend.database import Database
from backend.models import UploadResult
from backend.ocr import analyze_image

app = FastAPI(title="Stock Analysis Hub")

db = Database()

# 静态文件 - Vue build output
frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
frontend_legacy = Path(__file__).parent.parent / "frontend"

if frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="assets")
elif frontend_legacy.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_legacy)), name="static")


@app.get("/")
async def index():
    return RedirectResponse(url="/preview")


@app.get("/preview")
async def preview():
    if frontend_dist.exists():
        return FileResponse(str(frontend_dist / "index.html"))
    return FileResponse(str(frontend_legacy / "index.html"))


@app.get("/admin")
async def admin():
    if frontend_dist.exists():
        return FileResponse(str(frontend_dist / "index.html"))
    return FileResponse(str(frontend_legacy / "index.html"))


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
        record["sector_tags"] = json.dumps(
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
            row["sector_tags"] = json.loads(row["sector_tags"])
    return rows


@app.get("/api/records/range")
async def get_records_range(start: str, end: str):
    """查询日期范围内的记录。"""
    rows = db.query_date_range(start, end)
    for row in rows:
        if row.get("sector_tags"):
            row["sector_tags"] = json.loads(row["sector_tags"])
    return rows


@app.get("/api/dates")
async def get_dates():
    """获取所有有数据的日期。"""
    return {"dates": db.get_all_dates()}


@app.get("/api/stats/daily")
async def daily_stats(date: str):
    """日报统计数据，含前一天对比。"""
    rows = db.query_by_date(date)
    if not rows:
        raise HTTPException(404, "该日期无数据")
    for row in rows:
        if row.get("sector_tags"):
            row["sector_tags"] = json.loads(row["sector_tags"])

    # 从已有数据中找上一个交易日（自动跳过周末和假期）
    all_dates = db.get_all_dates()  # DESC order
    prev_date = ""
    for d in all_dates:
        if d < date:
            prev_date = d
            break
    prev_rows = db.query_by_date(prev_date)
    for row in prev_rows:
        if row.get("sector_tags"):
            row["sector_tags"] = json.loads(row["sector_tags"])

    return {
        "date": date,
        "records": rows,
        "prev_records": prev_rows,
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
    end = datetime.strptime(end_date, "%Y-%m-%d")
    start = (end - timedelta(days=6)).isoformat()
    rows = db.query_date_range(start, end_date)
    for row in rows:
        if row.get("sector_tags"):
            row["sector_tags"] = json.loads(row["sector_tags"])
    return {"start_date": start, "end_date": end_date, "records": rows}


@app.get("/api/stats/monthly")
async def monthly_stats(end_date: str):
    """月报统计数据（end_date 往前 30 天）。"""
    end = datetime.strptime(end_date, "%Y-%m-%d")
    start = (end - timedelta(days=29)).isoformat()
    rows = db.query_date_range(start, end_date)
    for row in rows:
        if row.get("sector_tags"):
            row["sector_tags"] = json.loads(row["sector_tags"])
    return {"start_date": start, "end_date": end_date, "records": rows}


# ---- 赛季统计 ----

@app.post("/api/season-stats")
async def save_season_stats(data: dict):
    """批量保存赛季每日统计数据。格式: {"records": [{"date":"2026-04-27","per_capital_pnl":0.02,"per_capital_position":81.95}, ...]}"""
    records = data.get("records", [])
    if not records:
        raise HTTPException(400, "无数据")
    for r in records:
        if not r.get("date"):
            raise HTTPException(400, "每条记录必须包含 date")
    try:
        db.upsert_season_stats(records)
    except Exception as e:
        raise HTTPException(400, f"保存失败: {str(e)}")
    return {"status": "ok", "count": len(records)}


@app.get("/api/season-stats")
async def get_season_stats(start: str = "", end: str = ""):
    """查询赛季统计数据。"""
    rows = db.query_season_stats(start, end)
    return rows


@app.get("/api/season-stats/dates")
async def get_season_dates():
    """获取所有赛季数据日期。"""
    return {"dates": db.get_season_dates()}


# ---- 赛季管理 ----

@app.get("/api/seasons")
async def get_seasons():
    """获取所有赛季。"""
    return db.get_all_seasons()


@app.post("/api/seasons")
async def create_season(data: dict):
    """创建赛季。格式: {"name":"第2赛季","start_date":"2026-05-01","end_date":"2026-06-30"}"""
    name = data.get("name", "")
    start = data.get("start_date", "")
    end = data.get("end_date", "")
    if not name or not start or not end:
        raise HTTPException(400, "name, start_date, end_date 均必填")
    return db.create_season(name, start, end)


@app.put("/api/seasons/{season_id}")
async def update_season(season_id: int, data: dict):
    """更新赛季信息。"""
    ok = db.update_season(
        season_id,
        name=data.get("name"),
        start_date=data.get("start_date"),
        end_date=data.get("end_date"),
    )
    if not ok:
        raise HTTPException(400, "无更新")
    return {"status": "ok"}
