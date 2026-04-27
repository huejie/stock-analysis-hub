import json
from backend.ocr import parse_ocr_text


def test_parse_full_text():
    """模拟百度 OCR 返回的文字，验证解析结果。"""
    mock_text = """
1
圣阳股份 1358.33w
002580 均内情指标-液冷储能-上午震荡回落-下午大幅冲高回落-收盘上涨4%-成交44亿
42人持仓
昨日：35

2
深圳华强 357.73w
000062 华为系-芯片分销龙头-今日二连板-上涨4天6板-盘中快速封-成交39亿
42人持仓
昨日：11
"""
    result = parse_ocr_text(mock_text, "2026-04-27")
    assert len(result) == 2

    r1 = result[0]
    assert r1["rank"] == 1
    assert r1["stock_name"] == "圣阳股份"
    assert r1["stock_code"] == "002580"
    assert r1["heat_value"] == 1358.33
    assert r1["holders_today"] == 42
    assert r1["holders_yesterday"] == 35
    assert r1["price_change_pct"] == 4.0
    assert r1["turnover_amount"] == 44.0
    assert "液冷储能" in r1["sector_tags"]

    r2 = result[1]
    assert r2["rank"] == 2
    assert r2["stock_name"] == "深圳华强"
    assert r2["stock_code"] == "000062"


def test_parse_negative_change():
    """测试下跌股票的涨跌幅解析。"""
    mock_text = """
5
博云新材 393.72w
002297 商业航天-硬质合金-上周五高位震荡-今日跌停被撬板-收盘下跌7.9%-成交33亿
27人持仓
昨日：23
"""
    result = parse_ocr_text(mock_text, "2026-04-27")
    assert len(result) == 1
    assert result[0]["price_change_pct"] == -7.9
    assert result[0]["turnover_amount"] == 33.0


def test_parse_slight_change():
    """测试微跌的解析。"""
    mock_text = """
4
综艺股份 125.57w
600770 国产芯片-参股神州龙芯-今日三连板炸板跳水-收盘微跌-成交19亿
28人持仓
昨日：5
"""
    result = parse_ocr_text(mock_text, "2026-04-27")
    assert len(result) == 1
    assert result[0]["price_change_pct"] == -0.1


def test_parse_empty_text():
    """测试空文本返回空列表。"""
    result = parse_ocr_text("", "2026-04-27")
    assert result == []


def test_parse_explosive_volume():
    """测试爆量成交额的解析。"""
    mock_text = """
6
信维通信 943.13w
300136 商业航天容错核心票-SpaceX-上周五冲高跳水-今日低开高走-收盘大涨8.8%-爆量120亿
26人持仓
昨日：25
"""
    result = parse_ocr_text(mock_text, "2026-04-27")
    assert len(result) == 1
    assert result[0]["turnover_amount"] == 120.0
    assert result[0]["price_change_pct"] == 8.8
    assert "商业航天" in result[0]["sector_tags"]
    assert "SpaceX" in result[0]["sector_tags"]
