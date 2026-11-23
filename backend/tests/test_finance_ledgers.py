"""The private financial review must never write to business ledgers before confirmation."""
import io
from concurrent.futures import ThreadPoolExecutor

from openpyxl import Workbook
from sqlalchemy import func, select

from app.models import (BizExpenseReport, BizPayment, FinCashJournal, FinLedgerEntry, FinReceipt,
                        FinSalesInvoice, LogOperation)
from app.services import finance_ledger_source as source
from conftest import data

API = "/api/v1/finance-ledgers"


def upload(client, headers, kind, content, filename="财务流水.csv", ai=False):
    return client.post(f"{API}/preview", headers=headers, data={"kind": kind, "use_xiaoju": str(ai).lower()},
                       files={"file": (filename, content.encode() if isinstance(content, str) else content)})


def show(client, headers, kind, **filters):
    return data(client.get(f"{API}/entries/{kind}", headers=headers, params=filters))


def review(client, headers, batch, **changes):
    return client.post(f"{API}/imports/{batch['id']}/review", headers=headers, json={"version": batch["version"], **changes})


def confirm(client, headers, batch, selected=None):
    ids = [r["id"] for r in batch["rows"] if r["status"] != "error"] if selected is None else selected
    return client.post(f"{API}/imports/{batch['id']}/confirm", headers=headers, json={"version": batch["version"], "row_ids": ids})


def workbook(sheets):
    wb = Workbook()
    wb.remove(wb.active)
    for title, records in sheets.items():
        ws = wb.create_sheet(title)
        for record in records:
            ws.append(record)
    stream = io.BytesIO()
    wb.save(stream)
    return stream.getvalue()


def test_workbook_templates_and_standalone_financials(client, login, db):
    headers = login("chenjing")
    models = [BizExpenseReport, BizPayment, FinCashJournal, FinReceipt, FinSalesInvoice]
    before = [db.scalar(select(func.count()).select_from(model)) for model in models]
    file = workbook({
        "到账": [["收款记录明细表"], ["序号", "收款日期", "收款银行", "汇款公司", "收入金额", "摘要", "项目"],
               [1, "2026/6/1", "测试银行2200", "示例公司", 3500.15, "九月款", "项目 A"],
               [2, "2026/6/1", "测试银行2200", "示例公司", 3500.15, "九月款", "项目 A"], ["合计", None, None, None, 7000.30]],
        "二维码甲": [["测试账户3300全付通收款记录明细表"], ["序号", "收款日期", "交易金额", "手续费", "实收金额", "项目"],
                  [1, "2026/6/2", 100, 0.23, 99.77, "项目 B"], [2, "2026/6/3", 200, 0.46, None, "项目 C"]],
        "二维码乙": [["测试账户4400二维码收款明细表"], ["序号", "收款日期", "实收金额", "项目"],
                  [1, "2026/6/2", "￥11,371.50", "项目 D"]],
        "支出": [["银行支出日记账"], ["序号", "日期", "汇款公司", "摘要", "收款方资料", "金额", "申请", "审批编号", "备注"],
               [1, "2026/6/1", "测试银行2200", "项目材料", "示例供应商", 15000, "测试人", "000234", "首款"]],
        "测试账户5500现金日记帐": [["序号", "日期", "对方科目", "申请编号", "摘要", "收入金额", "支付金额", "结余金额", "备注"],
                           [1, None, None, None, "上月结转", None, None, 810.59, None],
                           [2, "2026/6/2", "示例员工", "000456", "备用金支出", None, 10, 800.59, "用途"]],
        "发票": [["开票日期", "发票号码", "购方名称", "价税合计", "合同编号"], ["2026/6/2", "00000001234", "示例学校", 500, "原表文本编号"]],
    })
    batch = data(upload(client, headers, "bank_receipt", file, "财务账套.xlsx"))
    assert batch["counts"] == {"ready": 9, "duplicate": 0, "repeated": 0, "error": 0}
    assert show(client, headers, "bank_receipt", account="测试银行2200")["total"] == 0
    assert data(confirm(client, headers, batch))["inserted_by_kind"] == {
        "bank_receipt": 2, "qr_receipt": 3, "bank_expense": 1, "cash_journal": 2, "invoice": 1
    }
    assert data(confirm(client, headers, batch))["inserted"] == 9
    assert show(client, headers, "bank_receipt", account="测试银行2200")["totals"]["inflow"] == "7000.30"
    qr = show(client, headers, "qr_receipt", account="测试账户3300全付通")
    assert qr["totals"] == {"inflow": "299.31", "outflow": "0.00", "fee": "0.69"}
    assert show(client, headers, "qr_receipt", account="测试账户4400")["items"][0]["values"]["fee"] == ""
    cash = show(client, headers, "cash_journal", period="2026-06", account="测试账户5500")
    assert cash["total"] == 2 and cash["totals"]["inflow"] == "0.00" and cash["totals"]["outflow"] == "10.00"
    assert cash["items"][0]["values"]["date"] == "" and cash["items"][0]["values"]["balance"] == "810.59"
    assert cash["items"][0]["source"]["row"] == 2
    spend = show(client, headers, "bank_expense", keyword="000234", project="unassigned")["items"][0]
    assert data(client.patch(f"{API}/entries/{spend['id']}/project", headers=headers, json={"project": "项目甲"}))["values"]["project"] == "项目甲"
    assert show(client, headers, "bank_expense", keyword="000234", project="unassigned")["total"] == 0
    assert show(client, headers, "invoice", keyword="00000001234")["total"] == 1
    assert [db.scalar(select(func.count()).select_from(model)) for model in models] == before
    repeats = data(upload(client, headers, "bank_receipt", file, "重传财务账套.xlsx"))
    assert repeats["counts"]["duplicate"] == 9


