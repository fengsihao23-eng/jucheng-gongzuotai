"""Source exclusivity, financial precision, row lifecycle, and safe failures."""
import copy
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from io import BytesIO

import pytest
from openpyxl import load_workbook
from openpyxl.utils.datetime import to_excel
from sqlalchemy import delete, func, select

from app.core.deps import build_principal
from app.core.response import BizError
from app.core.timeutil import today
from app.models import BizContract, BizOpportunity, BizPayment, ContractWorkbook, SysDict, SysUser
from app.services import business_workbook as svc, contract_workbook_sync as sync, dict_service
from tests.conftest import data
from tests.test_contract_workbook import older, sales, sales_row as workbook_row, tender, changing_source

KEY = "only-workbook-test"
BASE = "/api/v1/business-workbook"


def sales_row(*args, **kwargs):
    row = workbook_row(*args, **kwargs)
    row["cells"][7] = "2026-03-05"
    return row


@pytest.fixture
def source_only(db):
    admin = db.scalar(select(SysUser).where(SysUser.wecom_userid == "admin"))
    wb = ContractWorkbook(source_key=KEY, config={"title": "指定经营表格", "url": "https://doc.weixin.qq.com/sheet/e3_only",
        "format": "workbook", "reader_id": admin.id, "enabled": True, "auto_apply": False, "auto_receipts": False,
        "presentation_only": True, "profiles": {}, "view_user_ids": [], "view_department_ids": []},
        revision="1", sheets=[sales(sales_row(3, "唯一原表合同", "源表客户", 100, received=30))], summary={})
    wb.sheets[0]["rows"][-1]["cells"][9] = 65  # Source discrepancy must not be silently corrected to 70.
    db.add(wb)
    dict_service.put(db, svc.CONFIG, "primary", {"enabled": True, "source_key": KEY})
    db.commit()
    yield wb, build_principal(admin)
    db.execute(delete(SysDict).where(SysDict.dict_type == svc.CONFIG))
    db.execute(delete(ContractWorkbook).where(ContractWorkbook.source_key == KEY))
    db.commit()


def test_only_source_values_not_seeded_ledgers(db, source_only, client, login):
    wb, p = source_only
    assert db.scalar(select(func.count()).select_from(BizContract)) > 0
    # Old reconciled values and links must be ignored as well as other ledgers.
    sheets = copy.deepcopy(wb.sheets)
    sheets[0]["rows"][-1]["record"] = {"amount": "99999999", "received": "8888888"}
    sheets[0]["rows"][-1]["target"] = {"id": 1}
    wb.sheets = [*sheets, older()]
    db.commit()
    for view in ("contracts", "collections"):
        response = client.get(f"{BASE}/{view}", headers=login("admin"))
        result = data(response)
        assert response.headers["cache-control"] == "no-store"
        assert result["total"] == 1
        item = result["items"][0]
        assert (item["name"], item["amount"], item["received"], item["outstanding"]) == ("唯一原表合同", "100.00", "30.00", "65.00")
        assert "target" not in item and "business_line" not in item
        assert any("之和不同" in issue for issue in item["issues"])
    board = data(client.get(f"{BASE}/board", headers=login("admin")))
    assert board["sheets"][0]["summary"]["amount"]["value"] == "100.00"
    for view in ("opportunities", "tender"):
        result = data(client.get(f"{BASE}/{view}", headers=login("admin")))
        assert result["active"] and not result["items"]  # Never use seeded opportunities/projects.


def test_missing_zero_text_negative_and_precision():
    rows = [sales_row(3, "空白合同", "客户", None), sales_row(4, "零额合同", "客户", 0),
            sales_row(5, "框架合同", "客户", "按月结算"), sales_row(6, "精确金额", "客户", 1069803.854),
            sales_row(7, "冲减", "客户", -25)]
    sheet = svc.Sheet(sales(*rows), "sales")
    assert [r["amount"] for r in sheet.items] == [None, "0.00", None, "1069803.854", "-25.00"]
    summary = sheet.summary()
    assert summary["count"] == 5 and summary["amount"]["numeric_count"] == 3
    assert summary["amount"]["value"] == "1069778.854"
    assert sheet.items[2]["amount_text"] == "按月结算"
    assert all(r["outstanding"] is None for r in sheet.items)
    assert svc.number(True) is None and svc.number("#VALUE!") is None
    assert svc.number("1.25万元") == Decimal("12500.00")


