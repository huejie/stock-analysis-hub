"""交易决策 API 路由（/api/trading/*）。

通过 APIRouter 注册到 main.py，不直接修改 main.py 路由列表（文档第 6 章）。
Phase 1 实现：股票池导入/查询、数据健康、数据任务。

注意：Phase 1 的 data-jobs 只创建任务记录，不实际执行抓取（执行需要 scheduler，
Phase 5）。前端可轮询状态，但任务会停留在 QUEUED。
"""
import hashlib
from datetime import date

from fastapi import APIRouter, HTTPException, Query

from ..config import settings
from .repository import TradingRepository
from .schemas import (
    DataHealthResponse,
    DataIssueOut,
    DataJobCreateRequest,
    DataJobResponse,
    StockPoolImportRequest,
    StockPoolItemResponse,
    StockPoolListItem,
    StockPoolVersionResponse,
)
from .services.market_data_service import MarketDataService
from .services.pool_service import PoolService

router = APIRouter(prefix="/api/trading", tags=["trading"])

# 模块级单例（测试通过 monkeypatch 替换）
trading_repo = TradingRepository(settings.db_path)
pool_service = PoolService(trading_repo)
market_data_service = MarketDataService(trading_repo, provider=None)


def _benchmark_codes() -> list[str]:
    return [c.strip() for c in settings.trading_benchmark_codes.split(",") if c.strip()]


# ---- 股票池 ----

@router.post("/stock-pools/import")
async def import_stock_pool(req: StockPoolImportRequest):
    try:
        result = pool_service.import_from_text(
            req.pool_name, req.text_body, source=req.source
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@router.get("/stock-pools")
async def list_stock_pools(pool_name: str = Query(default="default")):
    versions = pool_service.list_versions(pool_name)
    return {"versions": [
        StockPoolListItem(
            id=v["id"], pool_name=v["pool_name"], version_no=v["version_no"],
            items_count=v["items_count"], created_at=v["created_at"],
        ).model_dump() for v in versions
    ]}


@router.get("/stock-pools/{version_id}")
async def get_stock_pool(version_id: int):
    v = pool_service.get_version(version_id)
    if not v:
        raise HTTPException(404, f"股票池版本 {version_id} 不存在")
    return StockPoolVersionResponse(
        id=v["id"], pool_name=v["pool_name"], version_no=v["version_no"],
        items_hash=v["items_hash"], source=v["source"], created_at=v["created_at"],
        items=[StockPoolItemResponse(**it) for it in v["items"]],
    ).model_dump()


# ---- 数据健康 ----

@router.get("/data-health")
async def data_health(trade_date: str = Query(default="")):
    target = date.fromisoformat(trade_date) if trade_date else date.today()
    report = market_data_service.check_data_health(
        trade_date=target,
        pool_name="default",
        benchmark_codes=_benchmark_codes(),
    )
    return DataHealthResponse(
        trade_date=report["trade_date"],
        overall_status=report["overall_status"],
        benchmark_codes=report["benchmark_codes"],
        benchmark_updated=report["benchmark_updated"],
        pool_total=report["pool_total"],
        pool_available=report["pool_available"],
        pool_missing=report["pool_missing"],
        pool_missing_ratio=report["pool_missing_ratio"],
        issues=[DataIssueOut(**i) for i in report["issues"]],
        generated_at=report["generated_at"],
    ).model_dump()


# ---- 数据任务 ----

@router.post("/data-jobs", status_code=202)
async def create_data_job(req: DataJobCreateRequest):
    target = date.fromisoformat(req.trade_date) if req.trade_date else date.today()
    job_key = hashlib.sha256(
        f"{req.job_type}|{target.isoformat()}|{req.start_date}|{req.end_date}".encode()
    ).hexdigest()[:16]
    job_id = trading_repo.create_job(
        job_type=req.job_type,
        job_key=job_key,
        request={"trade_date": target.isoformat(),
                 "start_date": req.start_date, "end_date": req.end_date,
                 "stock_codes": req.stock_codes},
    )
    return {"job_id": job_id, "status": "QUEUED", "job_key": job_key}


@router.get("/data-jobs/{job_id}")
async def get_data_job(job_id: int):
    job = trading_repo.get_job(job_id)
    if not job:
        raise HTTPException(404, f"任务 {job_id} 不存在")
    return DataJobResponse(
        id=job["id"], job_type=job["job_type"], job_key=job["job_key"],
        status=job["status"], progress=job["progress"],
        created_at=job["created_at"], started_at=job["started_at"],
        finished_at=job["finished_at"], error_json=job.get("error"),
    ).model_dump()
