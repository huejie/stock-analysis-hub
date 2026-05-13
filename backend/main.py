import json
import os
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse

from backend.config import settings
from backend.crawler import crawl_and_save
from backend.database import Database
from backend.lhb_crawler import crawl_lhb, update_lhb_pool
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


@app.post("/api/crawl")
async def crawl_today():
    """手动触发爬虫，拉取今天数据。"""
    import asyncio
    try:
        result = await asyncio.to_thread(crawl_and_save, db)
        if result["skipped"]:
            return {"status": "ok", "message": f"{result['date']} 非交易日，已跳过", "date": result["date"], "stock_count": 0}
        if result["stock_count"] == 0:
            return {"status": "ok", "message": f"{result['date']} 暂无数据", "date": result["date"], "stock_count": 0}
        return {"status": "ok", "message": f"拉取成功，{result['date']} 共 {result['stock_count']} 只股票", "date": result["date"], "stock_count": result["stock_count"]}
    except Exception as e:
        raise HTTPException(500, f"爬取失败: {str(e)}")


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


# ---- 龙虎榜 ----

@app.post("/api/crawl-lhb")
async def crawl_lhb_today(date: str | None = None):
    """手动触发龙虎榜爬虫。可选 ?date=YYYY-MM-DD 指定日期。"""
    import asyncio
    try:
        from datetime import date as date_cls
        td = date_cls.fromisoformat(date) if date else None
        result = await asyncio.to_thread(crawl_lhb, db, td)
        return {
            "status": "ok",
            "message": f"龙虎榜 {result['date']}：汇总 {result['summary_count']} 条，信号股 {result['signal_count']} 只",
            **result,
        }
    except Exception as e:
        raise HTTPException(500, f"龙虎榜爬取失败: {str(e)}")


@app.post("/api/crawl-lhb-batch")
async def crawl_lhb_batch(start_date: str, end_date: str):
    """批量抓取龙虎榜。?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD"""
    import asyncio
    from datetime import date as date_cls, timedelta
    try:
        start = date_cls.fromisoformat(start_date)
        end = date_cls.fromisoformat(end_date)
        if start > end:
            start, end = end, start
    except (ValueError, TypeError):
        raise HTTPException(400, "日期格式错误，请使用 YYYY-MM-DD")

    results = []
    current = start
    while current <= end:
        if current.weekday() < 5:  # 跳过周末
            result = await asyncio.to_thread(crawl_lhb, db, current)
            results.append(result)
        current += timedelta(days=1)

    total_summary = sum(r["summary_count"] for r in results)
    total_signals = sum(r["signal_count"] for r in results)
    return {
        "status": "ok",
        "message": f"批量抓取完成：{len(results)}个交易日，汇总 {total_summary} 条，信号股 {total_signals} 只",
        "days": len(results),
        "total_summary": total_summary,
        "total_signals": total_signals,
        "details": results,
    }


@app.get("/api/lhb/signals")
async def get_lhb_signals(date: str = ""):
    """查询龙虎榜信号股。不传 date 返回所有。"""
    if date:
        rows = db.query_lhb_signals(date)
    else:
        rows = db.query_lhb_signals()
    for row in rows:
        if row.get("concept_tags"):
            row["concept_tags"] = json.loads(row["concept_tags"])
    return rows


@app.get("/api/lhb/signal-dates")
async def get_lhb_signal_dates():
    """获取龙虎榜信号股所有日期。"""
    return {"dates": db.get_lhb_signal_dates()}


@app.get("/api/lhb/trading-desk")
async def get_lhb_trading_desk(date: str, stock_code: str):
    """查询指定股票在指定日期的买卖营业部明细。"""
    return db.query_lhb_trading_desk(date, stock_code)


@app.get("/api/lhb/analysis")
async def lhb_analysis(months: int = 3):
    """龙虎榜近 N 个月板块分析。返回概念板块出现频率和平均涨跌幅。"""
    end = date.today()
    start = end - timedelta(days=months * 30)
    start_str = start.isoformat()
    end_str = end.isoformat()

    rows = db.query_lhb_signals_range(start_str, end_str)

    # 统计概念板块出现频率
    sector_freq: dict[str, dict] = {}
    for row in rows:
        tags = json.loads(row.get("concept_tags") or "[]")
        change = row.get("change_rate")
        for tag in tags:
            if tag not in sector_freq:
                sector_freq[tag] = {"count": 0, "total_change": 0.0, "change_list": []}
            sector_freq[tag]["count"] += 1
            if change is not None:
                sector_freq[tag]["total_change"] += change
                sector_freq[tag]["change_list"].append(change)

    # 按出现频率排序，取 Top 30
    sorted_sectors = sorted(sector_freq.items(), key=lambda x: x[1]["count"], reverse=True)[:30]
    result = []
    for tag, info in sorted_sectors:
        avg_change = info["total_change"] / len(info["change_list"]) if info["change_list"] else None
        result.append({
            "sector": tag,
            "count": info["count"],
            "avg_change": round(avg_change, 2) if avg_change is not None else None,
        })

    # 按信号类型统计
    type_stats = {}
    for row in rows:
        st = row.get("signal_type", "unknown")
        if st not in type_stats:
            type_stats[st] = {"count": 0, "total_net": 0.0}
        type_stats[st]["count"] += 1
        net = row.get("net_amt") or 0
        type_stats[st]["total_net"] += net

    return {
        "start_date": start_str,
        "end_date": end_str,
        "total_signals": len(rows),
        "sector_distribution": result,
        "signal_type_stats": type_stats,
    }


# ---- 龙虎榜股池 ----

@app.get("/api/lhb/pool")
async def get_lhb_pool(signal_type: str = ""):
    """查询龙虎榜股池（近30天有上榜）。可选 ?signal_type=foreign"""
    rows = db.query_lhb_pool(signal_type=signal_type)
    for row in rows:
        if row.get("concept_tags"):
            row["concept_tags"] = json.loads(row["concept_tags"])
    return rows


@dataclass
class PoolUpdateState:
    running: bool = False
    last_result: dict | None = None

_pool_update_state = PoolUpdateState()

@app.get("/api/lhb/pool/status")
async def get_pool_update_status():
    """查询股池更新状态。"""
    return {
        "running": _pool_update_state.running,
        "last_result": _pool_update_state.last_result,
    }

@app.post("/api/lhb/pool/update")
async def trigger_lhb_pool_update():
    """手动触发股池数据更新（后台异步执行）。"""
    if _pool_update_state.running:
        return {"status": "already_running", "message": "股池正在更新中，请稍后刷新查看"}
    import asyncio

    def _run():
        _pool_update_state.running = True
        try:
            result = update_lhb_pool(db)
            _pool_update_state.last_result = {
                "finished_at": datetime.now().isoformat(),
                **result,
            }
        except Exception as e:
            _pool_update_state.last_result = {"error": str(e)}
        finally:
            _pool_update_state.running = False

    asyncio.get_running_loop().run_in_executor(None, _run)
    return {"status": "started", "message": "股池更新已启动，后台执行中..."}
