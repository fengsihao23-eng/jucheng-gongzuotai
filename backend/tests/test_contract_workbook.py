"""Source revision, ledger reconciliation, scheduled updates, and access control."""
import copy
import io
from datetime import date
from decimal import Decimal

import pytest
from openpyxl import Workbook, load_workbook
from sqlalchemy import delete, func, select

from app.core.db import SessionLocal
from app.core.deps import build_principal
from app.core.response import BizError
from app.jobs.contracts import sync_workbooks
from app.models import BizContract, BizOpportunity, BizPayment, ContractWorkbook, ContractWorkbookLink, FinReceipt, SysUser
from app.services import (business_workbook, contract_workbook_layout as layout, contract_workbook_source as source,
                          contract_workbook_sync as sync)
from tests.conftest import data

BASE = "/api/v1/contracts/workbooks"
KEY = "test-contract-workbook"


@pytest.fixture
def fixed(db, monkeypatch):
    admin = db.scalar(select(SysUser).where(SysUser.wecom_userid == "admin"))
    executive = db.scalar(select(SysUser).where(SysUser.wecom_userid == "zhangzhenhua"))
    monkeypatch.setattr(sync.cli, "_owned", lambda *args: {"binding_id": "a" * 32})
    row = sync.configure(db, KEY, title="验收合同台账", url="https://doc.weixin.qq.com/sheet/e3_source",
                         reader_id=admin.id, view_department_ids=[executive.department_id], auto_apply=True)
    db.commit()
    yield row
    links = list(db.scalars(select(ContractWorkbookLink).where(ContractWorkbookLink.workbook_id == row.id)))
    for kind, model in (("contract", BizContract), ("opportunity", BizOpportunity)):
        ids = [l.target_id for l in links if l.kind == kind]
        if kind == "contract":
            db.execute(delete(FinReceipt).where(FinReceipt.contract_id.in_(ids)))
            db.execute(delete(BizPayment).where(BizPayment.contract_id.in_(ids)))
        db.execute(delete(model).where(model.id.in_(ids)))
    db.execute(delete(ContractWorkbook).where(ContractWorkbook.id == row.id))
    db.commit()


def sales_row(n, title, customer, amount, *, raw_code="", received=None):
    values = [n, title, customer, "", "示例甲方", amount, "公司分配", "2025-03-05",
              received, None, None, None, None, None, None,
              {"rich_string": [{"value": "合同原件", "link": "https://drive.weixin.qq.com/s?k=sample"}]},
              None, None, "阳春", raw_code]
    return {"row": n, "cells": values}


def sales(*rows):
    cols = ["序号", "项目/合同名称", "对接单位", "实际跟进人", "我方签约主体", "合同总金额", "项目性质", "签订时间",
            "应收", "", "", "", "", "已收款时间", "备注", "文件", "归档时间", "业绩金额", "所属区域业绩", "合同编号"]
    return {"id": "sales-sheet", "name": "1、2026年销售合同", "range": "A1:T30", "merges": [],
            "rows": [{"row": 1, "cells": cols}, {"row": 2, "cells": [None] * 8 + ["已收", "未收"]}, *rows]}


def older(*records, profile="older"):
    return {"id": profile, "name": "2、2026年前（已签单个项目+未回款）" if profile == "older" else
            "6、市场销售长效合同（已签运营类+持续回款）", "range": "A1:P30", "merges": [], "rows": [
                {"row": 1, "cells": ["序号", "项目名称", "对接单位" if profile == "older" else "挂靠单位", "项目负责人",
                                     "实际跟进人", "我方签约主体", "合同总金额", "签订时间", "应收", "", "", "", "", "", "", ""]},
                {"row": 2, "cells": [None] * 8 + ["已收", "未收"]},
                *[{"row": n, "cells": [n - 2, name, "历史客户", "负责人", "跟进甲", "主体", amount, when,
                                        received, outstanding, None, None, None, None, None, None]}
                  for n, name, amount, when, received, outstanding in records]]}


