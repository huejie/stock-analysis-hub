import re
import base64
import httpx
from pathlib import Path
from datetime import date
from backend.config import settings


# 百度 OCR API
TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
OCR_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic"

# 已知板块概念关键词（可扩展）
KNOWN_SECTORS = [
    "液冷储能", "商业航天", "光模块", "华为系", "芯片", "国产芯片",
    "医药", "SpaceX", "江苏板块", "光通信", "人工智能", "新能源",
    "半导体", "军工", "消费电子", "汽车", "光伏", "锂电",
    "机器人", "算力", "数据中心", "网络安全", "区块链",
    "参股", "趋势龙头", "高标龙头", "分销龙头",
]


def _get_access_token() -> str:
    """获取百度 OCR access_token。"""
    resp = httpx.post(TOKEN_URL, params={
        "grant_type": "client_credentials",
        "client_id": settings.baidu_ocr_api_key,
        "client_secret": settings.baidu_ocr_secret_key,
    })
    return resp.json()["access_token"]


def ocr_image(image_path: str) -> str:
    """调用百度 OCR 识别图片，返回全部文字拼接结果。"""
    token = _get_access_token()
    image_data = base64.b64encode(Path(image_path).read_bytes()).decode()

    resp = httpx.post(
        OCR_URL,
        params={"access_token": token},
        data={"image": image_data},
    )
    resp.raise_for_status()
    result = resp.json()

    words = [item["words"] for item in result.get("words_result", [])]
    return "\n".join(words)


