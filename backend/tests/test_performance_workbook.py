"""Year/source isolation, monthly filtering, reconciliation and board drills."""
import copy
from datetime import date
from decimal import Decimal

import pytest

from app.core.response import BizError
from app.services import business_workbook as source, workbook_metrics as metrics
from tests.conftest import data
from tests.test_business_workbook import source_only, sales_row
from tests.test_contract_workbook import older, sales

BASE = "/api/v1/business-workbook/contracts"


@pytest.fixture
def annual_sources(db, source_only):
    wb, p = source_only
    a = sales_row(3, "2026销售合同", "销售客户", 100, received=30)
    a["cells"][7], a["cells"][9], a["cells"][18], a["cells"][3] = "2026-02-03", 70, "市直", "跟进甲"
    wrong = sales_row(4, "销售表内误填旧年份", "客户", 9000)
    wrong["cells"][7] = "2025-01-01"
    wb.sheets = [sales(a, wrong), older(
        (3, "2025四月合同", 200, "2025-04-03", 50, 150),
        (4, "2025十一月合同", 300, "2025-11-12", 100, 200),
        (5, "2024历史合同", 400, "2024-02-02", 200, 200),
        (6, "历史日期缺失", 500, "待定", None, 500),
        (7, "历史表内误填2026", 8000, "2026-02-03", 0, 8000)),
        older((3, "不纳入的长效合同", 70000, "2026-02-03", 10, 69990), profile="longterm")]
    db.commit()
    return wb, p


def test_year_source_rule_cannot_be_bypassed(db, annual_sources, client, login):
    wb, p = annual_sources
    for query in ({}, {"year": "2026"}, {"year": "2026", "sheet": "all"}, {"year": "2026", "sheet": "older"}):
        result = data(client.get(BASE, params=query, headers=login("admin")))
        assert result["total"] == 1 and result["items"][0]["name"] == "2026销售合同"
        assert result["summary"]["amount"]["value"] == "100.00"
        assert {s["profile"] for s in result["sheets"]} == {"sales"}
    year = data(client.get(BASE, params={"year": "2025"}, headers=login("admin")))
    assert year["total"] == 2 and year["summary"]["amount"]["value"] == "500.00"
    assert {r["sheet_id"] for r in year["items"]} == {"older"}
    assert client.get(BASE, params={"sheet": "longterm"}, headers=login("admin")).status_code == 404
    assert client.get(BASE+"/rows/longterm/3?revision=1", headers=login("admin")).status_code == 404


def test_month_filters_match_count_and_all_amounts(db, annual_sources):
    wb, p = annual_sources
    result = source.page(db, p, "contracts", sign_month="2025-11")
    assert result["period"]["year"] == "2025" and result["total"] == 1
    assert {field: result["summary"][field]["value"] for field in ("amount", "received", "outstanding")} == {
        "amount": "300.00", "received": "100.00", "outstanding": "200.00"}
    assert source.page(db, p, "contracts", year="2024", sign_month="2024-02")["total"] == 1
    assert source.page(db, p, "contracts", year="2026", sign_month="2026-11")["summary"]["count"] == 0
    with pytest.raises(BizError, match="不一致"):
        source.page(db, p, "contracts", year="2026", sign_month="2025-11")
    with pytest.raises(BizError, match="格式无效"):
        source.page(db, p, "contracts", sign_month="2025-13")


def test_missing_dates_are_kept_without_assigning_a_year(db, annual_sources):
    wb, p = annual_sources
    result = source.page(db, p, "contracts", year="undated")
    assert result["total"] == 1 and result["items"][0]["name"] == "历史日期缺失"
    assert result["filter_options"]["undated_count"] == 1
    assert result["years"] == ["2026", "2025", "2024"]
    assert all(r["name"] != "历史日期缺失" for r in source.page(db, p, "contracts", year="2025")["items"])


def test_compact_filter_options_only_use_source_fields(db, annual_sources):
    wb, p = annual_sources
    result = source.page(db, p, "contracts", region="市直", customer="销售", owner="跟进甲", keyword="2026销售")
    assert result["total"] == 1
    assert result["filter_options"]["business_lines"] == [] and result["filter_options"]["statuses"] == []
    assert source.page(db, p, "contracts", business_line="推测业务线")["total"] == 0
    assert source.page(db, p, "contracts", region="外部补齐区域")["total"] == 0
    with pytest.raises(BizError, match="指定企微工作簿"):
        source.page(db, p, "contracts", source_type="manual")
    assert source.page(db, p, "contracts", source_type="wecom")["total"] == 1


def test_board_annual_monthly_and_comparison_share_source_routing(db, annual_sources, client, login, monkeypatch):
    wb, p = annual_sources
    monkeypatch.setenv("FAKE_TODAY", "2026-11-17")
    ov = data(client.get("/api/v1/board/overview?period=2026-11", headers=login("admin")))
    assert Decimal(ov["sign"]["year"]["amount"]) == 100
    assert Decimal(ov["sign"]["year"]["last_year_same_period"]) == 500
    for year, total in ((2026, "100.00"), (2025, "500.00"), (2024, "400.00")):
        metric = metrics.contract_metrics(wb, date(year, 1, 1), date(year, 12, 31))
        listing = data(client.get(BASE, params=metric["drill"]["query"], headers=login("admin")))
        assert metric["amount"] == Decimal(total) == Decimal(listing["summary"]["amount"]["value"])
        history = data(client.get(f"/api/v1/board/monthly?year={year}", headers=login("admin")))
        assert Decimal(history["totals"]["sign_amount"]) == Decimal(total)
    assert {s.profile for s in source.module_sheets(wb, "collections")} == {'sales', 'older'}


def test_known_source_missing_or_renamed_does_not_fall_back(db, annual_sources):
    wb, p = annual_sources
    changed = copy.deepcopy(wb.sheets)
    changed[0]["name"] = "1、2027年销售合同"
    wb.sheets = changed
    db.commit()
    with pytest.raises(BizError, match="尚未同步"):
        source.page(db, p, "contracts", year="2026")
    with pytest.raises(BizError, match="以前的数据"):
        source.page(db, p, "contracts", year="2027")


def test_cross_year_drill_remains_equal_to_metric(db, annual_sources):
    wb, p = annual_sources
    metric = metrics.contract_metrics(wb, date(2025, 1, 1), date(2026, 11, 17))
    result = source.page(db, p, "contracts", start="2025-01-01", end="2026-11-17")
    assert metric["amount"] == Decimal(result["summary"]["amount"]["value"]) == 600
    assert result["total"] == metric["count"] == 3
