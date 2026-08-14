"""股票池服务:导入/版本化/查询。

封装 Repository 的股票池操作,提供业务语义(去重、版本化、差异)。
不直接接触 SQL,不直接接触 Provider。
"""
from ..schemas import StockPoolImportRequest


class PoolService:
    def __init__(self, repo):
        self.repo = repo

    def import_from_text(self, pool_name: str, text_body: str,
                         source: str = "text") -> dict:
        req = StockPoolImportRequest(
            pool_name=pool_name, source=source, text_body=text_body
        )
        items = req.parsed_items()
        if not items:
            raise ValueError("解析后股票池为空,请检查输入格式")
        return self.repo.create_stock_pool_version(pool_name, items, source=source)

    def import_from_csv(self, pool_name: str, csv_body: str) -> dict:
        return self.import_from_text(pool_name, csv_body, source="csv")

    def get_version(self, version_id: int) -> dict | None:
        return self.repo.get_stock_pool_version(version_id)

    def list_versions(self, pool_name: str | None = None) -> list[dict]:
        return self.repo.list_stock_pool_versions(pool_name)

    def get_latest_codes(self, pool_name: str = "default") -> list[str]:
        return self.repo.get_latest_pool_codes(pool_name)

    def sync_from_hotlist(self, pool_name: str = "default", top_n: int = 10) -> dict:
        """从最新热榜 Top N 同步股票池(只读 stock_records)。

        代码用 normalize 转换(000636 → 000636.SZ),ST/北交所过滤。
        相同成分幂等(同 items_hash 返回 reused)。
        """
        from ..domain import normalize_stock_code
        top = self.repo.get_latest_hotlist_top(limit=top_n)
        if not top:
            raise ValueError("热榜无数据,请先运行热榜爬虫(crawl.py)")
        items = []
        for t in top:
            try:
                code = normalize_stock_code(t["stock_code"])
            except ValueError:
                continue
            items.append({"stock_code": code, "stock_name": t["stock_name"]})
        if not items:
            raise ValueError("热榜过滤后无有效沪深股票")
        return self.repo.create_stock_pool_version(
            pool_name, items, source="hotlist",
        )