def test_tender_second_lot_and_total_preserve_precision():
    raw = tender()
    raw["rows"][1]["cells"][10] = 731101
    raw["rows"][2]["cells"][10] = 287118.5
    raw["rows"].extend([
        {"row": 4, "cells": [2, "负责人", "第二项目", "待开标", "待定", None, None, "移动", "甲", 1126109.32, 1069803.854]},
        {"row": 5, "cells": [None] * 10 + [{"value": 2088023.354, "formula": "=SUM(K2:K4)"}]},
    ])
    sheet = svc.Sheet(raw, "tender")
    assert (len(sheet.items), sheet.summary()["groups"]) == (3, 2)
    assert sheet.items[1]["name"] == "验收招标项目"
    assert sheet.summary()["amount"]["value"] == "2088023.354"
    assert sheet.controls() == [{"field": "amount", "label": "我方合同金额预估\n（减5个点）", "cell": "K5",
                                 "source": "2088023.354", "calculated": "2088023.354", "difference": "0.000", "matches": True}]


def test_merged_receipts_count_anchor_once_and_keep_child_cells():
    raw = sales(sales_row(3, "甲合同", "客户", 100, received=80), sales_row(4, "乙合同", "客户", 200))
    raw["merges"] = ["I3:I4"]
    sheet = svc.Sheet(raw, "sales")
    assert sheet.summary()["received"]["value"] == "80.00"
    assert sheet.items[1]["received"] is None
    assert sheet.items[1]["received_text"] == "合并金额，见 I3"
    cell = sheet.cell(4, 9)
    assert cell["value"] is None and cell["text"] == "80" and cell["inherited_from"] == "I3"


def test_source_add_edit_remove_move_and_no_ledger_writes(db, source_only, monkeypatch):
    wb, p = source_only
    before = [db.scalar(select(func.count()).select_from(m)) for m in (BizContract, BizOpportunity, BizPayment)]
    current = changing_source(monkeypatch, [sales(sales_row(3, "新增甲", "甲客户", 10), sales_row(4, "新增乙", "乙客户", 20))])
    current[0] = "2"
    assert sync.sync(db, KEY)["changed"]
    assert svc.page(db, p, "contracts")["total"] == 2
    # Delete one, move and rename the other, lower its amount: no stale identity
    # or old receipt can prevent the source truth from being presented.
    current[:] = ["3", [sales(sales_row(18, "改名且移行", "新客户", 5, received=1))]]
    sync.sync(db, KEY)
    result = svc.page(db, p, "contracts")
    assert result["total"] == 1
    assert (result["items"][0]["row"], result["items"][0]["name"], result["summary"]["amount"]["value"]) == (18, "改名且移行", "5.00")
    assert sync.sync(db, KEY)["changed"] is False
    assert [db.scalar(select(func.count()).select_from(m)) for m in (BizContract, BizOpportunity, BizPayment)] == before


def test_read_error_and_header_drift_keep_complete_snapshot(db, source_only, monkeypatch):
    wb, p = source_only
    before = copy.deepcopy(wb.sheets)
    raw = sales(sales_row(3, "不应发布", "客户", 99999))
    raw["rows"][0]["cells"][5] = "意外移动金额列"
    changing_source(monkeypatch, [raw])[0] = "2"
    with pytest.raises(BizError, match="表头变化"):
        sync.sync(db, KEY)
    assert wb.sheets == before and wb.revision == "1" and wb.last_error
    assert svc.page(db, p, "contracts")["source"]["stale"]
    def unauthorized(*args):
        raise BizError(40301, "机器人授权过期")
    monkeypatch.setattr(sync.source, "fetch_workbook", unauthorized)
    with pytest.raises(BizError, match="机器人授权过期"):
        sync.sync(db, KEY)
    assert svc.page(db, p, "contracts")["items"][0]["amount"] == "100.00"


def test_permissions_revision_guard_and_no_fallback(db, source_only, client, login):
    wb, p = source_only
    assert client.get(f"{BASE}/contracts", headers=login("zhangzhenhua")).status_code == 403
    response = client.get(f"{BASE}/contracts/rows/sales-sheet/3", params={"revision": "1"}, headers=login("admin"))
    row = data(response)
    assert row["rows"][0]["cells"][5]["address"] == "F3"
    assert client.get(f"{BASE}/contracts/rows/sales-sheet/3", params={"revision": "old"}, headers=login("admin")).status_code == 409
    assert client.get(f"{BASE}/tender/rows/sales-sheet/3", params={"revision": "1"}, headers=login("admin")).status_code == 404
    wb.config = {**wb.config, "enabled": False}
    db.commit()
    assert data(client.get(f"{BASE}/contracts", headers=login("admin")))["active"] is True
    wb.sheets = []
    db.commit()
    assert client.get(f"{BASE}/contracts", headers=login("admin")).status_code == 400