def tender():
    return {"id": "tender-sheet", "name": "4、招标状态项目", "range": "A1:K10", "merges": ["C2:C3"],
            "rows": [
                {"row": 1, "cells": ["序号", "责任人", "招标项目", "进度", "开标时间", "更新时间", "备注", "运营商",
                                      "公司主体", "中标金额预估", "我方合同金额预估\n（减5个点）"]},
                {"row": 2, "cells": [1, None, "验收招标项目", "跟进", None, None, None, "示例运营商", "待定", 100, 95]},
                {"row": 3, "cells": [None] * 9 + [200, 190]},
            ]}


def changing_source(monkeypatch, tabs):
    current = ["1", tabs]

    def fetch(config, previous):
        return current[0], None if previous == current[0] else copy.deepcopy(current[1])

    monkeypatch.setattr(source, "fetch_workbook", fetch)
    return current


def test_schedule_to_ui_and_back(db, fixed, monkeypatch, client, login):
    current = changing_source(monkeypatch, [sales(sales_row(3, "验收合同一", "甲学校", 110, raw_code="ABC-1", received=50),
                                                   sales_row(4, "验收合同一", "乙学校", 120, received=20)), tender()])
    result = sync_workbooks(db, {})
    assert result["status"] == "success" and result["rows"] == 7
    first = data(client.get(f"{BASE}/{KEY}", headers=login("admin")))
    assert first["summary"]["created"] == 3
    assert first["summary"]["payment_diff"] == 2
    assert first["summary"]["sheets"] == 2
    assert client.get(f"{BASE}/{KEY}", headers=login("songjia")).status_code == 404
    assert data(client.get(BASE, headers=login("songjia")))["items"] == []
    assert data(client.get(f"{BASE}/{KEY}", headers=login("zhangzhenhua")))["summary"]["created"] == 3
    assert client.post(f"{BASE}/{KEY}/sync", headers=login("zhangzhenhua")).status_code == 403

    rows = data(client.get(f"{BASE}/{KEY}/rows", params={"sheet": "sales-sheet", "state": "business"}, headers=login("admin")))
    cid = rows["items"][0]["target"]["id"]
    assert rows["items"][0]["receipts"]["difference"] == "50.00"
    assert rows["items"][1]["target"]["id"] != cid
    c = data(client.get(f"/api/v1/contracts/{cid}", headers=login("admin")))
    assert c["workbook_sources"][0]["row"] == 3
    assert not c["payments"]
    assert data(client.get(f"/api/v1/contracts/{cid}", headers=login("songjia")))["workbook_sources"] == []
    export = client.get(f"{BASE}/{KEY}/export", headers=login("admin"))
    wb = load_workbook(io.BytesIO(export.content))
    assert wb.sheetnames == ["1、2026年销售合同", "4、招标状态项目"]
    assert wb.worksheets[0]["F3"].value == 110
    assert wb.worksheets[0]["P3"].hyperlink.target == "https://drive.weixin.qq.com/s?k=sample"
    assert sync_workbooks(db, {})["rows"] == 0

    current[:] = ["2", [sales(sales_row(3, "验收合同一", "乙学校", 120, received=20),
                              sales_row(17, "改名后的验收合同", "甲学校", 130, raw_code="ABC-1", received=70)), tender()]]
    assert sync_workbooks(db, {})["status"] == "success"
    second = data(client.get(f"/api/v1/contracts/{cid}", headers=login("admin")))
    assert (second["name"], second["amount"], second["workbook_sources"][0]["row"]) == ("改名后的验收合同", "130.00", 17)
    assert second["workbook_sources"][0]["data"]["receipts"]["difference"] == "70.00"
    assert len(second["payments"]) == 0

    current[:] = ["3", [sales(sales_row(3, "验收合同一", "乙学校", 120, received=20)), tender()]]
    assert sync_workbooks(db, {})["status"] == "success"
    gone = data(client.get(f"/api/v1/contracts/{cid}", headers=login("admin")))
    assert gone["workbook_sources"][0]["present"] is False
    assert gone["amount"] == "130.00"


