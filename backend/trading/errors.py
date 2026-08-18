"""交易模块领域错误与 API 错误码。

错误码与 HTTP 状态映射见设计文档第 11.5 章。Phase 1 只定义本阶段用到的错误。
"""
from fastapi import HTTPException


class TradingError(Exception):
    """交易模块领域错误基类。"""

    code: str = "TRADING_ERROR"
    http_status: int = 400

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_http_exception(self) -> HTTPException:
        return HTTPException(
            status_code=self.http_status,
            detail={
                "code": self.code,
                "message": self.message,
                "details": self.details,
            },
        )


# ---- 数据相关(Phase 1 使用) ----

class DataStaleError(TradingError):
    code = "DATA_STALE"
    http_status = 409


class DataIncompleteError(TradingError):
    code = "DATA_INCOMPLETE"
    http_status = 409


class DataConflictError(TradingError):
    code = "DATA_CONFLICT"
    http_status = 409


class ProviderUnavailableError(TradingError):
    code = "PROVIDER_UNAVAILABLE"
    http_status = 503


class ProviderConfigInvalidError(TradingError):
    code = "PROVIDER_CONFIG_INVALID"
    http_status = 500


class EmptyProviderResultError(TradingError):
    code = "EMPTY_PROVIDER_RESULT"
    http_status = 503


class CalendarUnavailableError(TradingError):
    code = "CALENDAR_UNAVAILABLE"
    http_status = 503


class InvalidTradeDateError(TradingError):
    code = "INVALID_TRADE_DATE"
    http_status = 422


class PlanBlockedError(TradingError):
    code = "PLAN_BLOCKED"
    http_status = 409


class PlanInProgressError(TradingError):
    code = "PLAN_IN_PROGRESS"
    http_status = 202


class PlanGenerationFailedError(TradingError):
    code = "PLAN_GENERATION_FAILED"
    http_status = 500


class PlanSupersededError(TradingError):
    code = "PLAN_SUPERSEDED"
    http_status = 409


class StrategyNotActiveError(TradingError):
    code = "STRATEGY_NOT_ACTIVE"
    http_status = 422


class PlanAlreadyPublishedError(TradingError):
    code = "PLAN_ALREADY_PUBLISHED"
    http_status = 409


class ExecutionIdempotencyConflictError(TradingError):
    code = "EXECUTION_IDEMPOTENCY_CONFLICT"
    http_status = 409


class InsufficientCashError(TradingError):
    code = "INSUFFICIENT_CASH"
    http_status = 422


class PositionLimitExceededError(TradingError):
    code = "POSITION_LIMIT_EXCEEDED"
    http_status = 422
