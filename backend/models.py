from pydantic import BaseModel, Field
from typing import Optional


class StockRecord(BaseModel):
    date: str = Field(default="", pattern=r"^\d{4}-\d{2}-\d{2}$")
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
    per_capital_pnl: Optional[float] = None
    per_capital_position: Optional[float] = None


class StockRecordResponse(StockRecord):
    id: int
    created_at: Optional[str] = None


class UploadResult(BaseModel):
    date: str
    records: list[StockRecord]


class DateInfo(BaseModel):
    dates: list[str]