def test_failed_read_keeps_previous_snapshot_and_manual_changes(db, fixed, monkeypatch):
    current = changing_source(monkeypatch, [sales(sales_row(3, "失败保护", "示例客户", 55, raw_code="ABC-2"))])
    sync.sync(db, KEY)
    cid = db.scalar(select(ContractWorkbookLink.target_id).where(ContractWorkbookLink.workbook_id == fixed.id))
    c = db.get(BizContract, cid)
    c.amount = Decimal("56")
    db.commit()
    current[:] = ["2", [sales(sales_row(3, "失败保护", "示例客户", 77, raw_code="ABC-2"))]]
    result = sync.sync(db, KEY)
    assert result["summary"]["review"] == 1
    assert db.get(BizContract, cid).amount == Decimal("56")

    def fail(*args):
        raise BizError(40301, "企微授权已过期")

    monkeypatch.setattr(source, "fetch_workbook", fail)
    assert sync_workbooks(db, {})["status"] == "failed"
    db.refresh(fixed)
    assert fixed.revision == "2" and fixed.sheets[0]["rows"][-1]["record"]["amount"] == "77.00"
    assert fixed.last_error == "企微授权已过期"


def test_official_authorization_channel_is_retained_and_scoped(db, fixed, monkeypatch):
    current = changing_source(monkeypatch, [sales(sales_row(3, "授权入口测试", "示例客户", 66))])
    sync.sync(db, KEY)
    official = "https://work.weixin.qq.com/ai/aiHelper/authorizationList?from=workbench&a=2"

    def expired(*args):
        raise BizError(40301, "企业微信机器人「文档」使用权限已过期", data={
            "authorization_url": official, "authorization_label": "重新授权文档",
        })

    monkeypatch.setattr(source, "fetch_workbook", expired)
    with pytest.raises(BizError, match="使用权限已过期"):
        sync.sync(db, KEY)
    db.refresh(fixed)
    assert (fixed.authorization_url, fixed.authorization_label, fixed.authorization_required) == (
        official, "重新授权文档", True,
    )

    owner = build_principal(db.scalar(select(SysUser).where(SysUser.wecom_userid == "admin")))
    viewer = build_principal(db.scalar(select(SysUser).where(SysUser.wecom_userid == "zhangzhenhua")))
    assert business_workbook.source_info(fixed, owner)["authorization_url"] == official
    assert sync.describe(fixed, owner)["authorization_url"] == official
    assert "authorization_url" not in business_workbook.source_info(fixed, viewer)
    assert "authorization_url" not in sync.describe(fixed, viewer)

    monkeypatch.setattr(source, "fetch_workbook", lambda *args: (current[0], None))
    assert sync.sync(db, KEY)["changed"] is False
    db.refresh(fixed)
    assert fixed.last_error is None and fixed.authorization_required is False
    assert fixed.authorization_url == official  # Keep the channel available for the next seven-day renewal.

    monkeypatch.setattr(sync.cli, "_owned", lambda *args: {"binding_id": "b" * 32})
    sync.configure(db, KEY, title=fixed.config["title"], url=fixed.config["url"], reader_id=owner.id,
                   view_department_ids=fixed.config["view_department_ids"], auto_apply=True)
    db.commit()
    assert fixed.authorization_url is None and fixed.authorization_required is False and fixed.last_error is None


def test_non_official_authorization_url_is_never_saved(db, fixed, monkeypatch):
    def denied(*args):
        raise BizError(40301, "企微授权已过期", data={
            "authorization_url": "https://work.weixin.qq.com.evil.example/steal",
            "authorization_label": "重新授权",
        })

    monkeypatch.setattr(source, "fetch_workbook", denied)
    with pytest.raises(BizError, match="企微授权已过期"):
        sync.sync(db, KEY)
    db.refresh(fixed)
    assert fixed.authorization_url is None and fixed.authorization_required is False


