from datetime import date, datetime, timezone
from decimal import Decimal as D

from sqlalchemy import func, select
import pytest

from app.core.response import bad_request
from app.integration.wecom.base import ApprovalDto
from app.models import BizContract, BizPayment, BizPaymentPlan, FinReceipt, FinReceivablesSourceRow
from app.services import dict_service, dispatch_service, receivables_sync
from tests.conftest import data

BASE = "/api/v1/receivables"
DATE = "2026-10-10"


def approval(db, number, kind, values, status=2):
    template = f"test-receivables-{kind}"
    dict_service.put(db, "approval_template", template, {"key": kind, "fields": {k: k for k in values}})
    db.commit()
    stamp = datetime(2026, 10, 18, 10, tzinfo=timezone.utc)
    return dispatch_service.ingest(db, number, detail=ApprovalDto(
        sp_no=number, template_id=template, sp_name=kind, sp_status=status, apply_time=stamp,
        applicant_userid="songjia", fields=values, approved_time=stamp if status == 2 else None))


def contract(db, number, amount="1000.00", sign_on=DATE):
    c = BizContract(contract_no=number, name=number, customer_name="验收客户", amount=D(amount),
                    sign_date=date.fromisoformat(sign_on), business_line="XY", status=1, source_type=3)
    db.add(c)
    db.commit()
    return c


def post_receipt(client, headers, reference, **overrides):
    return client.post(BASE + "/receipts", headers=headers, json={
        "reference": reference, "received_on": DATE, "amount": "200.00", "payer": "验收客户",
        "account": "测试收款户", "kind": "other", **overrides,
    })


def test_contract_approval_matches_history_and_uses_actual_sign_month(client, login, db):
    h = login("zhangzhenhua")
    ov = "/api/v1/board/overview"
    period = {"period": "2026-09", "refresh": 1}
    old = contract(db, "QA-LINKED-CONTRACT", sign_on="2026-09-24")
    before = data(client.get(ov, params=period, headers=h))["sign"]["month"]["amount"]
    values = {"contract_no": old.contract_no, "amount": "1000.00", "customer_name": "验收客户", "sign_date": "2026-09-24"}
    assert approval(db, "QA-CONTRACT-LINK", "contract", values).dispatch_status == 1
    db.refresh(old)
    assert old.source_approval_no == "QA-CONTRACT-LINK" and old.source_type == 3
    assert data(client.get(ov, params=period, headers=h))["sign"]["month"]["amount"] == before
    values = {**values, "contract_no": "QA-NEW-CONTRACT", "business_line": "XY"}
    approval(db, "QA-CONTRACT-NEW", "contract", values, status=1)
    assert not db.scalar(select(BizContract).where(BizContract.contract_no == values["contract_no"]))
    assert approval(db, "QA-CONTRACT-NEW", "contract", values).dispatch_status == 1
    assert approval(db, "QA-CONTRACT-NEW", "contract", values).dispatch_status == 1
    assert D(data(client.get(ov, params=period, headers=h))["sign"]["month"]["amount"]) - D(before) == 1000
    changed = approval(db, "QA-CONTRACT-NEW", "contract", {**values, "amount": "1234.00"})
    assert changed.dispatch_status == 2 and changed.dispatched_at
    assert db.scalar(select(BizContract.amount).where(BizContract.contract_no == values["contract_no"])) == 1000


