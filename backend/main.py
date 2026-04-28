import json
import os
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.config import settings
from backend.database import Database
from backend.models import UploadResult
from backend.ocr import analyze_image

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

    prev_date_obj = datetime.strptime(date, "%Y-%m-%d") - timedelta(days=1)
    prev_date = prev_date_obj.strftime("%Y-%m-%d")
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
