"""The private financial review must never write to business ledgers before confirmation."""
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select

from app.core.response import BizError
from app.models import (BizExpenseReport, BizPayment, FinCashJournal, FinLedgerEntry, FinLedgerImport, FinReceipt,
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


def test_wecom_export_with_empty_fill_preserves_source_values(client, login, monkeypatch):
    def unexpected_ai(*args):
        pytest.fail("兼容处理后的标准表头应直接进入核对")

    monkeypatch.setattr(source, "run_xiaoju", unexpected_ai)
    headers = login("chenjing")
    wb = Workbook()
    sheet = wb.active
    sheet.title = "银行收入"
    sheet.append(["收款记录明细表"])
    sheet.append(["收款日期", "收款银行", "汇款公司", "收入金额", "流水号"])
    sheet.append([date(2026, 6, 2), "在线表格测试银行", "示例公司", 123.45, 123])
    sheet["E3"].number_format = "00000000"
    original, exported = io.BytesIO(), io.BytesIO()
    wb.save(original)
    with ZipFile(original) as archive, ZipFile(exported, "w", ZIP_DEFLATED) as target:
        for filename in archive.namelist():
            content = archive.read(filename)
            if filename == "xl/styles.xml":
                assert b"<fill><patternFill/></fill>" in content
                content = content.replace(b"<fill><patternFill/></fill>", b"<fill/>", 1)
            target.writestr(filename, content)

    batch = data(upload(client, headers, "bank_receipt", exported.getvalue(), "在线表格导出.xlsx", ai=True))
    assert batch["groups"][0]["method"] == "rule"
    row, = batch["rows"]
    assert (row["sheet"], row["row"], row["status"]) == ("银行收入", 3, "ready")
    assert row["values"]["date"] == "2026-06-02"
    assert row["values"]["net"] == "123.45"
    assert row["values"]["reference"] == "00000123"
    assert show(client, headers, "bank_receipt", account="在线表格测试银行")["total"] == 0


def test_unreadable_excel_returns_actionable_error_without_creating_draft(client, login, db, caplog):
    before = db.scalar(select(func.count()).select_from(FinLedgerImport))
    response = upload(client, login("chenjing"), "bank_receipt", b"not a workbook", "无法打开的表.xlsx", ai=True)
    assert response.status_code == 400
    error = response.json()
    assert "另存为 .xlsx" in error["message"] and "粘贴表格" in error["message"]
    assert error["trace_id"] in caplog.text and "BadZipFile" in caplog.text
    assert "not a workbook" not in caplog.text
    assert db.scalar(select(func.count()).select_from(FinLedgerImport)) == before


def test_workbook_templates_and_standalone_financials(client, login, db, monkeypatch):
    def unexpected_ai(*args):
        pytest.fail("标准模板应直接进入核对")

    monkeypatch.setattr(source, "run_xiaoju", unexpected_ai)
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
        "现金日记账": [["序号", "日期", "对方科目", "申请编号", "摘要", "收入金额", "支付金额", "结余金额", "备注"],
                           [1, None, None, None, "上月结转", None, None, 810.59, None],
                           [2, date(2026, 6, 2), "示例员工", "000456", "备用金支出", None, 10, 800.59, "用途"],
                           [3, date(2026, 6, 3), "备用金", None, "备用金补充", 1000, None, 1800.59, None],
                           [4, date(2026, 6, 4), "内部转记", None, "同日转记", None, 0, 1800.59, None]],
        "开票台账": [["编制单位：示例公司"], ["开票日期", "开票单位", "客户", "项目名称", "借方", "备注"],
                   [None, None, "应收账款合计", None, 1000, None],
                   [date(2026, 6, 2), "示例开票单位", "示例客户", "示例项目", 500, "首款"],
                   [date(2026, 6, 2), "示例开票单位", "示例客户", "示例项目", 500, "首款"]],
        "历史发票": [["开票日期", "发票号码", "购方名称", "价税合计", "合同编号"],
                   ["2026/6/2", "00000001234", "示例学校", 500, "原表文本编号"]],
        "已收账款明细": [["已收账款明细表"], ["开票日期", "开票单位", "名称", "收款日期", "收款金额", "备注"],
                       [None, None, "已收款合计", None, 700, None],
                       [date(2025, 12, 1), "示例开票单位", "示例客户-第一批项目款", date(2026, 6, 2), 700, "到账"]],
    })
    batch = data(upload(client, headers, "bank_receipt", file, "财务账套.xlsx", ai=True))
    assert batch["counts"] == {"ready": 11, "duplicate": 0, "repeated": 0, "error": 0}
    assert batch["skipped"] == [{"name": label, "reason": "二维码收款明细已停用"} for label in ("二维码甲", "二维码乙")]
    assert show(client, headers, "bank_receipt", account="测试银行2200")["total"] == 0
    assert data(confirm(client, headers, batch))["inserted_by_kind"] == {
        "bank_receipt": 2, "bank_expense": 1, "cash_journal": 4, "invoice": 3, "collection_receipt": 1
    }
    assert data(confirm(client, headers, batch))["inserted"] == 11
    assert show(client, headers, "bank_receipt", account="测试银行2200")["totals"]["inflow"] == "7000.30"
    cash = show(client, headers, "cash_journal", period="2026-06")
    assert cash["total"] == 4 and cash["totals"]["inflow"] == "1000.00" and cash["totals"]["outflow"] == "10.00"
    assert [row["cash_category"] for row in cash["items"]] == ["opening", "expense", "income", "zero"]
    assert cash["items"][0]["values"]["date"] == "" and cash["items"][0]["values"]["balance"] == "810.59"
    assert cash["items"][0]["source"]["row"] == 2
    assert cash["items"][3]["values"]["payment"] == "0.00"
    for direction, total_key, expected in (("income", "inflow", "1000.00"), ("expense", "outflow", "10.00")):
        movement = show(client, headers, "cash_journal", period="2026", direction=direction)
        assert movement["total"] == 1 and movement["totals"][total_key] == expected
        assert movement["items"][0]["cash_category"] == direction
    spend = show(client, headers, "bank_expense", keyword="000234", project="unassigned")["items"][0]
    assert data(client.patch(f"{API}/entries/{spend['id']}/project", headers=headers, json={"project": "项目甲"}))["values"]["project"] == "项目甲"
    assert show(client, headers, "bank_expense", keyword="000234", project="unassigned")["total"] == 0
    assert show(client, headers, "invoice", keyword="00000001234")["total"] == 1
    invoices = show(client, headers, "invoice", keyword="示例开票单位")
    assert invoices["total"] == 2 and invoices["items"][0]["values"]["invoice_no"] == ""
    collections = show(client, headers, "collection_receipt", period="2026")
    assert collections["total"] == 1 and collections["totals"]["inflow"] == "700.00"
    assert collections["items"][0]["values"]["name"] == "示例客户-第一批项目款"
    assert show(client, headers, "collection_receipt", period="2025")["total"] == 0
    assert [db.scalar(select(func.count()).select_from(model)) for model in models] == before
    repeats = data(upload(client, headers, "bank_receipt", file, "重传财务账套.xlsx"))
    assert repeats["counts"]["duplicate"] == 11


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
            execute("map_columns", {"source": "1", "kind": "bank_receipt", "header_row": 0,
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
    assert batch["groups"][0]["method"] == "xiaoju" and batch["groups"][0]["header"] == 1
    assert batch["counts"]["error"] == 0 and batch["rows"][0]["values"]["net"] == "1200.00"
    assert show(client, headers, "bank_receipt", account="验证账户A3")["total"] == 0
    batch = data(upload(client, headers, "bank_receipt", "日期2026/6/7，验证账户B3入账1200元，付款方示例客户。", "到账说明.txt", ai=True))
    assert batch["rows"][0]["values"]["project"] == ""
    assert data(confirm(client, headers, batch))["inserted"] == 1


def test_xiaoju_classifies_a_shared_cash_amount_column(client, login, monkeypatch):
    def fake_pi(**kwargs):
        execute = kwargs["execute"]
        params = {"source": "1", "kind": "cash_journal", "header_row": 1, "mapping": {
            "0": "date", "1": "summary", "2": "flow_direction", "3": "flow_amount", "4": "balance",
        }}
        with pytest.raises(BizError):
            execute("map_columns", {**params, "direction_labels": {"缺少源值": "payment"}})
        result = execute("map_columns", {**params, "direction_labels": {
            "备款补进": "income", "单据拨付": "payment", "待判定": "payment",
        }})
        assert result["ok"]

    monkeypatch.setattr(source.pi_bridge, "available", lambda: True)
    monkeypatch.setattr(source.pi_bridge, "run", fake_pi)
    headers = login("chenjing")
    file = workbook({"自制零用金": [
        ["2026年7月"],
        ["发生在哪天", "具体事项", "资金怎么走", "变了多少", "清点剩下"],
        [None, "上月结转", None, None, 850],
        ["2026/7/3", "补备用金", "备款补进", 500, 1350],
        ["2026/7/4", "购买纸张", "单据拨付", 50, 1300],
        ["2026/7/5", "待核对", "待判定", 20, 1280],
    ]})
    draft = data(upload(client, headers, "cash_journal", file, "非标准现金.xlsx", ai=True))
    assert draft["groups"][0]["method"] == "xiaoju" and draft["groups"][0]["header"] == 2
    assert [r["cash_category"] for r in draft["rows"]] == ["opening", "income", "expense", "unknown"]
    assert draft["rows"][-1]["errors"]["income"].startswith("请按原表核对")
    assert show(client, headers, "cash_journal", period="2026-07")["total"] == 0
    fixed = data(review(client, headers, draft, rows=[{"id": "1:6", "values": {"payment": "20"}}]))
    assert fixed["rows"][-1]["cash_category"] == "expense"
    assert data(confirm(client, headers, fixed))["inserted"] == 4
    assert show(client, headers, "cash_journal", period="2026-07", direction="expense")["totals"]["outflow"] == "70.00"


def test_cash_signed_and_separate_amounts_respect_source_conventions(client, login):
    headers = login("chenjing")
    batch = data(upload(client, headers, "cash_journal", workbook({
        "现金变动": [["日期", "带符号金额", "摘要", "余额"],
                   ["2026/8/1", 200, "补备用金", 1200], ["2026/8/2", -30, "买信封", 1170]],
        "现金调整": [["日期", "收入金额", "支付金额", "摘要"],
                   ["2026/8/3", 100, -25, "收款和冲回支出"]],
    }), "现金金额.xlsx", ai=True))
    assert [row["cash_category"] for row in batch["rows"]] == ["income", "expense", "both"]
    assert data(confirm(client, headers, batch))["inserted"] == 3
    income = show(client, headers, "cash_journal", period="2026-08", direction="income")
    expense = show(client, headers, "cash_journal", period="2026-08", direction="expense")
    assert income["total"] == expense["total"] == 2
    assert income["totals"] == {"inflow": "300.00", "outflow": "0.00", "fee": "0.00"}
    assert expense["totals"] == {"inflow": "0.00", "outflow": "5.00", "fee": "0.00"}


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


def test_module_access_and_retired_receipts(client, login):
    finance = login("chenjing")
    ledgers = data(client.get(f"{API}/schema", headers=finance))["ledgers"]
    assert list(ledgers) == ["invoice", "collection_receipt", "bank_receipt", "bank_expense", "cash_journal"]
    assert [ledgers[kind]["menu"] for kind in ledgers] == ["receivables", "receivables", "journal", "journal", "journal"]
    assert [field["label"] for field in ledgers["invoice"]["fields"] if field["visible"]] == [
        "开票日期", "开票单位", "客户", "项目名称", "借方", "备注",
    ]
    assert [field["label"] for field in ledgers["collection_receipt"]["fields"] if field["visible"]] == [
        "开票日期", "开票单位", "名称", "收款日期", "收款金额", "备注",
    ]
    assert client.get(f"{API}/entries/qr_receipt", headers=finance).status_code == 400
    content = "收款日期,收款账户,交易金额,手续费,实收金额,项目\n2026-06-11,历史二维码账户,100,2,98,示例"
    rejected = upload(client, finance, "bank_receipt", content)
    assert rejected.status_code == 400 and "二维码收款明细已停用" in rejected.json()["message"]
    assert upload(client, finance, "qr_receipt", content).status_code == 400


def test_year_filters_and_human_selection(client, login):
    finance = login("chenjing")
    content = ("收款日期,收款银行,汇款公司,实收金额\n"
               "2026-01-30,年度测试账户,示例客户,98\n"
               "2026-08-01,年度测试账户,示例客户,198\n"
               "2025-12-31,年度测试账户,示例客户,60\n"
               "2026-10-01,年度测试账户,示例客户,300")
    batch = data(upload(client, finance, "bank_receipt", content))
    assert batch["counts"]["ready"] == 4
    assert data(confirm(client, finance, batch, ["1:2", "1:3", "1:4"]))["inserted"] == 3
    result = show(client, finance, "bank_receipt", account="年度测试账户", period="2026")
    assert result["total"] == 2 and result["totals"]["inflow"] == "296.00"
    assert show(client, finance, "bank_receipt", account="年度测试账户", period="2026-08")["total"] == 1
    year = data(client.get("/api/v1/journal", headers=finance, params={"mode": "year", "period": "2026"}))
    assert year["start"] == "2026-01-01" and year["end"] == "2026-12-31"


def test_import_history_drafts_confirmations_and_removal(client, login):
    finance, cashier = (login(name) for name in ("chenjing", "zhaoli"))
    file = workbook({
        "收款记录": [["开票日期", "开票单位", "名称", "收款日期", "收款金额"],
                   ["2026/5/10", "示例开票单位", "测试公司", "2026/6/30", 800]],
        "支出": [["日期", "汇款公司", "摘要", "收款方资料", "金额"], ["2026/6/30", "导入历史账户", "测试采购", "商户", 20]],
    })
    batch = data(upload(client, finance, "bank_receipt", file, "导入历史复核.xlsx"))

    def history(menu, headers):
        result = data(client.get(f"{API}/imports", headers=headers, params={"menu": menu}))
        return next((row for row in result["items"] if row["id"] == batch["id"]), None)

    assert history("journal", finance)["status"] == "draft"
    assert history("receivables", finance)["status"] == "draft"
    assert history("receivables", cashier) is None
    assert data(confirm(client, finance, batch))["inserted"] == 2
    assert history("journal", finance)["kinds"] == ["bank_expense"]
    assert history("receivables", finance)["kinds"] == ["collection_receipt"]
    assert history("receivables", cashier)["can_revoke"] is False
    assert history("journal", finance)["active"] == 1
    assert data(client.post(f"{API}/imports/{batch['id']}/revoke", headers=finance, json={}))["removed"] == 2
    assert history("journal", finance)["status"] == "removed"
    assert not history("journal", finance)["can_revoke"]


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


def test_revoke_old_mixed_import_preserves_retired_qr_rows(client, login, db):
    finance = login("chenjing")
    content = "收款日期,收款银行,汇款公司,收入金额\n2026-05-16,停用账表测试账户,示例客户,50"
    preview = data(upload(client, finance, "bank_receipt", content))
    assert data(confirm(client, finance, preview))["inserted"] == 1
    batch = db.scalar(select(FinLedgerImport).where(FinLedgerImport.token == preview["id"]))
    counts = {"bank_receipt": 1, "qr_receipt": 1}
    batch.result = {**batch.result, "inserted": 2, "inserted_by_kind": counts, "selection": counts}
    old = FinLedgerEntry(import_id=batch.id, source_sheet="二维码记录", source_row=3, kind="qr_receipt",
                         identity="a" * 64, signature="b" * 64, search_text="旧版已停用", values={})
    db.add(old)
    db.commit()
    uri = f"{API}/imports/{batch.token}/revoke"
    assert data(client.get(uri, headers=finance))["by_kind"] == {"bank_receipt": 1}
    assert data(client.post(uri, headers=finance, json={}))["removed"] == 1
    db.refresh(old)
    assert old.deleted_at is None