def test_pagination_search_year_and_full_source_view(db, source_only):
    wb, p = source_only
    raw = sales(sales_row(3, "甲合同", "客户甲", 100), sales_row(4, "乙框架", "客户乙", None),
                sales_row(5, "丙合同", "客户丙", 50), sales_row(6, "丁合同", "客户丁", 50))
    raw["rows"][3]["cells"][7] = "待定"
    raw["rows"].append({"row": 8, "cells": ["合计", None, None, None, None, 200]})
    wb.sheets = [raw]
    db.commit()
    result = svc.page(db, p, "contracts", keyword="乙框架", year="undated", size=1)
    assert result["total"] == 1 and result["summary"]["amount"]["value"] is None
    assert svc.page(db, p, "contracts", year="2026")["total"] == 3
    assert svc.page(db, p, "contracts", page=99, size=1)["page"] == 3
    with pytest.raises(BizError, match="逐格原值"):
        svc.page(db, p, "contracts", raw=True)
    assert svc.page(db, p, "contracts")["sheet"]["controls"][0]["matches"]


def test_no_source_config_legacy_mode_is_explicit(db, client, login):
    db.execute(delete(SysDict).where(SysDict.dict_type == svc.CONFIG))
    db.commit()
    assert data(client.get(f"{BASE}/contracts", headers=login("admin"))) == {"active": False}


def test_pinned_document_does_not_fall_back(db, source_only):
    wb, p = source_only
    setting = dict_service.put(db, svc.CONFIG, "primary", {"enabled": True, "source_key": KEY, "docid": "original-doc"})
    db.commit()
    with pytest.raises(BizError, match="标识不一致"):
        svc.page(db, p, "contracts")


def test_reconfigure_preserves_source_only_mode(db, source_only, monkeypatch):
    wb, p = source_only
    wb.config = {**wb.config, "docid": "e3_only", "binding_id": "binding"}
    db.commit()
    monkeypatch.setattr(sync.cli, "_owned", lambda *args: {"binding_id": "binding"})
    sync.configure(db, KEY, title="仍是原表", url=wb.config["url"], reader_id=p.id,
                   auto_apply=True, auto_receipts=True)
    assert wb.config["presentation_only"]
    assert not wb.config["auto_apply"] and not wb.config["auto_receipts"]


def test_dashboard_is_module_aggregate_and_preserves_other_blocks(db, source_only, client, login, monkeypatch):
    from app.services import board_service as board
    wb, p = source_only
    monkeypatch.setenv("FAKE_TODAY", "2026-11-16")
    raw = sales(sales_row(3, "本月合同", "客户", 300), sales_row(4, "年度合同", "客户", 100))
    raw["rows"][-2]["cells"][7] = "2026-11-04"
    raw["rows"][-1]["cells"][7] = "2026-02-02"
    wb.sheets = [raw, tender()]
    db.commit()
    ov = data(client.get("/api/v1/board/overview?period=2026-11", headers=login("admin")))
    for key in ("today", "week", "month", "year"):
        drill = ov["sign"][key]["drill"]["query"]
        rows = data(client.get(f"{BASE}/contracts", params=drill, headers=login("admin")))
        assert rows["total"] == ov["sign"][key]["count"]
        assert Decimal(rows["summary"]["amount"]["value"] or 0) == Decimal(ov["sign"][key]["amount"])
    assert ov["sign"]["year"]["amount"] == "400.00"
    assert ov["tender"]["bidding_count"] == 1
    assert ov["tender"]["bidding_amount"] == "285.00"  # Both lots.
    assert ov["payment"]["amount"] is None  # No implicit ledger fallback.
    assert ov["opportunity"]["month"]["count"] == 0
    from app.core.response import encode
    assert ov["key_projects"] == encode(board._key_projects(db, date(2026, 11, 16), p))
    assert ov["submission"]["weekly"]["required"] == board._submission_block(db, date(2026, 11, 16))["weekly"]["required"]
    assert Decimal(ov["profit"]["gross_profit"]) == board._profit_block(db, date(2026, 11, 16))["gross_profit"]
    history = data(client.get("/api/v1/board/monthly?year=2026", headers=login("admin")))
    assert Decimal(history["totals"]["sign_amount"]) == 400
    assert history["months"][10]["pay_amount"] is None
    assert client.get("/api/v1/board/overview", headers=login("zhangzhenhua")).status_code == 403