def test_failed_sync_cannot_publish_authorization_after_rebinding(db, fixed, monkeypatch):
    config = fixed.config

    def expired(*args):
        raise BizError(40301, "企微授权已过期", data={
            "authorization_url": "https://work.weixin.qq.com/ai/aiHelper/authorizationList?from=chat&a=2",
        })

    rollback = db.rollback

    def rebind_after_rollback():
        rollback()
        with SessionLocal() as edit:
            sync.configure(edit, KEY, title=config["title"], url=config["url"],
                           reader_id=config["reader_id"], auto_apply=True)
            edit.commit()

    monkeypatch.setattr(source, "fetch_workbook", expired)
    monkeypatch.setattr(sync.cli, "_owned", lambda *args: {"binding_id": "b" * 32})
    with monkeypatch.context() as during_sync:
        during_sync.setattr(db, "rollback", rebind_after_rollback)
        with pytest.raises(BizError, match="企微授权已过期"):
            sync.sync(db, KEY)
    db.refresh(fixed)
    assert fixed.config["binding_id"] == "b" * 32
    assert fixed.last_error is None and fixed.authorization_url is None


def test_missing_source_identity_does_not_create_duplicate(db, fixed, monkeypatch):
    current = changing_source(monkeypatch, [sales(sales_row(3, "源表原名称", "示例客户", 88))])
    assert sync.sync(db, KEY)["summary"]["created"] == 1
    current[:] = ["2", [sales(sales_row(3, "整条替换的新名称", "新客户", 99))]]
    result = sync.sync(db, KEY)
    assert result["summary"]["unresolved"] == 1
    assert result["summary"]["removed"] == 1
    assert db.scalar(select(func.count()).select_from(ContractWorkbookLink).where(
        ContractWorkbookLink.workbook_id == fixed.id)) == 1
    current[0] = "3"
    assert sync.sync(db, KEY)["summary"]["unresolved"] == 1


def test_review_link_requires_current_revision_and_owner(db, fixed, monkeypatch, client, login):
    changing_source(monkeypatch, [sales(sales_row(3, "人工核对合同", "示例客户", None, raw_code="CHECK-1"))])
    sync.sync(db, KEY)
    obj = BizContract(contract_no="WB-REVIEW-001", name="人工核对合同", customer_name="示例客户",
                      amount=Decimal("15"), sign_date=date(2025, 3, 5), business_line="TD")
    db.add(obj)
    db.commit()
    body = {"sheet_id": "sales-sheet", "row_number": 3, "number": obj.contract_no, "revision": "1"}
    path = f"{BASE}/{KEY}/link"
    assert client.post(path, headers=login("zhangzhenhua"), json=body).status_code == 403
    assert client.post(path, headers=login("admin"), json={**body, "revision": "0"}).status_code == 409
    assert data(client.post(path, headers=login("admin"), json=body)) == {"linked": True}
    row = data(client.get(f"{BASE}/{KEY}/rows", params={"sheet": "sales-sheet", "source_row": 3},
                          headers=login("admin")))["items"][-1]
    assert row["target"]["id"] == obj.id and row["match"] == "人工核对"
    assert data(client.get(f"/api/v1/contracts/{obj.id}", headers=login("admin")))["amount"] == "15.00"


