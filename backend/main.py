import json
import logging as _logging
import os
import shutil
import time as _time
import uuid as _uuid
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
from backend.trading.router import router as trading_router

app = FastAPI(title="Stock Analysis Hub")

_logging.basicConfig(level=_logging.INFO, format='%(message)s')
_logger = _logging.getLogger("stockpulse")


@app.middleware("http")
async def request_id_middleware(request, call_next):
    """结构化日志中间件(spec §11.1):每个请求分配 request_id,记录耗时与状态。"""
    request_id = request.headers.get("X-Request-ID", str(_uuid.uuid4())[:12])
    request.state.request_id = request_id
    start = _time.time()
    try:
        response = await call_next(request)
    except Exception:
        # 异常路径也要记录,便于排查;异常继续向上抛出
        duration_ms = int((_time.time() - start) * 1000)
        _logger.info(json.dumps({
            "request_id": request_id, "method": request.method,
            "path": request.url.path, "status": 500,
            "duration_ms": duration_ms, "error": "unhandled",
        }))
        raise
    duration_ms = int((_time.time() - start) * 1000)
    response.headers["X-Request-ID"] = request_id
    _logger.info(json.dumps({
        "request_id": request_id, "method": request.method,
        "path": request.url.path, "status": response.status_code,
        "duration_ms": duration_ms,
    }))
    return response


db = Database()

if settings.trading_enabled:
    app.include_router(trading_router)

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


@app.get("/api/health")
async def health_check():
    """健康检查(spec §13.5):Web + 数据库。"""
    try:
        db.execute("SELECT 1")
        return {"status": "ok", "db": "ok"}
    except Exception as e:
        return {"status": "degraded", "db": "error", "detail": str(e)}


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


# ---- 连续上榜统计 / 个股历史 / 日报 / 回测 ----

@app.get("/api/stats/streak")
async def get_streak_stats(days: int = 30, min_streak: int = 2):
    """连续上榜统计。"""
    return db.query_streak_stats(days, min_streak)


@app.get("/api/stocks/{code}/history")
async def get_stock_history(code: str):
    """查询单只股票的历史数据（stock_records + lhb_signals + lhb_trading_desk）。"""
    result = db.query_stock_history(code)
    if not result["records"] and not result["lhb_signals"] and not result["lhb_trading_desk"]:
        raise HTTPException(404, "该股票无记录")
    return result