def test_invoice_approval_receipt_partial_allocation_and_reversal(client, login, db):
    h, leader = login("chenjing"), login("zhangzhenhua")
    c = contract(db, "QA-CASH-CYCLE")
    db.add(BizPaymentPlan(contract_id=c.id, seq=1, stage_name="首期", amount=D(600), due_date=date(2026, 10, 11)))
    db.commit()
    values = {"customer": "验收客户", "amount": "600.00", "contract_no": c.contract_no}
    invoice = approval(db, "QA-INVOICE", "invoice", values)
    ident = int(invoice.dispatch_target)
    assert approval(db, "QA-INVOICE", "invoice", values).dispatch_target == str(ident)
    before = data(client.get(BASE + "/summary", headers=leader))
    pending = data(client.get(BASE + "/invoices", headers=leader, params={"keyword": "QA-CASH-CYCLE"}))["items"]
    assert pending[0]["state"] == "pending" and D(pending[0]["net"]) == 0
    body = {"invoice_no": "QA-INVOICE-600", "amount": "600.00", "issued_on": DATE}
    assert client.post(f"{BASE}/invoices/{ident}/issue", headers=leader, json=body).status_code == 403
    data(client.post(f"{BASE}/invoices/{ident}/issue", headers=h, json=body))
    receipt = data(post_receipt(client, h, "QA-CASH-200", kind="contract", contract_no=c.contract_no))
    assert data(post_receipt(client, h, "qa-cash-200", kind="contract", contract_no=c.contract_no)) == receipt
    assert post_receipt(client, h, "QA-CASH-200", kind="contract", contract_no=c.contract_no, amount="201").status_code == 409
    assert client.post(f"/api/v1/contracts/{c.id}/payments", headers=h,
                       json={"amount": "200", "payment_date": DATE}).status_code == 409
    allocations = f"{BASE}/receipts/{receipt['id']}/allocations"
    data(client.put(allocations, headers=h, json={"items": [{"invoice_id": ident, "amount": "200.00"}]}))
    r = data(client.get(BASE + "/invoices", headers=h, params={"state": "outstanding", "keyword": c.contract_no}))["items"][0]
    assert D(r["outstanding"]) == 400
    got = data(client.get("/api/v1/board/overview", headers=leader))["receivables"]
    assert D(got["net_issued"]) - D(before["net_issued"]) == 600
    assert D(got["contract_received"]) - D(before["contract_received"]) == 200
    assert D(got["plan_overdue_now"]) - D(before["plan_overdue_now"]) == -200
    assert client.post(f"{BASE}/invoices/{ident}/correct", headers=h,
                       json={"reduced_total": "500", "reason": "验收"}).status_code == 409
    data(client.post(f"{BASE}/receipts/{receipt['id']}/void", headers=h, json={"reason": "重复到账凭证"}))
    assert not db.get(BizPayment, receipt["payment_id"])
    assert D(data(client.get(BASE + "/summary", headers=leader))["contract_received"]) == D(before["contract_received"])
    data(client.post(f"{BASE}/invoices/{ident}/correct", headers=h, json={"reduced_total": "600", "reason": "已全额红冲"}))
    assert D(data(client.get(BASE + "/summary", headers=leader))["net_issued"]) == D(before["net_issued"])


def test_withdrawn_invoice_approval_cannot_be_issued(client, login, db):
    values = {"customer": "验收客户", "amount": "60.00"}
    a = approval(db, "QA-INVOICE-WITHDRAW", "invoice", values)
    ident = int(a.dispatch_target)
    assert approval(db, a.sp_no, "invoice", values, status=4).dispatch_status == 2
    assert client.post(f"{BASE}/invoices/{ident}/issue", headers=login("chenjing"),
                       json={"invoice_no": "QA-WITHDRAW", "amount": "60", "issued_on": DATE}).status_code == 409
    data(client.post(f"/api/v1/admin/approvals/{a.sp_no}/review", headers=login("admin"),
                     json={"reason": "已核对撤回，未实际开票"}))
    row = data(client.get(BASE + "/invoices", params={"keyword": a.sp_no}, headers=login("chenjing")))["items"][0]
    assert row["state"] == "cancelled"


def test_receipt_approval_and_pending_match_do_not_double_count(client, login, db):
    h = login("chenjing")
    c = contract(db, "QA-ASSIGN")
    values = {"reference": "QA-ASSIGN-150", "received_on": DATE, "amount": "150.00", "payer": "验收客户",
              "account": "测试收款户", "kind": "待匹配收款"}
    before = data(client.get(BASE + "/summary", headers=h))
    a = approval(db, "QA-RECEIPT-APPROVAL", "receipt", values)
    assert a.dispatch_status == 1
    pending = data(client.get(BASE + "/summary", headers=h))
    assert D(pending["other_received"]) - D(before["other_received"]) == 150
    assert client.post(f"/api/v1/contracts/{c.id}/payments", headers=h,
                       json={"amount": "150", "payment_date": DATE}).status_code == 409
    data(client.post(f"{BASE}/receipts/{a.dispatch_target}/assign", json={"contract_no": c.contract_no}, headers=h))
    assigned = data(client.get(BASE + "/summary", headers=h))
    assert D(assigned["contract_received"]) - D(before["contract_received"]) == 150
    assert assigned["total_received"] == pending["total_received"]
    assert db.scalar(select(func.count()).select_from(FinReceipt).where(FinReceipt.reference == values["reference"])) == 1


