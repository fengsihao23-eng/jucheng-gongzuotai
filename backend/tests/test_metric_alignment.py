"""Cross-module and Xiaoju/MCP metric alignment against the fixed WeCom workbook."""
from copy import deepcopy
from decimal import Decimal

from app.services import xiaoju_business as business
from tests.conftest import data
from tests.test_business_workbook import source_only  # noqa: F401
from tests.test_collection_workbook import collection_source  # noqa: F401
from tests.test_xiaoju_business import executor


def test_cross_page_and_xiaoju_metrics_align_with_workbook(db, client, login, collection_source, monkeypatch):
    monkeypatch.setenv("FAKE_TODAY", "2026-11-18")
    wb, _ = collection_source
    sheets = deepcopy(wb.sheets)
    # Configure explicit Nov/Dec planned collection columns and Nov actual receipt progress on both sheets.
    for idx, src in enumerate(sheets[:2]):
        src["rows"][0]["cells"][10] = "11月预计可回款金额"
        src["rows"][0]["cells"][11] = "11月回款进度"
        src["rows"][0]["cells"][12] = "12月达回款条件，应回款金额"
        rows = {r["row"]: r for r in src["rows"]}
        for n in (3, 4, 5):
            rows[n]["cells"][10] = rows[n]["cells"][11] = rows[n]["cells"][12] = None
        if idx == 0:
            # 2026 sales sheet: rows 3, 4, 5
            rows[3]["cells"][7] = "2026-11-05"
            rows[4]["cells"][7] = "2026-06-10"
            rows[5]["cells"][7] = "2026-08-15"
            rows[3]["cells"][10], rows[5]["cells"][10] = 120, 80
            rows[3]["cells"][11] = "11月10日已回款50元"
            rows[3]["cells"][12], rows[4]["cells"][12] = 60, 40
            plan_nov, prog_nov, plan_dec = 200, 50, 100
        else:
            rows[3]["cells"][10] = 300
            rows[3]["cells"][11] = "11月12日已收到30元"
            rows[3]["cells"][12] = 150
            plan_nov, prog_nov, plan_dec = 300, 30, 150
        rows[8]["cells"].extend([None] * (13 - len(rows[8]["cells"])))
        rows[8]["cells"][10], rows[8]["cells"][11], rows[8]["cells"][12] = plan_nov, prog_nov, plan_dec
    wb.sheets = sheets
    db.commit()

    headers = login("admin")
    # 1. Benchmark endpoints: /business-workbook/contracts & /business-workbook/collections
    contracts_wb = data(client.get("/api/v1/business-workbook/contracts", params={"year": "2026"}, headers=headers))
    collections_wb = data(client.get("/api/v1/business-workbook/collections", params={"unpaid_only": True}, headers=headers))
    cards = collections_wb["collection_cards"]
    assert [c["title"] for c in cards] == ["当月预估回款金额", "次月预估回款金额"]
    assert [c["amount"] for c in cards] == ["500.00", "250.00"]

    expected_year_sign = Decimal(contracts_wb["summary"]["amount"]["value"])
    expected_nov_plan = Decimal("500.00")
    expected_dec_plan = Decimal("250.00")
    expected_nov_paid = Decimal("80.00")

    # 2. Board & Morning Report
    board_ov = data(client.get("/api/v1/board/overview", params={"refresh": 1}, headers=headers))
    assert Decimal(board_ov["sign"]["year"]["amount"]) == expected_year_sign
    assert Decimal(board_ov["payment"]["amount"]) == expected_nov_paid
    assert Decimal(board_ov["payment"]["planned_amount"]) == expected_nov_plan
    assert Decimal(board_ov["payment"]["next_planned_amount"]) == expected_dec_plan

    # 3. Performance page (/api/v1/performance/overview & /board & /export)
    perf_ov = data(client.get("/api/v1/performance/overview", params={"period": "2026-11"}, headers=headers))
    assert perf_ov["source_mode"] == "workbook"
    assert Decimal(perf_ov["year_sign"]["amount"]) == expected_year_sign
    assert Decimal(perf_ov["month_pay"]["amount"]) == expected_nov_paid
    assert Decimal(perf_ov["planned_pay"]["amount"]) == expected_nov_plan
    assert Decimal(perf_ov["planned_pay"]["next_amount"]) == expected_dec_plan
    assert perf_ov["planned_pay"]["title"] == "当月预估回款金额"
    assert sum(Decimal(r["year_sign"]) for r in perf_ov["by_owner"]) == expected_year_sign
    assert sum(Decimal(r["year_sign"]) for r in perf_ov["by_line"]) == expected_year_sign

    perf_board = data(client.get("/api/v1/performance/board", headers=headers))
    assert perf_board["source_mode"] == "workbook"
    assert Decimal(perf_board["summary"]["contract_amount"]) == Decimal(collections_wb["overall_summary"]["amount"]["value"])
    assert Decimal(perf_board["summary"]["receivable"]) == Decimal(collections_wb["overall_summary"]["outstanding"]["value"])
    assert Decimal(perf_board["summary"]["planned_month"]) == expected_nov_plan
    assert client.get("/api/v1/performance/export?period=2026-11", headers=headers).status_code == 200

    # 4. Legacy API endpoints exposed to Xiaoju: /contracts/signing & /collections
    signing_api = data(client.get("/api/v1/contracts/signing", headers=headers))
    assert signing_api["source_mode"] == "workbook"
    by_key = {item["key"]: Decimal(item["amount"]) for item in signing_api["items"]}
    assert by_key["year"] == expected_year_sign

    col_api = data(client.get("/api/v1/collections", headers=headers))
    assert col_api["source_mode"] == "workbook"
    assert Decimal(col_api["month_received"]["amount"]) == expected_nov_paid
    assert [c["title"] for c in col_api["collection_cards"]] == ["当月预估回款金额", "次月预估回款金额"]

    # 5. Xiaoju BusinessExecutor & MCP tools alignment + live data_mode
    runner = executor(db, "admin", read_only=True)
    found_perf = runner.discover(query="今年业绩和预估回款")
    op_ids = [o["operation_id"] for o in found_perf["operations"]]
    assert any(op.startswith("business_workbook.") for op in op_ids)

    xj_perf = runner.execute("performance.overview", {"query": {"period": "2026-11"}})
    assert xj_perf["ok"] and xj_perf["data_mode"] == "live"
    assert Decimal(xj_perf["data"]["year_sign"]["amount"]) == expected_year_sign
    assert Decimal(xj_perf["data"]["planned_pay"]["amount"]) == expected_nov_plan

    xj_col = runner.execute("business_workbook.page", {"path": {"view": "collections"}, "query": {"unpaid_only": True}})
    assert xj_col["ok"] and xj_col["data_mode"] == "live"
    assert [c["title"] for c in xj_col["data"]["collection_cards"]] == ["当月预估回款金额", "次月预估回款金额"]

    xj_daily = executor(db, "admin").execute("mcp.board.executive_daily", {"body": {"date": "2026-11-18"}})
    assert xj_daily["ok"] and "当月预估回款" in xj_daily["data"]["summary"]