def test_manual_review_version_and_scope(client, login):
    headers = login("chenjing")
    assert client.get(f"{API}/schema", headers=login("yangyue")).status_code == 403
    batch = data(upload(client, headers, "bank_expense", "说明: 费用\n实际时间|从哪儿汇款|谁拿钱|实际支出\n2026/06/07|B02||¥20.55",
                        "自制支出.txt"))
    assert client.get(f"{API}/imports/{batch['id']}", headers=login("zhaoli")).status_code == 404
    assert upload(client, login("zhaoli"), "invoice", "发票号码,开票金额\n123,100").status_code == 403
    assert confirm(client, headers, batch, [batch["rows"][-1]["id"]]).status_code == 409
    adjusted = data(review(client, headers, batch, groups=[{"id": "1", "kind": "bank_expense", "header": 2,
                                                          "mapping": {"0": "date", "1": "account", "2": "payee", "3": "payment"}}]))
    assert adjusted["counts"]["error"] == 1 and len(adjusted["rows"]) == 1
    fixed = data(review(client, headers, adjusted, rows=[{"id": "1:3", "values": {"payee": "测试商户"}}]))
    assert fixed["counts"]["ready"] == 1
    assert confirm(client, headers, batch, ["1:3"]).status_code == 409
    assert data(confirm(client, headers, fixed))["inserted"] == 1
    assert show(client, headers, "bank_expense", account="B02")["totals"]["outflow"] == "20.55"


