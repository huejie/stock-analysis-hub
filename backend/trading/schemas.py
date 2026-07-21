"""交易模块 Pydantic 请求/响应模型。

字段命名 snake_case,与现有后端一致(文档 11.1)。
"""
import csv
import io

from pydantic import BaseModel, Field, field_validator, model_validator

from .domain import normalize_stock_code


# ---- 股票池 ----

class StockPoolImportRequest(BaseModel):
    """导入股票池(文本/CSV)。

    文本格式:每行一只,'code name' 或 'code',用空格/逗号/制表符分隔。
    CSV 格式:首行表头,必须含 code 列(可选 name)。
    """
    pool_name: str = Field(default="default", min_length=1, max_length=64)
    source: str = Field(..., pattern="^(text|csv|xlsx)$")
    text_body: str = Field(..., min_length=1)
    note: str = ""

    @field_validator("source")
    @classmethod
    def _phase1_source(cls, v: str) -> str:
        # Phase 1 只实现 text/csv,xlsx 占位但暂不支持
        if v == "xlsx":
            raise ValueError("Phase 1 暂不支持 xlsx,请使用 text 或 csv")
        return v

    def parsed_items(self) -> list[dict]:
        """解析输入,返回 [{'stock_code': '000001.SZ', 'stock_name': '...'}]。"""
        items: list[dict] = []
        seen: set[str] = set()
        if self.source == "csv":
            reader = csv.DictReader(io.StringIO(self.text_body))
            for row in reader:
                raw_code = (row.get("code") or row.get("stock_code") or "").strip()
                if not raw_code:
                    continue
                normalized = normalize_stock_code(raw_code)
                if normalized in seen:
                    continue
                seen.add(normalized)
                items.append({
                    "stock_code": normalized,
                    "stock_name": (row.get("name") or row.get("stock_name") or "").strip(),
                })
        else:  # text
            for line in self.text_body.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # 支持 'code name'、'code,name'、'code\tname'、'code'
                parts = line.replace("\t", " ").replace(",", " ").split()
                raw_code = parts[0]
                try:
                    normalized = normalize_stock_code(raw_code)
                except ValueError:
                    continue  # 跳过无法解析的行
                if normalized in seen:
                    continue
                seen.add(normalized)
                name = parts[1] if len(parts) > 1 else ""
                items.append({"stock_code": normalized, "stock_name": name})
        return items


class StockPoolItemResponse(BaseModel):
    stock_code: str
    stock_name: str | None = None
    sector_name: str | None = None
    manual_blacklist: bool = False
    note: str = ""


class StockPoolVersionResponse(BaseModel):
    id: int
    pool_name: str
    version_no: int
    items_hash: str
    source: str
    created_at: str
    items: list[StockPoolItemResponse] = []


class StockPoolListItem(BaseModel):
    """股票池版本列表项(不含明细)。"""
    id: int
    pool_name: str
    version_no: int
    items_count: int
    created_at: str


# ---- 数据任务 ----

class DataJobCreateRequest(BaseModel):
    """创建数据任务(手动触发行情更新/回补)。"""
    job_type: str = Field(..., pattern="^(update_bars|backfill_bars|refresh_calendar|validate_data)$")
    trade_date: str | None = None       # 单日更新
    start_date: str | None = None       # 回补区间
    end_date: str | None = None
    stock_codes: list[str] | None = None  # None 表示股票池全部

    @model_validator(mode="after")
    def _check_dates(self):
        if self.job_type == "backfill_bars":
            if not self.start_date or not self.end_date:
                raise ValueError("backfill_bars 必须提供 start_date 和 end_date")
        return self


class DataJobResponse(BaseModel):
    id: int
    job_type: str
    job_key: str
    status: str
    progress: float
    created_at: str
    started_at: str | None
    finished_at: str | None
    error_json: dict | None = None


# ---- 数据健康 ----

class DataIssueOut(BaseModel):
    severity: str
    issue_code: str
    message: str
    stock_code: str | None = None
    details: dict = {}


class DataHealthResponse(BaseModel):
    trade_date: str
    overall_status: str  # OK / PARTIAL / BLOCKED
    benchmark_codes: list[str]
    benchmark_updated: dict[str, bool]   # code -> 是否更新到 trade_date
    pool_total: int
    pool_available: int
    pool_missing: list[str]
    pool_missing_ratio: float
    issues: list[DataIssueOut]
    generated_at: str
