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

    百度 OCR 返回的实际格式（每只股票 5-6 行）：
        1
        圣阳股份1358.33w
        002580场内情绪高标-液冷储能-上午震荡回
        42人持仓>
        调-下午大幅冲高回落-收盘上涨4%-成交44亿
        昨日：35
    """
    if not text.strip():
        return []

    lines = text.strip().split('\n')
    records = []
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        # 匹配排名行（纯数字 1-10，可能紧跟名称如 "7天银机电"）
        rank_match = re.match(r'^(10|[1-9])(?:\s|$)', line)
        if not rank_match:
            # 也可能是 "7天银机电917.15w" 这种排名和名称在一行
            rank_match = re.match(r'^(10|[1-9])([\u4e00-\u9fa5].*)', line)

        if not rank_match:
            i += 1
            continue

        rank = int(rank_match.group(1))
        rest_of_line = rank_match.group(2).strip() if rank_match.lastindex >= 2 else ""

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
        }

        # 收集该股票的所有文本行（直到下一个排名出现）
        block_lines = []
        if rest_of_line:
            block_lines.append(rest_of_line)
        i += 1

        while i < len(lines):
            next_line = lines[i].strip()
            # 检查是否是下一个排名
            if re.match(r'^(10|[1-9])(?:\s|$)', next_line) or re.match(r'^(10|[1-9])([\u4e00-\u9fa5])', next_line):
                break
            block_lines.append(next_line)
            i += 1

        # 合并所有行用于全文匹配
        full_text = '\n'.join(block_lines)

        # 股票名称 + 热度值（名称和数字之间可能无空格）
        heat_match = re.search(r'([\u4e00-\u9fa5]{2,6})([\d.]+)w', full_text)
        if heat_match:
            record["stock_name"] = heat_match.group(1)
            record["heat_value"] = float(heat_match.group(2))

        # 股票代码（6位数字，在行首出现）
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
                        # 检查之后的所有行找 "XX亿"
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

        # 板块概念标签
        for sector in KNOWN_SECTORS:
            if sector in full_text and sector not in record["sector_tags"]:
                record["sector_tags"].append(sector)

        # 走势描述：提取代码后面的描述部分，合并多行
        desc_parts = []
        for bl in block_lines:
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


async def analyze_image(image_path: str) -> dict:
    """完整的图片识别流程：OCR → 解析。"""
    text = ocr_image(image_path)
    today = date.today().isoformat()
    records = parse_ocr_text(text, today)
    return {"date": today, "records": records}