def test_actual_monthly_receipts_reconcile_and_forecasts_are_not_cash(db, source_only, monkeypatch):
    from app.services import workbook_metrics as metrics, workbook_board as board
    wb, p = source_only
    raw = sales(sales_row(3, "实际回款", "客户", 1000), sales_row(4, "预计回款", "客户", 500))
    raw["rows"][0]["cells"][11] = "11月回款进度"
    raw["rows"][-2]["cells"][11] = "11月10日已完成本月回款300.55元\n11-13"
    raw["rows"][-1]["cells"][11] = "预计11月回款500元，尚未收到"
    raw["rows"].append({"row": 8, "cells": [None] * 11 + [300.55]})
    wb.sheets = [raw, older()]
    db.commit()
    pay = board.payment(db, wb, date(2026, 11, 16))
    rows = svc.page(db, p, "collections", basis="monthly", period="2026-11")
    assert pay["amount"] == Decimal(rows["summary"]["received"]["value"]) == Decimal("300.55")
    assert rows["total"] == 1 and rows["items"][0]["receipt_cell"] == "L3"
    assert pay["daily"]["actual"][8] == 0 and pay["daily"]["actual"][9] == Decimal("300.55")
    assert metrics.collection_metrics(wb, "2026-10")["amount"] is None
    # Editing a total without matching source evidence must not publish an
    # invented receipt or report a partial sum as a complete monthly value.
    changed = copy.deepcopy(raw)
    changed["rows"][-1]["cells"][11] = 500
    wb.sheets = [changed, older()]
    db.commit()
    assert board.payment(db, wb, date(2026, 11, 16))["amount"] is None


def test_opportunity_new_date_is_source_date_not_import_time(db, source_only):
    from app.services import workbook_board as board
    wb, p = source_only
    raw = {"id": "market", "name": "5、市场商机", "range": "A1:I10", "merges": [], "rows": [
        {"row": 1, "cells": ["市场商机"]},
        {"row": 2, "cells": ["工单编号", "工单模板", "联系人", "责任人", "协同人", "工单状态", "工单主题", "发起时间", "描述"]},
        {"row": 4, "cells": ["id-1", "模板", "客户", "甲", "乙", "已接受", "旧商机", "2025-01-03", ""]},
        {"row": 5, "cells": ["id-2", "模板", "客户", "甲", "乙", "已接受", "本月商机", "2026-11-10", ""]}]}
    wb.sheets = [raw]
    db.commit()
    result = board.opportunities(wb, date(2026, 11, 16))
    assert result["month"]["count"] == 1
    assert result["month"]["amount"] is None
    assert result["funnel"][0]["count"] == 2
    drill = result["month"]["drill"]["query"]
    assert svc.page(db, p, "opportunities", sheet_id=drill["sheet"], start=drill["start"], end=drill["end"])["total"] == 1


@pytest.fixture
def market_source(db, source_only):
    wb, _ = source_only
    stamp = today().isoformat()
    old = (today().replace(day=1) - timedelta(days=1)).isoformat()
    serial = to_excel(datetime.combine(today(), time(16, 12, 34)))
    raw = {"id": "market", "name": "5、市场商机", "range": "A1:I9", "merges": [], "rows": [
        {"row": 1, "cells": ["市场商机"]},
        {"row": 2, "cells": ["工单编号", "工单模板", "联系人", "责任人", "协同人", "工单状态", "工单主题", "发起时间", "描述"]},
        {"row": 3, "cells": ["MT-01", "市场商机", "原表联系人", "原表责任人", "协同成员", "已接受",
                             {"rich_string": [{"value": "校园"}, {"value": "设备"}]}, serial, "=100+200"]},
        {"row": 4, "cells": ["MT-02", "市场商机", "另一个联系人", "另一个责任人", None, "待处理",
                             "历史设备", old, ""]},
        {"row": 5, "cells": ["MT-03", "市场商机", "原表联系人", "原表责任人", None, None,
                             "缺日期", None, ""]},
        {"row": 6, "cells": [None, "市场商机", "空行联系人", "空行责任人", None, "空行状态",
                             "非工单行", stamp, ""]}]}
    decoy = copy.deepcopy(raw)
    decoy.update(id="wrong-market", name="5、另一份商机")
    decoy["rows"][2]["cells"][3:7] = ["不在市场商机内", None, "旧阶段", "混入项目"]
    wb.sheets = [*wb.sheets, decoy, raw]
    db.commit()
    return wb, raw, decoy