def test_xiaoju_uses_tools_for_unfamiliar_headers_and_source_proof(client, login, monkeypatch):
    def fake_pi(**kwargs):
        execute = kwargs["execute"]
        assert execute("check_session_scope", {})["ok"]
        sample = execute("read_rows", {"source": "1"})
        if sample["rows"][0][1][0] == "哪天来的":
            execute("map_columns", {"source": "1", "kind": "bank_receipt", "header_row": 1,
                                    "mapping": {"0": "date", "1": "account", "2": "payer", "3": "net"}})
        else:
            result = execute("extract_rows", {"source": "1", "kind": "bank_receipt", "rows": [{"row": 1, "values": {
                "date": "2026/6/7", "account": "验证账户B3", "payer": "示例客户", "net": "1200", "project": "不存在的项目"}}]})
            assert result["accepted"] == 1

    monkeypatch.setattr(source.pi_bridge, "available", lambda: True)
    monkeypatch.setattr(source.pi_bridge, "run", fake_pi)
    headers = login("chenjing")
    batch = data(upload(client, headers, "bank_receipt", "哪天来的,收到哪了,谁打来的,来了多少\n2026/6/7,验证账户A3,示例客户,1200",
                        ai=True))
    assert batch["groups"][0]["method"] == "xiaoju" and batch["rows"][0]["values"]["net"] == "1200.00"
    assert show(client, headers, "bank_receipt", account="验证账户A3")["total"] == 0
    batch = data(upload(client, headers, "bank_receipt", "日期2026/6/7，验证账户B3入账1200元，付款方示例客户。", "到账说明.txt", ai=True))
    assert batch["rows"][0]["values"]["project"] == ""
    assert data(confirm(client, headers, batch))["inserted"] == 1


def test_concurrent_confirm_and_conflicting_reference(client, login):
    headers = login("chenjing")
    content = "收款日期,收款银行,汇款公司,收入金额,银行流水号\n2026-06-11,并发测试账户,示例客户,1999,CI7008"
    batches = [data(upload(client, headers, "bank_receipt", content)) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda b: data(confirm(client, headers, b)), batches))
    assert sorted(receipt["inserted"] for receipt in results) == [0, 1]
    assert show(client, headers, "bank_receipt", account="并发测试账户")["total"] == 1
    conflict_batch = data(upload(client, headers, "bank_receipt", content.replace("1999", "1888")))
    assert conflict_batch["rows"][0]["status"] == "error"
    assert "差异" in conflict_batch["rows"][0]["errors"]["reference"]
    assert confirm(client, headers, conflict_batch, ["1:2"]).status_code == 409


def test_qr_arithmetic_and_human_selection(client, login):
    headers = login("chenjing")
    content = "收款日期,收款账户,交易金额,手续费,实收金额,项目\n2026-06-11,计算测试账户,100,2,99,待修正\n2026-06-12,计算测试账户,100,2,98,待选\n2026-06-13,计算测试账户,200,2,198,未选"
    batch = data(upload(client, headers, "qr_receipt", content))
    assert batch["counts"]["error"] == 1
    assert data(confirm(client, headers, batch, ["1:3"]))["inserted"] == 1
    result = show(client, headers, "qr_receipt", account="计算测试账户")
    assert result["totals"]["inflow"] == "98.00" and result["total"] == 1


def test_delete_single_entry_preserves_audit_and_allows_corrected_reimport(client, login, db):
    finance, cashier, director = (login(name) for name in ("chenjing", "zhaoli", "zhangzhenhua"))
    content = "收款日期,收款银行,汇款公司,收入金额,流水号\n2026-06-11,单条更正测试账户,示例客户,149.90,FIN-DELETE-909"
    batch = data(upload(client, finance, "bank_receipt", content))
    assert data(confirm(client, finance, batch))["inserted"] == 1
    row = show(client, finance, "bank_receipt", account="单条更正测试账户")["items"][0]
    assert row["can_delete"]
    assert not show(client, director, "bank_receipt", account="单条更正测试账户")["items"][0]["can_delete"]
    assert client.delete(f"{API}/entries/{row['id']}", headers=cashier).status_code == 403
    corrected = content.replace("149.90", "129.90")
    assert data(upload(client, finance, "bank_receipt", corrected))["counts"]["error"] == 1
    result = client.request("DELETE", f"{API}/entries/{row['id']}", headers=finance, json={"reason": "来源金额有误"})
    assert data(result) == {"removed": 1}
    assert data(client.delete(f"{API}/entries/{row['id']}", headers=finance)) == {"removed": 0}
    removed = db.get(FinLedgerEntry, row["id"])
    assert removed.deleted_at and removed.deleted_by and removed.delete_reason == "来源金额有误"
    audit = db.scalars(select(LogOperation).where(LogOperation.action == "finance_ledger.delete",
                                                  LogOperation.target_id == str(row["id"]))).one()
    assert audit.after["reason"] == "来源金额有误" and audit.user_id == removed.deleted_by
    ledger = show(client, finance, "bank_receipt", account="单条更正测试账户")
    assert ledger["total"] == 0 and ledger["totals"]["inflow"] == "0.00"
    assert "单条更正测试账户" not in ledger["accounts"]
    assert data(confirm(client, finance, batch))["inserted"] == 1  # The historical receipt is still idempotent.
    assert show(client, finance, "bank_receipt", account="单条更正测试账户")["total"] == 0
    ready = data(upload(client, finance, "bank_receipt", corrected))
    assert ready["counts"]["ready"] == 1
    assert data(confirm(client, finance, ready))["inserted"] == 1
    assert show(client, finance, "bank_receipt", account="单条更正测试账户")["totals"]["inflow"] == "129.90"