def test_explicit_receipts_sync_without_repeating_or_guessing_dates(db, fixed, monkeypatch):
    fixed.config = {**fixed.config, "auto_receipts": True}
    db.commit()
    row = sales_row(3, "逐笔回款验收", "示例客户", 150, received=50, raw_code="PAY-CHECK")
    row["cells"][13] = "2025-03-06"
    current = changing_source(monkeypatch, [sales(row)])
    result = sync.sync(db, KEY)
    assert result["summary"]["new_payments"] == 1
    link = db.scalar(select(ContractWorkbookLink).where(ContractWorkbookLink.workbook_id == fixed.id))
    cid = link.target_id
    first = list(db.scalars(select(BizPayment).where(BizPayment.contract_id == cid)))
    assert len(first) == 1 and first[0].payment_date == date(2025, 3, 6)
    assert db.scalar(select(func.count()).select_from(FinReceipt).where(FinReceipt.contract_id == cid)) == 1
    assert result["summary"].get("payment_diff", 0) == 0
    current[0] = "2"
    sync.sync(db, KEY)
    assert db.scalar(select(func.count()).select_from(BizPayment).where(BizPayment.contract_id == cid)) == 1

    # A later cumulative amount and a date alone cannot establish the date
    # of an incremental receipt.
    row["cells"][8] = 120
    row["cells"][13] = "2025-03-10"
    current[:] = ["3", [sales(row)]]
    assert sync.sync(db, KEY)["summary"]["payment_diff"] == 1
    assert db.scalar(select(func.count()).select_from(BizPayment).where(BizPayment.contract_id == cid)) == 1
    row["cells"][14] = "2025年3月6日回款50元；2025年3月10日已收到70元"
    from app.services import board_service
    original_items = board_service.dict_service.items
    def older_dictionary(db, kind):
        items = original_items(db, kind)
        return {k: v for k, v in items.items() if k != "TD"} if kind == "business_line" else items
    monkeypatch.setattr(board_service.dict_service, "items", older_dictionary)
    prime = board_service.overview(db, ref=date(2025, 3, 31))
    prime_received = prime["payment"]["amount"]
    # Simulate the scheduler being in the other API worker: its local cache
    # invalidation cannot reach this worker.
    monkeypatch.setattr(sync.contracts, "_invalidate", lambda: None)
    current[:] = ["4", [sales(row)]]
    assert sync.sync(db, KEY)["summary"]["new_payments"] == 1
    assert db.scalar(select(func.sum(BizPayment.amount)).where(BizPayment.contract_id == cid)) == Decimal("120")
    assert db.scalar(select(func.count()).select_from(FinReceipt).where(FinReceipt.contract_id == cid)) == 2
    live = board_service.overview(db, ref=date(2025, 3, 31))
    assert live["payment"]["amount"] == prime_received + Decimal("70")
    assert sum((item["amount"] for item in live["payment"]["by_line"]), Decimal(0)) == live["payment"]["amount"]
    assert next(item["name"] for item in live["payment"]["by_line"] if item["line"] == "TD") == "待定"
    assert layout.receipt_events("120.00", None, "2025年3月6日回款50元；2025年3月10日已收到69.99元") == []


def test_source_requires_complete_stable_revision():
    def fake(version_after=7, partial=False):
        versions = iter((7, version_after))

        def fetch(method, payload):
            if method == "sheet.version.get":
                return {"version": next(versions)}
            if method == "sheet.workbook.get":
                return {"sheets": [{"sheet_id": "abc", "name": "子表", "range": "A2:B4"}]}
            if method == "sheet.merge.get":
                return {"merges": ["A2:A3"]}
            return {"actual_range": "A2:B4", "cells": [["合同", {"value": 10, "formula": "5+5"}],
                                                        ["", 2], ["", ""]][:2 if partial else 3]}
        return fetch

    revision, tabs = source.read_online(fake(), "e3_demo")
    assert revision == "7"
    assert tabs[0]["rows"] == [{"row": 2, "cells": ["合同", {"value": 10, "formula": "5+5"}]},
                               {"row": 3, "cells": ["", 2]}]
    assert source.read_online(fake(), "e3_demo", "7") == ("7", None)
    with pytest.raises(BizError, match="编辑"):
        source.read_online(fake(8), "e3_demo")
    with pytest.raises(BizError, match="未完整"):
        source.read_online(fake(partial=True), "e3_demo")


def test_xlsx_keeps_values_formulas_merges_and_file_links():
    wb = Workbook()
    ws = wb.active
    ws.title = "合同"
    ws["A1"] = "合并区"
    ws.merge_cells("A1:B1")
    ws["A2"] = date(2025, 10, 18)
    ws["B2"] = "=1+2"
    ws["C2"] = "附件"
    ws["C2"].hyperlink = "https://drive.weixin.qq.com/s?k=test"
    wb.create_sheet("空子表")
    buf = io.BytesIO()
    wb.save(buf)
    tabs = source.read_xlsx(buf.getvalue())
    assert len(tabs) == 2
    assert tabs[0]["merges"] == ["A1:B1"]
    assert tabs[0]["rows"][1]["cells"] == ["2025-10-18T00:00:00", {"formula": "=1+2", "value": None},
                                            {"value": "附件", "link": "https://drive.weixin.qq.com/s?k=test"}]
    assert layout.day("2025-10-18T00:00:00") == "2025-10-18"
    assert layout.amount("待定") is None