@app.get("/api/reports/daily")
async def daily_report(date_str: str = ""):
    """每日汇总报告。未传 date 取最近有数据的日期。"""
    from datetime import date as date_cls

    # 0. 未传 date 则取最近有数据的日期
    target_date = date_str
    if not target_date:
        all_dates = db.get_all_dates()
        if not all_dates:
            return {"date": "", "sections": []}
        target_date = all_dates[0]

    # 1. 查当天和前一天 stock_records
    rows = db.query_by_date(target_date)
    for row in rows:
        if row.get("sector_tags"):
            row["sector_tags"] = json.loads(row["sector_tags"])

    # 当天无数据返回空 sections
    if not rows:
        return {"date": target_date, "sections": []}

    all_dates = db.get_all_dates()
    prev_date = ""
    for d in all_dates:
        if d < target_date:
            prev_date = d
            break
    prev_rows = db.query_by_date(prev_date)
    for row in prev_rows:
        if row.get("sector_tags"):
            row["sector_tags"] = json.loads(row["sector_tags"])

    sections: list[dict] = []

    # 3. Top3 变动（新进/上升/退出）
    top3_items = []
    prev_codes = {r["stock_code"]: r["rank"] for r in prev_rows}
    curr_codes = {r["stock_code"]: r["rank"] for r in rows}
    for r in rows[:3]:
        code = r["stock_code"]
        if code not in prev_codes:
            top3_items.append({
                "type": "TOP3_NEW", "stock_name": r["stock_name"],
                "stock_code": code, "rank": r["rank"],
                "text": f"{r['stock_name']}(#{r['rank']}) 新进Top3",
            })
        elif prev_codes[code] > r["rank"]:
            top3_items.append({
                "type": "TOP3_UP", "stock_name": r["stock_name"],
                "stock_code": code, "rank": r["rank"], "prev_rank": prev_codes[code],
                "text": f"{r['stock_name']}(#{r['rank']}) 从第{prev_codes[code]}名上升",
            })
        else:
            top3_items.append({
                "type": "TOP3_STABLE", "stock_name": r["stock_name"],
                "stock_code": code, "rank": r["rank"],
                "text": f"{r['stock_name']}(#{r['rank']}) 保持Top3",
            })
    for r in prev_rows[:3]:
        if r["stock_code"] not in curr_codes:
            top3_items.append({
                "type": "TOP3_EXIT", "stock_name": r["stock_name"],
                "stock_code": r["stock_code"], "rank": r["rank"],
                "text": f"{r['stock_name']}(#{r['rank']}) 退出Top3",
            })
    if top3_items:
        sections.append({"title": "Top3 变动", "items": top3_items})

    # 4. 连续上榜追踪
    streak_result = db.query_streak_stats(days=30, min_streak=2)
    streak_items = []
    for s in streak_result.get("streaks", []):
        ranks = s.get("ranks", [])
        first_rank = ranks[-1] if ranks else 0
        last_rank = ranks[0] if ranks else 0
        item_type = "DARK_HORSE" if s["is_dark_horse"] else "STREAK"
        streak_items.append({
            "type": item_type,
            "stock_name": s["stock_name"], "stock_code": s["stock_code"],
            "streak_days": s["streak_days"], "rank_trend": s["rank_trend"],
            "first_rank": first_rank, "last_rank": last_rank,
            "latest_change": s.get("latest_change"),
            "text": f"{s['stock_name']}({s['stock_code']}) 连续{s['streak_days']}天 "
                    f"({first_rank}->{last_rank})",
        })
    if streak_items:
        sections.append({"title": "连续上榜追踪", "items": streak_items})

    # 5. 龙虎榜信号
    lhb_signals = db.query_lhb_signals(target_date)
    for sig in lhb_signals:
        if sig.get("concept_tags"):
            sig["concept_tags"] = json.loads(sig["concept_tags"])
    signal_items = []
    for sig in lhb_signals:
        net_amt = sig.get("net_amt") or 0
        item_type = "FOREIGN" if sig["signal_type"] == "foreign" else "INST"
        signal_items.append({
            "type": item_type,
            "stock_name": sig["stock_name"], "stock_code": sig["stock_code"],
            "net_amt": net_amt,
            "change_rate": sig.get("change_rate"),
            "concept_tags": sig.get("concept_tags", []),
            "text": f"{sig['stock_name']}({sig['stock_code']}) "
                    f"净买入{net_amt:.0f}万",
        })
    if signal_items:
        sections.append({"title": "龙虎榜信号", "items": signal_items})

    # 6. 板块热度变化（降噪：只返回 diff>=2 或 top5）
    sector_change: dict[str, dict] = {}
    for row in prev_rows:
        for tag in row.get("sector_tags", []):
            if tag not in sector_change:
                sector_change[tag] = {"prev_count": 0, "curr_count": 0}
            sector_change[tag]["prev_count"] += 1
    for row in rows:
        for tag in row.get("sector_tags", []):
            if tag not in sector_change:
                sector_change[tag] = {"prev_count": 0, "curr_count": 0}
            sector_change[tag]["curr_count"] += 1

    hot_items = []
    for tag, info in sector_change.items():
        diff = info["curr_count"] - info["prev_count"]
        if diff != 0:
            hot_items.append({
                "type": "SECTOR_HOT" if diff > 0 else "SECTOR_COOL",
                "sector": tag,
                "prev_count": info["prev_count"],
                "curr_count": info["curr_count"],
                "diff": diff,
                "text": f"{tag} {info['prev_count']}→{info['curr_count']} "
                        f"({'+' if diff > 0 else ''}{diff})",
            })
    hot_items.sort(key=lambda x: abs(x["diff"]), reverse=True)
    # 降噪：保留 diff>=2 或排名前 5
    significant = [it for it in hot_items if abs(it["diff"]) >= 2]
    top5 = hot_items[:5]
    seen_sectors = {it["sector"] for it in significant}
    for it in top5:
        if it["sector"] not in seen_sectors:
            significant.append(it)
            seen_sectors.add(it["sector"])
    significant.sort(key=lambda x: abs(x["diff"]), reverse=True)
    if significant:
        sections.append({"title": "板块热度变化", "items": significant})

    return {"date": target_date, "sections": sections}


