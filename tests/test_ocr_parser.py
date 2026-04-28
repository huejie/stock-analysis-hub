import json
from backend.ocr import parse_ocr_text


def test_parse_full_text():
    """模拟百度 OCR 实际返回的文字格式（名称和热度无空格，描述跨多行）。"""
    mock_text = """1
圣阳股份1358.33w
002580场内情绪高标-液冷储能-上午震荡回
42人持仓>
调-下午大幅冲高回落-收盘上涨4%-成交44亿
昨日：35
2
深圳华强357.73w
000062华为昇腾-芯片分销龙头-今日二连板-
42人持仓>
上演4天3板-盘中炸板回封-成交39亿
昨日：11
3
天通股份907.58w
600330铌酸锂-光模块上游-商业航天-今日高
34人持仓>
位触板回落-再创新高-收盘上涨5.9%-成交72亿
昨日：12"""
    result = parse_ocr_text(mock_text, "2026-04-27")
    assert len(result) == 3

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
    assert r2["heat_value"] == 357.73
    assert r2["price_change_pct"] == 10.0  # 二连板+炸板回封 = 涨停
    assert r2["turnover_amount"] == 39.0

    r3 = result[2]
    assert r3["rank"] == 3
    assert r3["stock_name"] == "天通股份"
    assert r3["price_change_pct"] == 5.9
    assert "商业航天" in r3["sector_tags"]


def test_parse_negative_change():
    """测试下跌股票的涨跌幅解析。"""
    mock_text = """5
博云新材393.72w
002297商业航天-硬质合金-上周五高位震荡-
27人持仓>
今日跌停被撬板-收盘下跌7.9%-成交33亿
昨日：23"""
    result = parse_ocr_text(mock_text, "2026-04-27")
    assert len(result) == 1
    assert result[0]["stock_name"] == "博云新材"
    assert result[0]["price_change_pct"] == -7.9
    assert result[0]["turnover_amount"] == 33.0


def test_parse_slight_change():
    """测试微跌的解析。"""
    mock_text = """4
综艺股份125.57w
600770国产芯片-参股神州龙芯-今日三连板炸
28人持仓>
板跳水-收盘微跌-成交19亿
昨日：5"""
    result = parse_ocr_text(mock_text, "2026-04-27")
    assert len(result) == 1
    assert result[0]["stock_name"] == "综艺股份"
    assert result[0]["price_change_pct"] == -0.1


def test_parse_empty_text():
    """测试空文本返回空列表。"""
    result = parse_ocr_text("", "2026-04-27")
    assert result == []


def test_parse_explosive_volume_multiline():
    """测试爆量成交额跨行解析。"""
    mock_text = """6
信维通信934.13w
300136商业航天容量核心票-SpaceX-上周五
26人持仓>
冲高跳水-今日低开高走-收盘大涨8.8%-爆量
昨日：25
120亿"""
    result = parse_ocr_text(mock_text, "2026-04-27")
    assert len(result) == 1
    assert result[0]["stock_name"] == "信维通信"
    assert result[0]["turnover_amount"] == 120.0
    assert result[0]["price_change_pct"] == 8.8
    assert "商业航天" in result[0]["sector_tags"]
    assert "SpaceX" in result[0]["sector_tags"]


def test_parse_rank_with_name_inline():
    """测试排名和名称在同一行（如 "7天银机电"）。"""
    mock_text = """7天银机电917.15w
300342商业航天20厘米趋势龙头-近期新高横
25人持仓>
盘-今日低开震荡翻红-收盘上涨1%-成交35亿
昨日：32"""
    result = parse_ocr_text(mock_text, "2026-04-27")
    assert len(result) == 1
    assert result[0]["rank"] == 7
    assert result[0]["stock_name"] == "天银机电"
    assert result[0]["heat_value"] == 917.15
    assert result[0]["price_change_pct"] == 1.0


def test_parse_limit_down():
    """测试跌停的解析。"""
    mock_text = """8
苏州高新337.45w
600736江苏板块-商业航天-参股联讯仪器-今
24人持仓>
日低开跌停-盘中多次撬板未果-成交13亿
昨日：34"""
    result = parse_ocr_text(mock_text, "2026-04-27")
    assert len(result) == 1
    assert result[0]["stock_name"] == "苏州高新"
    assert result[0]["price_change_pct"] == -10.0


def test_parse_limit_up_keywords():
    """测试各种涨停描述：封板、涨停、X连板、X天Y板、回封。"""
    cases = [
        ("600736", "封板", 10.0),
        ("600736", "涨停", 10.0),
        ("000062", "今日首板", 10.0),
        ("000062", "今日三连板", 10.0),
        ("300136", "上演5天3板", 20.0),  # 创业板 20%
        ("688256", "炸板回封", 20.0),   # 科创板 20%
        ("002580", "尾盘回封", 10.0),   # 主板 10%
    ]
    for code, keyword, expected in cases:
        mock_text = f"""1
测试股票500.00w
{code}描述文字-{keyword}-成交10亿
30人持仓>
昨日：20"""
        result = parse_ocr_text(mock_text, "2026-04-27")
        assert len(result) == 1, f"Failed for '{keyword}' with code {code}"
        assert result[0]["price_change_pct"] == expected, (
            f"'{keyword}' with code {code}: expected {expected}, got {result[0]['price_change_pct']}"
        )
