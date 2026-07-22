"""交易决策 API 路由（/api/trading/*）。

通过 APIRouter 注册到 main.py，不直接修改 main.py 路由列表（文档第 6 章）。
Phase 1 实现：股票池导入/查询、数据健康、数据任务。
Phase 2 实现：账户、持仓、成交、净值快照（spec §12.5）。

注意：Phase 1 的 data-jobs 只创建任务记录，不实际执行抓取（执行需要 scheduler，
Phase 5）。前端可轮询状态，但任务会停留在 QUEUED。
"""
import hashlib
import json
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
    AccountCreateRequest,
    AccountUpdateRequest,
    AccountResponse,
    PositionResponse,
    PositionManualCorrectRequest,
    ExecutionCreateRequest,
    ExecutionResponse,
    EquitySnapshotResponse,
)
from .services.account_service import AccountService
from .services.execution_service import ExecutionService
from .services.market_data_service import MarketDataService
from .services.pool_service import PoolService
from .services.portfolio_service import PortfolioService

router = APIRouter(prefix="/api/trading", tags=["trading"])

# 模块级单例（测试通过 monkeypatch 替换）
trading_repo = TradingRepository(settings.db_path)
pool_service = PoolService(trading_repo)
market_data_service = MarketDataService(trading_repo, provider=None)
account_service = AccountService(trading_repo)
execution_service = ExecutionService(trading_repo)
portfolio_service = PortfolioService(trading_repo)


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


# ---- Phase 2: 账户 ----

@router.post("/accounts")
async def create_account(req: AccountCreateRequest):
    """创建账户(spec §8.2 风险配置默认值)。"""
    try:
        acc = account_service.create_account(**req.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return AccountResponse(**acc).model_dump()


@router.get("/accounts")
async def list_accounts(active_only: bool = Query(default=False)):
    """账户列表(可选只看 active)。"""
    accounts = account_service.list_accounts(active_only=active_only)
    return {"accounts": [AccountResponse(**a).model_dump() for a in accounts]}


@router.get("/accounts/{account_id}")
async def get_account(account_id: int):
    """账户详情(404 if missing)。"""
    acc = account_service.get_account(account_id)
    if not acc:
        raise HTTPException(404, f"账户 {account_id} 不存在")
    return AccountResponse(**acc).model_dump()


@router.put("/accounts/{account_id}")
async def update_account(account_id: int, req: AccountUpdateRequest):
    """更新账户(部分字段)。initial_equity 不可改,传 400。"""
    fields = req.model_dump(exclude_none=True)
    try:
        acc = account_service.update_account(account_id, fields)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not acc:
        raise HTTPException(404, f"账户 {account_id} 不存在")
    return AccountResponse(**acc).model_dump()


# ---- Phase 2: 持仓 ----

@router.get("/positions")
async def get_positions(account_id: int = Query(...)):
    """持仓列表(spec §12.5)。"""
    positions = trading_repo.get_positions(account_id)
    return {"positions": [PositionResponse(**p).model_dump() for p in positions]}


@router.put("/positions/{stock_code}")
async def manual_correct_position(stock_code: str, req: PositionManualCorrectRequest):
    """人工校正持仓(spec §12.5)。对账差异需人工确认。"""
    pos = trading_repo.upsert_position(
        account_id=req.account_id, stock_code=stock_code,
        quantity=req.quantity, available_quantity=req.available_quantity,
        average_cost=req.average_cost, initial_stop=req.initial_stop,
        trailing_stop=req.trailing_stop,
    )
    trading_repo.write_audit_log(
        actor="user", action="MANUAL_CORRECT_POSITION", entity_type="position",
        entity_id=str(pos["id"]),
        after_json=json.dumps(
            {"quantity": req.quantity, "available_quantity": req.available_quantity,
             "note": req.note},
            ensure_ascii=False,
        ),
    )
    return PositionResponse(**pos).model_dump()


# ---- Phase 2: 成交 ----

@router.post("/executions")
async def record_execution(req: ExecutionCreateRequest):
    """录入成交(原子更新 现金/持仓/审计 + client_execution_id 幂等)。

    返回 {execution, position}。SELL 时若无持仓或可卖不足,抛 ValueError -> 400。
    """
    try:
        result = execution_service.record_execution(**req.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "execution": ExecutionResponse(**result["execution"]).model_dump(),
        "position": PositionResponse(**result["position"]).model_dump() if result["position"] else None,
    }


@router.get("/executions")
async def list_executions(account_id: int = Query(...),
                          start: str | None = Query(default=None),
                          end: str | None = Query(default=None)):
    """成交记录(按 trade_date 范围过滤)。"""
    execs = trading_repo.list_executions(account_id, start=start, end=end)
    return {"executions": [ExecutionResponse(**e).model_dump() for e in execs]}


# ---- Phase 2: 净值快照 ----

@router.get("/equity-snapshots/{account_id}")
async def get_equity_snapshot(account_id: int, trade_date: str = Query(...)):
    """计算并保存指定交易日的净值快照(含 peak/drawdown)。

    持仓行情缺失抛 ValueError -> 400(spec §7.4 阻断原则)。
    """
    try:
        snap = portfolio_service.compute_and_save_equity_snapshot(
            account_id, date.fromisoformat(trade_date)
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return EquitySnapshotResponse(**snap).model_dump()
