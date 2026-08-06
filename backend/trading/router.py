"""交易决策 API 路由（/api/trading/*）。

通过 APIRouter 注册到 main.py，不直接修改 main.py 路由列表（文档第 6 章）。
Phase 1: 股票池导入/查询、数据健康。
Phase 2: 账户、持仓、成交、净值快照（spec §12.5）。
Phase 3: 策略版本、计划生成。
Phase 5: 回测、复盘。

data-jobs 创建任务记录后,通过 run_in_executor 后台执行行情抓取,前端轮询状态。
"""
import hashlib
import json
from datetime import date

from fastapi import APIRouter, HTTPException, Query

from ..config import settings
from .providers.factory import get_provider
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
from .services.strategy_service import StrategyService
from .services.plan_service import PlanService
from .services.backtest_service import BacktestService
from .services.review_service import ReviewService
from .schemas import (
    StrategyCreateRequest,
    StrategyResponse,
    StrategyActivateResponse,
    PlanRunCreateRequest,
    PlanRunResponse,
    PlanRunDetailResponse,
    PlanItemResponse,
    PlanPublishResponse,
    BacktestCreateRequest,
    BacktestRunResponse,
    BacktestTradeResponse,
    ReviewSummaryResponse,
)
from .errors import (
    PlanBlockedError,
    PlanAlreadyPublishedError,
    StrategyNotActiveError,
)

router = APIRouter(prefix="/api/trading", tags=["trading"])

# 模块级单例（测试通过 monkeypatch 替换）
trading_repo = TradingRepository(settings.db_path)
pool_service = PoolService(trading_repo)
market_data_service = MarketDataService(trading_repo, provider=get_provider())
account_service = AccountService(trading_repo)
execution_service = ExecutionService(trading_repo)
portfolio_service = PortfolioService(trading_repo)
strategy_service = StrategyService(trading_repo)
plan_service = PlanService(trading_repo, market_data_service, portfolio_service)
backtest_service = BacktestService(trading_repo)
review_service = ReviewService(trading_repo)


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

# 幂等保护:同 job_key 正在运行时不重复启动(spec §13.1 互斥语义)
_running_job_keys: set[str] = set()


def _execute_data_job(job_id: int, job_key: str, req_data: dict) -> None:
    """后台执行数据任务(在 run_in_executor 线程中调用)。

    状态流转:RUNNING → SUCCEEDED(带 result) / FAILED(带 error)。
    """
    try:
        trading_repo.update_job(job_id, status="RUNNING")
        job_type = req_data.get("job_type", "")
        trade_date_str = req_data.get("trade_date", "")
        target = date.fromisoformat(trade_date_str) if trade_date_str else date.today()

        if job_type in ("update_bars", "validate_data"):
            # 更新股票池全部 + 基准
            pool_count = market_data_service.update_pool_bars("default", target)
            benchmarks = [c.strip() for c in settings.trading_benchmark_codes.split(",") if c.strip()]
            bench_count = market_data_service.update_benchmark(target, benchmarks)
            result = {"pool_bars_updated": pool_count, "benchmark_bars_updated": bench_count}

        elif job_type == "backfill_bars":
            start_str = req_data.get("start_date")
            end_str = req_data.get("end_date")
            if not start_str or not end_str:
                raise ValueError("backfill_bars 需要 start_date 和 end_date")
            codes = req_data.get("stock_codes")
            if not codes:
                codes = trading_repo.get_latest_pool_codes("default")
            # 分日回补
            from datetime import timedelta
            cur = date.fromisoformat(start_str)
            end = date.fromisoformat(end_str)
            total = 0
            while cur <= end:
                total += market_data_service.update_bars(codes, cur)
                cur += timedelta(days=1)
            result = {"bars_updated": total, "date_range": f"{start_str}~{end_str}"}

        elif job_type == "refresh_calendar":
            # Phase 5 简化:等价于 update_bars
            pool_count = market_data_service.update_pool_bars("default", target)
            result = {"bars_updated": pool_count}

        else:
            raise ValueError(f"未知 job_type: {job_type}")

        trading_repo.update_job(job_id, status="SUCCEEDED", progress=1.0, result=result)
    except Exception as e:
        trading_repo.update_job(job_id, status="FAILED", error={"error": str(e)})
    finally:
        _running_job_keys.discard(job_key)