@app.get("/api/lhb/backtest")
async def get_backtest(signal_type: str = "", months: int = 3, group_by: str = "month"):
    """龙虎榜回测统计。"""
    return db.query_backtest(signal_type, months, group_by)


# ---- AI 分析 ----
# 数据导出由 export_ai_data.py 完成，分析由 Hermes 手动执行，结果通过 import 端点保存。

@app.get("/api/ai/daily-review")
async def get_daily_review(date: str = ""):
    """获取 AI 每日复盘报告（从数据库读取）。"""
    target_date = date or date.today().isoformat()
    row = db.query_ai_analysis("daily_review", target_date)
    if row:
        return {
            "date": target_date,
            "content": row.get("response", ""),
            "generated_at": row.get("created_at", ""),
            "model": row.get("model_name", "Hermes"),
        }
    return {"date": target_date, "content": None, "message": "暂无分析，请导出数据后让 Hermes 分析并导入"}


@app.post("/api/ai/import")
async def import_ai_analysis(data: dict):
    """导入 Hermes 生成的分析结果。

    请求体: {"analysis_type": "daily_review", "date": "2026-06-05", "content": "分析内容...", "model": "Hermes"}
    """
    analysis_type = data.get("analysis_type", "")
    date_str = data.get("date", "")
    content = data.get("content", "")

    if not analysis_type or not date_str or not content:
        raise HTTPException(400, "analysis_type, date, content 均必填")

    valid_types = {"daily_review", "stock_pick", "signal_diagnosis"}
    if analysis_type not in valid_types:
        raise HTTPException(400, f"analysis_type 必须是: {', '.join(valid_types)}")

    db.save_ai_analysis(
        analysis_type=analysis_type,
        date_str=date_str,
        input_summary="Hermes manual import",
        response=content,
        model_name=data.get("model", "Hermes"),
    )
    return {"status": "ok", "message": f"已导入 {analysis_type} ({date_str})"}


@app.get("/api/ai/history")
async def get_ai_history(analysis_type: str = "", days: int = 30):
    """查询 AI 分析历史记录。"""
    return {"records": db.query_ai_history(analysis_type, days)}


@app.get("/api/ai/stock-pick")
async def get_stock_pick(date: str = ""):
    """获取智能选股结果（从数据库读取）。"""
    target_date = date or date.today().isoformat()
    row = db.query_ai_analysis("stock_pick", target_date)
    if row:
        return {
            "date": target_date,
            "content": row.get("response", ""),
            "generated_at": row.get("created_at", ""),
            "model": row.get("model_name", "Hermes"),
        }
    return {"date": target_date, "content": None, "message": "暂无选股结果，请导出数据后让 Hermes 分析并导入"}


@app.get("/api/ai/signal-diagnosis")
async def get_signal_diagnosis(period: int = 30):
    """获取信号有效性诊断（从数据库读取）。"""
    today_str = date.today().isoformat()
    row = db.query_ai_analysis("signal_diagnosis", today_str)
    if row:
        return {
            "date": today_str,
            "content": row.get("response", ""),
            "generated_at": row.get("created_at", ""),
            "model": row.get("model_name", "Hermes"),
        }
    return {"date": today_str, "content": None, "message": "暂无诊断结果，请导出数据后让 Hermes 分析并导入"}