def parse_ocr_text(text: str, record_date: str) -> list[dict]:
    """将 OCR 文本解析为结构化股票记录列表。

    核心思路：用"中文名+热度值(w)"作为锚点定位每只股票，
    不依赖排名数字（OCR 经常漏识别排名）。

    OCR 实际格式示例：
        1
        圣阳股份1358.33w
        002580场内情绪高标-液冷储能-上午震荡回
        42人持仓>
        调-下午大幅冲高回落-收盘上涨4%-成交44亿
        昨日：35

    或排名被 OCR 漏掉：
        深圳华强373.22w
        000062华为昇腾-芯片分销龙头-今日二连板-
        44人持仓>
        上演4天3板-盘中炸板回封-成交39亿
        昨日：11
    """
    if not text.strip():
        return []

    lines = text.strip().split('\n')

    # 股票名称 + 热度值的正则（锚点模式）
    stock_heat_re = re.compile(r'([\u4e00-\u9fa5]{2,6})([\d.]+)w')

    # 找到所有包含"股票名+热度"的行（锚点行）
    anchor_indices = []
    for i, line in enumerate(lines):
        if stock_heat_re.search(line.strip()):
            anchor_indices.append(i)

    if not anchor_indices:
        return []

    # 确定每个锚点的 block 起始行（可能包含前一行的排名数字）
    block_starts = []
    for anchor in anchor_indices:
        bs = anchor
        if anchor > 0:
            prev = lines[anchor - 1].strip()
            if re.match(r'^(10|[1-9])$', prev):
                bs = anchor - 1
        block_starts.append(bs)

    records = []
    for idx, anchor in enumerate(anchor_indices):
        block_start = block_starts[idx]
        block_end = block_starts[idx + 1] if idx + 1 < len(anchor_indices) else len(lines)

        block_lines = [lines[j].strip() for j in range(block_start, block_end)]
        full_text = '\n'.join(block_lines)
        anchor_line = lines[anchor].strip()

        # 确定排名
        rank = idx + 1  # 默认按锚点出现顺序
        # 锚点行内嵌排名（如 "8东山精密871.87w"）
        rank_m = re.match(r'^(10|[1-9])([\u4e00-\u9fa5])', anchor_line)
        if rank_m:
            rank = int(rank_m.group(1))
        else:
            # block 首行是独立排名数字
            first = block_lines[0].strip()
            rank_m = re.match(r'^(10|[1-9])$', first)
            if rank_m:
                rank = int(rank_m.group(1))

        record = {
            "date": record_date,
            "rank": rank,
            "stock_name": "",
            "stock_code": "",
            "heat_value": None,
            "sector_tags": [],
            "price_change_pct": None,
            "turnover_amount": None,
            "holders_today": None,
            "holders_yesterday": None,
            "price_action": "",
            "per_capital_pnl": None,
            "per_capital_position": None,
        }

        # 股票名称 + 热度值
        heat_match = stock_heat_re.search(full_text)
        if heat_match:
            record["stock_name"] = heat_match.group(1)
            record["heat_value"] = float(heat_match.group(2))

        # 股票代码（6位数字）
        code_match = re.search(r'(?<!\d)(\d{6})(?!\d)', full_text)
        if code_match:
            record["stock_code"] = code_match.group(1)

        # 涨跌幅
        pct_match = re.search(r'上涨([\d.]+)%', full_text)
        if pct_match:
            record["price_change_pct"] = float(pct_match.group(1))
        else:
            pct_match = re.search(r'下跌([\d.]+)%', full_text)
            if pct_match:
                record["price_change_pct"] = -float(pct_match.group(1))
            elif '大跌' in full_text:
                match = re.search(r'大跌([\d.]+)%', full_text)
                record["price_change_pct"] = -float(match.group(1)) if match else -0.1
            elif '微跌' in full_text:
                record["price_change_pct"] = -0.1
            elif '大涨' in full_text:
                match = re.search(r'大涨([\d.]+)%', full_text)
                record["price_change_pct"] = float(match.group(1)) if match else 0.1
            elif '跌停' in full_text:
                record["price_change_pct"] = -10.0

        # 成交额
        vol_match = re.search(r'(?:成交|爆量)([\d.]+)亿', full_text)
        if vol_match:
            record["turnover_amount"] = float(vol_match.group(1))
        else:
            # "爆量" 后面的数字可能在后续任一行（可能隔了一行）
            if '爆量' in full_text:
                for j, bl in enumerate(block_lines):
                    if '爆量' in bl:
                        for k in range(j + 1, len(block_lines)):
                            num_match = re.match(r'([\d.]+)亿', block_lines[k].strip())
                            if num_match:
                                record["turnover_amount"] = float(num_match.group(1))
                                break
                        break

        # 今日持仓人数
        holders_match = re.search(r'(\d+)人持仓', full_text)
        if holders_match:
            record["holders_today"] = int(holders_match.group(1))

        # 昨日持仓人数
        yesterday_match = re.search(r'昨日[：:]\s*(\d+)', full_text)
        if yesterday_match:
            record["holders_yesterday"] = int(yesterday_match.group(1))

        # 人均盈亏（正负均可，格式如 "人均盈亏-2.3万" 或 "人均盈亏+1.5万"）
        pnl_match = re.search(r'人均盈亏\s*([+-]?[\d.]+)\s*万', full_text)
        if pnl_match:
            record["per_capital_pnl"] = float(pnl_match.group(1))

        # 人均仓位（格式如 "人均仓位12.5万"）
        pos_match = re.search(r'人均仓位\s*([\d.]+)\s*万', full_text)
        if pos_match:
            record["per_capital_position"] = float(pos_match.group(1))

        # 板块概念标签
        for sector in KNOWN_SECTORS:
            if sector in full_text and sector not in record["sector_tags"]:
                record["sector_tags"].append(sector)

        # 走势描述：提取代码后面的描述部分，合并多行
        desc_parts = []
        for bl in block_lines:
            if stock_heat_re.search(bl):
                continue  # 跳过股票名称+热度行
            desc_match = re.match(r'\d{6}(.*)', bl)
            if desc_match:
                desc_parts.append(desc_match.group(1).strip())
            elif re.match(r'[\u4e00-\u9fa5]', bl) and '人持仓' not in bl and '昨日' not in bl and not re.match(r'[\d.]+亿', bl):
                desc_parts.append(bl)
        if desc_parts:
            record["price_action"] = ''.join(desc_parts)

        records.append(record)

    # 按排名排序
    records.sort(key=lambda r: r["rank"] or 99)
    return records


def extract_date_from_text(text: str) -> str | None:
    """从 OCR 文本中提取日期，支持多种格式。"""
    now = date.today()

    # 2026-04-28 或 2026/04/28
    m = re.search(r'(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?', text)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # 04月28日 或 4月28日（用当前年份）
    m = re.search(r'(\d{1,2})月(\d{1,2})日', text)
    if m:
        return f"{now.year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"

    # 04-28 或 04/28（用当前年份）
    m = re.search(r'(?<!\d)(\d{1,2})[-/](\d{1,2})(?!\d)', text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{now.year}-{month:02d}-{day:02d}"

    return None


async def analyze_image(image_path: str) -> dict:
    """完整的图片识别流程：OCR → 解析。"""
    text = ocr_image(image_path)

    # 保存原始 OCR 文本用于调试
    debug_path = Path("data/ocr_debug_latest.txt")
    debug_path.parent.mkdir(parents=True, exist_ok=True)
    debug_path.write_text(text, encoding="utf-8")

    detected_date = extract_date_from_text(text) or date.today().isoformat()
    records = parse_ocr_text(text, detected_date)
    return {"date": detected_date, "records": records, "raw_text": text}