def test_legacy_receipt_link_and_csv_source_review(client, login, db):
    h = login("chenjing")
    c = contract(db, "QA-EXISTING-PAYMENT")
    pay = BizPayment(contract_id=c.id, amount=D(200), payment_date=date.fromisoformat(DATE))
    db.add(pay)
    db.commit()
    assert post_receipt(client, h, "QA-EXISTING", kind="contract", contract_no=c.contract_no).json()["data"]["payment_ids"] == [pay.id]
    before = data(client.get(BASE + "/summary", headers=h))
    content = ("reference,received_on,amount,payer,account,kind,contract_no,payment_id\n"
               f"QA-EXISTING,{DATE},200,验收客户,测试户,contract,{c.contract_no},{pay.id}\n").encode()
    path = BASE + "/sources/upload/receipt"
    assert data(client.post(path, headers=h, files={"file": ("test.csv", content)}))["changed"] == 1
    queue = data(client.get(BASE + "/sources", headers=h))["items"]
    row = next(r for r in queue if r["record_key"] == "QA-EXISTING")
    assert row["state"] == "pending" and not db.scalar(select(FinReceipt).where(FinReceipt.reference == "QA-EXISTING"))
    payload = {"items": [{"id": row["id"], "version": row["version"]}]}
    assert data(client.post(BASE + "/sources/apply-batch", headers=h, json=payload))[0]["state"] == "applied"
    assert data(client.get(BASE + "/summary", headers=h)) == before
    assert data(client.post(path, headers=h, files={"file": ("test.csv", content)}))["changed"] == 0
    receipt = db.scalar(select(FinReceipt).where(FinReceipt.reference == "QA-EXISTING"))
    assert receipt.payment_id == pay.id and not receipt.owns_payment
    assert client.get(BASE + "/sources", headers=login("zhangzhenhua")).status_code == 403


def test_fixed_source_changed_deleted_and_failed_reads_preserve_ledger(client, login, db, monkeypatch):
    from app.jobs.receivables import sync_sources

    source = "wecom:qa-source"
    values = {"reference": "QA-SOURCE-ROW", "received_on": DATE, "amount": "95.50", "payer": "验收客户",
              "account": "测试户", "kind": "other"}
    from app.models import SysUser
    reader = db.scalar(select(SysUser.id).where(SysUser.wecom_userid == "chenjing"))
    dict_service.put(db, "receivables_source", "qa-source", {"enabled": True, "kind": "receipt", "auto_apply": True, "reader_id": reader})
    db.commit()
    monkeypatch.setattr(receivables_sync, "fetch_source", lambda *_: [("row-1", values)])
    assert sync_sources(db, {})["rows"] == 1
    row = db.scalar(select(FinReceivablesSourceRow).where(FinReceivablesSourceRow.source_key == source))
    assert row.state == "applied"
    assert sync_sources(db, {})["rows"] == 0
    values["amount"] = "97"
    assert sync_sources(db, {})["rows"] == 1
    db.refresh(row)
    assert row.state == "changed" and db.get(FinReceipt, row.target_id).amount == D("95.50")

    def unavailable(*_):
        raise bad_request("测试：连接失效")

    monkeypatch.setattr(receivables_sync, "fetch_source", unavailable)
    with pytest.raises(RuntimeError, match="连接失效"):
        sync_sources(db, {})
    db.refresh(row)
    assert row.state == "changed"
    monkeypatch.setattr(receivables_sync, "fetch_source", lambda *_: [])
    assert sync_sources(db, {})["rows"] == 1
    db.refresh(row)
    assert row.state == "missing" and db.get(FinReceipt, row.target_id).amount == D("95.50")


def test_configured_source_uses_only_bound_reader_and_mapped_columns(db, monkeypatch):
    from app.models import SysUser
    from app.services import wecom_cli_service as cli

    reader = db.scalar(select(SysUser.id).where(SysUser.wecom_userid == "chenjing"))
    monkeypatch.setattr(cli, "_owned", lambda user_id: {"binding_id": f"reader-{user_id}"})
    methods = []

    def read(user_id, method, params, binding, workspace):
        assert user_id == reader and binding == f"reader-{reader}"
        assert params["docid"] == "test-doc" and params["sheet_id"] == "test-sheet"
        methods.append(method)
        if method == "smartsheet.fields.list":
            return {"fields": [{"field_title": name} for name in ["票号", "开票日", "金额", "客户", "不接入"]]}
        return {"records": [{"record_id": "test-row", "values": {
            "票号": "00100", "开票日": DATE, "金额": 12.5, "客户": "验收客户", "不接入": "忽略"}}]}

    monkeypatch.setattr(cli, "import_read", read)
    got = receivables_sync.fetch_source(db, {"kind": "invoice", "reader_id": reader, "docid": "test-doc",
        "sheet_id": "test-sheet", "fields": {"invoice_no": "票号", "issued_on": "开票日", "amount": "金额", "customer": "客户"}})
    assert methods == ["smartsheet.fields.list", "smartsheet.records.list"]
    assert got == [("test-row", {"invoice_no": "00100", "issued_on": DATE, "amount": 12.5, "customer": "验收客户"})]
