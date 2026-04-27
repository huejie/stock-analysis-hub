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
    """将 OCR 文本解析为结构化股票记录列表。"""
    if not text.strip():
        return []

    # 按排名数字分割为块
    blocks = re.split(r'\n\s*(?=(?:10|[1-9])\s*\n)', text)
    records = []

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        record = {
            "date": record_date,
            "rank": None,
            "stock_name": "",
            "stock_code": "",
            "heat_value": None,
            "sector_tags": [],
            "price_change_pct": None,
            "turnover_amount": None,
            "holders_today": None,
            "holders_yesterday": None,
            "price_action": "",
        }

        # 排名
        rank_match = re.match(r'^(\d+)\s*$', block.split('\n')[0].strip())
        if rank_match:
            record["rank"] = int(rank_match.group(1))
        else:
            continue

        full_text = block

        # 股票名称 + 热度值
        heat_match = re.search(r'([\u4e00-\u9fa5]+)\s+([\d.]+)w', full_text)
        if heat_match:
            record["stock_name"] = heat_match.group(1)
            record["heat_value"] = float(heat_match.group(2))

        # 股票代码
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
            elif '微跌' in full_text:
                record["price_change_pct"] = -0.1
            elif '大涨' in full_text:
                match = re.search(r'大涨([\d.]+)%', full_text)
                record["price_change_pct"] = float(match.group(1)) if match else 0.1

        # 成交额
        vol_match = re.search(r'(?:成交|爆量)([\d.]+)亿', full_text)
        if vol_match:
            record["turnover_amount"] = float(vol_match.group(1))

        # 今日持仓人数
        holders_match = re.search(r'(\d+)人持仓', full_text)
        if holders_match:
            record["holders_today"] = int(holders_match.group(1))

        # 昨日持仓人数
        yesterday_match = re.search(r'昨日[：:]\s*(\d+)', full_text)
        if yesterday_match:
            record["holders_yesterday"] = int(yesterday_match.group(1))

        # 板块概念标签
        for sector in KNOWN_SECTORS:
            if sector in full_text and sector not in record["sector_tags"]:
                record["sector_tags"].append(sector)

        # 走势描述：提取代码后面的描述部分
        desc_match = re.search(r'\d{6}\s*(.*)', full_text)
        if desc_match:
            desc = desc_match.group(1)
            # 去掉持仓相关部分
            desc = re.split(r'\d+人持仓', desc)[0].strip()
            record["price_action"] = desc

        records.append(record)

    # 按排名排序
    records.sort(key=lambda r: r["rank"] or 99)
    return records


async def analyze_image(image_path: str) -> dict:
    """完整的图片识别流程：OCR → 解析。"""
    text = ocr_image(image_path)
    today = date.today().isoformat()
    records = parse_ocr_text(text, today)
    return {"date": today, "records": records}