def test_revoke_mixed_batch_does_not_remove_prior_records(client, login, db):
    finance, cashier = (login(name) for name in ("chenjing", "zhaoli"))
    content = "收款日期,收款银行,汇款公司,收入金额,流水号\n2026-06-11,批次撤销测试账户,示例客户,128.80,FIN-BATCH-900"
    prior = data(upload(client, cashier, "bank_receipt", content))
    assert data(confirm(client, cashier, prior))["inserted"] == 1
    saved = show(client, cashier, "bank_receipt", account="批次撤销测试账户")["items"][0]
    assert saved["can_delete"]
    file = workbook({
        "到账": [["收款日期", "收款银行", "汇款公司", "收入金额", "流水号"],
               ["2026-06-11", "批次撤销测试账户", "示例客户", 128.80, "FIN-BATCH-900"],
               ["2026-06-12", "批次撤销测试账户", "示例客户", 152.50, "FIN-BATCH-901"]],
        "支出": [["日期", "汇款公司", "摘要", "收款方资料", "金额"],
               ["2026-06-12", "批次撤销测试账户", "测试采购", "示例商家", 71.20]],
    })
    batch = data(upload(client, finance, "bank_receipt", file, "批次撤销.xlsx"))
    assert batch["counts"]["duplicate"] == 1
    assert data(confirm(client, finance, batch))["inserted"] == 2
    uri = f"{API}/imports/{batch['id']}/revoke"
    assert client.get(uri, headers=cashier).status_code == 403
    assert client.post(uri, headers=cashier, json={}).status_code == 403
    preview = data(client.get(uri, headers=finance))
    assert preview == {"filename": "批次撤销.xlsx", "total": 2, "by_kind": {"bank_receipt": 1, "bank_expense": 1}}
    assert data(client.post(uri, headers=finance, json={"reason": "传错账套"})) == {"removed": 2, "by_kind": preview["by_kind"]}
    assert data(client.post(uri, headers=finance, json={})) == {"removed": 0, "by_kind": {}}
    assert data(client.get(uri, headers=finance))["total"] == 0
    ledger = show(client, finance, "bank_receipt", account="批次撤销测试账户")
    assert ledger["total"] == 1 and ledger["items"][0]["id"] == saved["id"]
    assert ledger["totals"]["inflow"] == "128.80"
    assert show(client, finance, "bank_expense", account="批次撤销测试账户")["total"] == 0
    audit = db.scalars(select(LogOperation).where(LogOperation.action == "finance_ledger.revoke",
                                                  LogOperation.after["filename"].as_string() == "批次撤销.xlsx")).one()
    assert audit.after["reason"] == "传错账套" and audit.after["removed"] == 2
    assert data(client.delete(f"{API}/entries/{saved['id']}", headers=cashier))["removed"] == 1