@router.post("/data-jobs", status_code=202)
async def create_data_job(req: DataJobCreateRequest):
    import asyncio
    target = date.fromisoformat(req.trade_date) if req.trade_date else date.today()
    job_key = hashlib.sha256(
        f"{req.job_type}|{target.isoformat()}|{req.start_date}|{req.end_date}".encode()
    ).hexdigest()[:16]

    # 幂等:同 key 正在运行 → 不重复启动
    if job_key in _running_job_keys:
        existing = trading_repo.get_job_by_key(job_key) if hasattr(trading_repo, "get_job_by_key") else None
        if existing:
            return {"job_id": existing["id"], "status": "RUNNING", "job_key": job_key, "reused": True}

    job_id = trading_repo.create_job(
        job_type=req.job_type,
        job_key=job_key,
        request={"trade_date": target.isoformat(),
                 "start_date": req.start_date, "end_date": req.end_date,
                 "stock_codes": req.stock_codes, "job_type": req.job_type},
    )
    # 后台执行(参考 main.py lhb pool 更新模式)
    _running_job_keys.add(job_key)
    req_data = {"job_type": req.job_type, "trade_date": target.isoformat(),
                "start_date": req.start_date, "end_date": req.end_date,
                "stock_codes": req.stock_codes}
    asyncio.get_running_loop().run_in_executor(
        None, _execute_data_job, job_id, job_key, req_data
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


# ---- Phase 3: 策略 ----

@router.get("/strategies")
async def list_strategies(strategy_code: str | None = Query(default=None)):
    """策略版本列表(可选 strategy_code 过滤,spec §11.3)。"""
    rows = strategy_service.list_strategies(strategy_code=strategy_code)
    return {"strategies": [StrategyResponse(**r).model_dump() for r in rows]}


@router.post("/strategies")
async def create_strategy(req: StrategyCreateRequest):
    """创建策略版本(DRAFT)。params_hash 命中复用(spec §11.3)。"""
    try:
        created = strategy_service.create_strategy(
            strategy_code=req.strategy_code, name=req.name,
            params_json=req.params_json,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    # 补全 Response 所需字段(create 仅返回 id/version_no/params_hash/status/reused)
    full = strategy_service.get_strategy(created["id"])
    return StrategyResponse(**full).model_dump()


@router.post("/strategies/{version_id}/activate")
async def activate_strategy(version_id: int):
    """激活策略版本(DRAFT→ACTIVE,同 code 旧版本 RETIRED,stub 门禁 warning)。

    spec §14.4:Phase 5 回测引擎就绪后启用真校验,当前只返回 warning。
    """
    try:
        result = strategy_service.activate_strategy(version_id)
    except ValueError as e:
        # 不存在或已退役 → 区分 404 / 400
        if "不存在" in str(e):
            raise HTTPException(404, str(e))
        raise HTTPException(400, str(e))
    return StrategyActivateResponse(**result).model_dump()


# ---- Phase 3: 计划 ----

@router.post("/plan-runs")
async def create_plan_run(req: PlanRunCreateRequest):
    """生成计划(10 步流程 + 幂等键,spec §9/§11.3)。

    幂等命中返回 reused=True 的已有 run;策略未激活 → 422;
    数据门禁阻断 → 409;引擎异常 → 400/500。
    """
    try:
        result = plan_service.generate_plan(
            account_id=req.account_id, signal_date=req.signal_date,
            stock_pool_version_id=req.stock_pool_version_id,
            strategy_version_id=req.strategy_version_id,
            force_new_version=req.force_new_version,
        )
    except StrategyNotActiveError as e:
        raise e.to_http_exception()
    except PlanBlockedError as e:
        raise e.to_http_exception()
    except ValueError as e:
        raise HTTPException(400, str(e))
    return PlanRunResponse(
        id=result["id"], run_key=result["run_key"], status=result["status"],
        signal_date=result["signal_date"],
        target_trade_date=result["target_trade_date"],
        reused=result["reused"],
    ).model_dump()


@router.get("/plan-runs")
async def list_plan_runs(signal_date: str | None = Query(default=None),
                         status: str | None = Query(default=None)):
    """计划运行列表(可选 signal_date/status 过滤,spec §11.3)。"""
    rows = trading_repo.list_plan_runs(signal_date=signal_date, status=status)
    return {"plan_runs": rows}


@router.get("/plan-runs/{run_id}")
async def get_plan_run_detail(run_id: int):
    """计划详情 + 全部明细(spec §11.4)。"""
    detail = plan_service.get_plan_detail(run_id)
    if detail is None:
        raise HTTPException(404, f"计划 {run_id} 不存在")
    items = [PlanItemResponse(**it).model_dump() for it in detail["items"]]
    return PlanRunDetailResponse(
        id=detail["id"], status=detail["status"],
        signal_date=detail["signal_date"],
        target_trade_date=detail["target_trade_date"],
        market_regime=detail.get("market_regime"),
        market_score=detail.get("market_score"),
        degraded=detail.get("degraded", False),
        recommended_exposure=detail.get("recommended_exposure"),
        warnings=detail.get("warnings", []),
        items=items,
        created_at=detail.get("created_at"),
        published_at=detail.get("published_at"),
    ).model_dump()


@router.post("/plan-runs/{run_id}/publish")
async def publish_plan_run(run_id: int):
    """发布计划(READY/PARTIAL→PUBLISHED,spec §9.2/§11.4)。

    已发布 → 409 PLAN_ALREADY_PUBLISHED;非就绪 → 409 PLAN_BLOCKED。
    """
    try:
        result = plan_service.publish_plan(run_id)
    except PlanAlreadyPublishedError as e:
        raise e.to_http_exception()
    except PlanBlockedError as e:
        raise e.to_http_exception()
    except ValueError as e:
        if "不存在" in str(e):
            raise HTTPException(404, str(e))
        raise HTTPException(400, str(e))
    return PlanPublishResponse(
        id=result["id"], status=result["status"],
        published_at=result["published_at"],
    ).model_dump()


# ---- Phase 5: 回测 ----

@router.post("/backtests")
async def create_backtest(req: BacktestCreateRequest):
    """创建并同步运行回测(spec §11.2 POST /backtests;Phase 5 同步返回 200)。

    spec §11.1 说异步任务返回 202,但 Phase 5 无异步任务队列,小型 fixture 回测
    很快,故同步运行并直接返回结果。错误:策略/池不存在 → 400;日期非法 → 400。
    """
    fee_params = None
    if req.fee_params is not None:
        fee_params = req.fee_params.model_dump(exclude_none=True)
    try:
        result = backtest_service.run_backtest(
            strategy_version_id=req.strategy_version_id,
            stock_pool_version_id=req.stock_pool_version_id,
            start_date=req.start_date, end_date=req.end_date,
            initial_equity=req.initial_equity, fee_params=fee_params,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return BacktestRunResponse(
        id=result["id"], job_id=result["job_id"],
        strategy_version_id=result["strategy_version_id"],
        stock_pool_version_id=result["stock_pool_version_id"],
        start_date=result["start_date"], end_date=result["end_date"],
        initial_equity=result["initial_equity"], fee_params=result["fee_params"],
        status=result["status"], metrics=result["metrics"],
        equity_curve=result["equity_curve"],
        trades=[BacktestTradeResponse(**t) for t in result["trades"]],
        created_at=result["created_at"], finished_at=result.get("finished_at"),
    ).model_dump()


@router.get("/backtests/{run_id}")
async def get_backtest(run_id: int):
    """查询回测状态 + 指标 + 交易(spec §11.2)。"""
    result = backtest_service.get_backtest(run_id)
    if result is None:
        raise HTTPException(404, f"回测 {run_id} 不存在")
    return BacktestRunResponse(
        id=result["id"], job_id=result["job_id"],
        strategy_version_id=result["strategy_version_id"],
        stock_pool_version_id=result["stock_pool_version_id"],
        start_date=result["start_date"], end_date=result["end_date"],
        initial_equity=result["initial_equity"], fee_params=result["fee_params"],
        status=result["status"], metrics=result["metrics"],
        equity_curve=result["equity_curve"],
        trades=[BacktestTradeResponse(**t) for t in result["trades"]],
        created_at=result["created_at"], finished_at=result.get("finished_at"),
    ).model_dump()


# ---- Phase 5: 复盘 ----

@router.get("/reviews/summary")
async def review_summary(account_id: int = Query(...),
                         period: int = Query(default=30, gt=0)):
    """复盘摘要(spec §11.2 GET /reviews/summary)。

    账户不存在 → 400;period <= 0 → 422(由 Query gt=0 保证)。
    """
    try:
        result = review_service.get_summary(account_id=account_id, period=period)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return ReviewSummaryResponse(**result).model_dump()