def test_market_scope_period_and_filters_use_only_real_work_orders(db, market_source, client, login):
    from app.services import workbook_board

    wb, raw, decoy = market_source
    headers = login("admin")

    def listed(**params):
        return data(client.get(f"{BASE}/opportunities", params=params, headers=headers))

    result = listed(sheet="all")
    assert [s["name"] for s in result["sheets"]] == ["5、市场商机"]
    assert result["total"] == result["stats"]["total"] == 3
    assert {key: set(values) for key, values in result["options"].items()} == {
        "status": {"已接受", "待处理"}, "owner": {"另一个责任人", "原表责任人"},
        "contact": {"另一个联系人", "原表联系人"}}
    assert [r["sequence"] for r in result["items"]] == ["MT-01", "MT-02", "MT-03"]
    row = result["items"][0]
    assert (row["name"], row["template"], row["collaborator"], row["date"]) == (
        "校园设备", "市场商机", "协同成员", f"{today()} 16:12:34")
    assert "amount" not in row and "stage" not in row
    assert listed(keyword="校园", status="已接受", contact="原表联系人", owner="原表责任人")["total"] == 1
    assert listed(owner="不在市场商机内")["total"] == listed(status="赢单")["total"] == 0
    assert listed(start=str(today()), end=str(today()))["total"] == 1
    assert listed(page=2, size=1)["items"][0]["sequence"] == "MT-02"
    for key in ("month", "week"):
        drill = result["stats"][key]
        assert drill["count"] == listed(**drill["drill"]["query"])["total"] == 1 and drill["amount"] is None
    board = workbook_board.opportunities(wb, today())
    assert board["month"] == result["stats"]["month"]
    assert [(s["stage"], s["count"]) for s in board["funnel"]] == [("已接受", 1), ("待处理", 1)]
    assert client.get(f"{BASE}/opportunities", params={"sheet": decoy["id"]}, headers=headers).status_code == 404
    for sheet, row_number in ((decoy["id"], 3), (raw["id"], 6)):
        assert client.get(f"{BASE}/opportunities/rows/{sheet}/{row_number}",
                          params={"revision": wb.revision}, headers=headers).status_code == 404
    wb.sheets = [decoy]
    db.commit()
    unavailable = listed()
    assert unavailable["warning"] and not unavailable["items"] and not unavailable["options"]["owner"]


def test_market_export_matches_list_and_respects_scope(market_source, client, login):
    wb, raw, _ = market_source
    headers = login("admin")
    params = {"sheet": raw["id"], "revision": wb.revision, "owner": "原表责任人", "status": "已接受"}
    shown = data(client.get(f"{BASE}/opportunities", params=params, headers=headers))
    response = client.get(f"{BASE}/opportunities/export", params=params, headers=headers)
    assert response.status_code == 200
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    book = load_workbook(BytesIO(response.content), read_only=True)
    assert book.sheetnames == ["5、市场商机", "口径说明"]
    sheet = book["5、市场商机"]
    header, record = tuple(sheet.values)
    assert header == ("工单编号", "工单模板", "联系人", "责任人", "协同人", "工单状态", "工单主题", "发起时间", "描述")
    assert record == tuple(shown["items"][0][key] for key in (
        "sequence", "template", "contact", "owner", "collaborator", "progress", "name", "date", "note"))
    assert sheet["I2"].data_type == "s"
    book.close()
    path = f"{BASE}/opportunities/export"
    assert client.get(path, params={**params, "revision": "obsolete"}, headers=headers).status_code == 409
    assert client.get(path, params={**params, "sheet": "wrong-market"}, headers=headers).status_code == 404
    assert client.get(path, params=params, headers=login("zhangzhenhua")).status_code == 403


def test_portal_and_mcp_obey_same_source_permissions(db, source_only):
    from app.api.portal import Ctx
    from app.mcp_server.server import t_compare, t_executive_daily
    wb, admin = source_only
    other = build_principal(db.scalar(select(SysUser).where(SysUser.wecom_userid == "zhangzhenhua")))
    assert "sign" not in Ctx(db, other).board
    assert "submission" in Ctx(db, other).board
    for fn in (t_compare, t_executive_daily):
        with pytest.raises(BizError) as error:
            fn(db, other)
        assert error.value.code == 40301
