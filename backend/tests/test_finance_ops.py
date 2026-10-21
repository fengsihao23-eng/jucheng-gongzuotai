"""财务运营：报销归集 / 银行流水导入与核对（FR-09）/ 出纳日记账（FR-05-03）/ 柠檬云双通道（FR-08）/ 税务申报 / 项目成本 / RBAC。

运行：cd backend && TEST_DB=jucheng_test_fin .venv/bin/python -m pytest tests/test_finance_ops.py -q
"""
import io
import time
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from tests.conftest import data

D = Decimal
API = "/api/v1"


def up(client, url, headers, name, content, **fields):
    return client.post(API + url, headers=headers, files={"file": (name, content)}, data=fields)


@pytest.fixture(scope="module")
def fin(login):
    return login("chenjing")


@pytest.fixture(scope="module")
def cashier(login):
    return login("zhaoli")


@pytest.fixture(scope="module")
def cmb_sample(client, cashier):
    """在测试库上现场生成样例并导入一次（模块共享）。"""
    from app.core.db import SessionLocal
    from scripts.make_samples_finance import build_cmb_sample
    s = SessionLocal()
    content, meta = build_cmb_sample(s)
    s.close()
    rep = data(up(client, "/flows/import", cashier, "bankflow_cmb_sample.xlsx", content))
    return {"content": content, "meta": meta, "report": rep}


# ====================== FR-09 流水导入 ======================
def test_import_report_dup_and_row_errors(cmb_sample):
    rep, meta = cmb_sample["report"], cmb_sample["meta"]
    assert rep["bank_template"] == "cmb"
    assert rep["total_rows"] == meta["data_rows"]
    assert rep["duplicate_rows"] == 1  # 与库内已有流水重复
    assert rep["error_rows"] == 1
    assert rep["inserted_rows"] == meta["data_rows"] - 2
    errs = [e for e in rep["errors"] if e["row"] == meta["error_row"]]
    assert {e["field"] for e in errs} >= {"交易日"}, rep["errors"]
    assert all({"row", "field", "message"} <= set(e) for e in rep["errors"])
    bc = rep["balance_check"]
    assert bc["ok"] is True and bc["accounts"][0]["db_continuity"] is True
    assert rep["suggest_summary"]["unmatched"] == rep["inserted_rows"]


def test_reimport_zero_duplicates(client, cashier, cmb_sample, db):
    from app.models import FinBankFlow
    before = db.scalar(select(func.count()).select_from(FinBankFlow))
    rep = data(up(client, "/flows/import", cashier, "again.xlsx", cmb_sample["content"]))
    assert rep["inserted_rows"] == 0
    assert rep["duplicate_rows"] == cmb_sample["meta"]["data_rows"] - 1
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(FinBankFlow)) == before
    assert db.scalar(select(func.count()).select_from(
        select(FinBankFlow.fingerprint).group_by(FinBankFlow.fingerprint).having(func.count() > 1).subquery())) == 0


def test_500_rows_under_10s_and_idempotent(client, cashier):
    from scripts.make_samples_finance import build_cmb_500
    content = build_cmb_500(520)
    t0 = time.perf_counter()
    rep = data(up(client, "/flows/import", cashier, "bankflow_cmb_500.xlsx", content))
    cost = time.perf_counter() - t0
    assert rep["total_rows"] == 520 and rep["inserted_rows"] == 520 and rep["error_rows"] == 0
    assert rep["balance_check"]["ok"] is True
    assert cost <= 10, f"500 行解析入库耗时 {cost:.2f}s"
    rep2 = data(up(client, "/flows/import", cashier, "bankflow_cmb_500.xlsx", content))
    assert rep2["inserted_rows"] == 0 and rep2["duplicate_rows"] == 520
    acct = rep["balance_check"]["accounts"][0]["account"]
    assert acct == "招商银行 7559****0002"


def test_icbc_csv_gbk_and_template_detect(client, cashier, db):
    from scripts.make_samples_finance import build_icbc_csv
    rep = data(up(client, "/flows/import", cashier, "icbc.csv", build_icbc_csv(db)))
    assert rep["bank_template"] == "icbc" and rep["inserted_rows"] == 3 and rep["error_rows"] == 0
    acc = rep["balance_check"]["accounts"][0]
    assert acc["account"] == "工商银行一般户 3602****0088" and acc["db_continuity"] is True


def test_ccb_template_and_balance_break(client, cashier):
    import csv
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["账号", "交易时间", "借方发生额（支取）", "贷方发生额（收入）", "余额", "币种", "对方户名", "对方账号", "摘要",
                "账户明细编号-交易流水号"])
    w.writerow(["44001234560166", "20260801 10:00:00", "", "1000.00", "11000.00", "人民币", "甲公司", "", "收款", "CCB-1"])
    w.writerow(["44001234560166", "20260802 10:00:00", "200.00", "", "10800.00", "人民币", "乙公司", "", "付款", "CCB-2"])
    w.writerow(["44001234560166", "20260803 10:00:00", "300.00", "", "10000.00", "人民币", "丙公司", "", "付款", "CCB-3"])
    w.writerow(["44001234560166", "20260804 10:00:00", "300.00", "50.00", "9700.00", "人民币", "丁公司", "", "冲突", "CCB-4"])
    rep = data(up(client, "/flows/import", cashier, "ccb.csv", out.getvalue().encode()))
    assert rep["bank_template"] == "ccb" and rep["inserted_rows"] == 3 and rep["error_rows"] == 1
    assert rep["errors"][0]["row"] == 5 and "方向冲突" in rep["errors"][0]["message"]
    bc = rep["balance_check"]
    assert bc["ok"] is False and bc["accounts"][0]["breaks"][0]["row"] == 4
    assert bc["accounts"][0]["account"] == "建设银行一般户 4400****0166"


def test_unrecognized_file(client, cashier):
    rep = data(up(client, "/flows/import", cashier, "x.csv", "a,b,c\n1,2,3\n".encode()))
    assert rep["inserted_rows"] == 0 and rep["errors"][0]["field"] == "表头"


# ====================== FR-09-03 匹配建议准确率 ======================
def test_suggestion_accuracy_on_sample(client, cashier, cmb_sample, db):
    """样例文件中已知可匹配行：Top1 建议即正确目标的比例 ≥ 80%。"""
    from app.models import FinBankFlow
    exp = cmb_sample["meta"]["expected"]
    assert len(exp) >= 7
    hit = 0
    for e in exp:
        f = db.scalar(select(FinBankFlow).where(FinBankFlow.import_id == cmb_sample["report"]["id"],
                                                FinBankFlow.source_row == e["row"]))
        s = data(client.get(f"{API}/flows/{f.id}/suggestions", headers=cashier))
        top = s[0] if s else {}
        if top.get("kind") == e["kind"] and (top.get("target_id") == e["target_id"]) and \
                (e["target_no"] is None or top.get("target_no") == e["target_no"]):
            hit += 1
    assert hit / len(exp) >= 0.8, f"准确率 {hit}/{len(exp)}"


def test_suggestion_accuracy_on_seed_pending(client, cashier, db):
    """种子中待核对流水（到账/报销付款/结息）：依据业务真值抽检 Top1 准确率 ≥ 80%。"""
    from app.models import BizContract, BizExpenseReport, FinBankFlow, SysUser
    flows = db.scalars(select(FinBankFlow).where(FinBankFlow.match_status == 0, FinBankFlow.source_file == "seed")).all()
    assert len(flows) >= 4
    hit = 0
    for f in flows:
        if f.direction == 1 and "结息" in (f.summary or ""):
            truth = ("other_income", None)
        elif f.direction == 1:
            c = next(c for c in db.scalars(select(BizContract).where(BizContract.customer_name == f.counterparty)))
            truth = ("payment", {c.id for c in db.scalars(select(BizContract).where(BizContract.customer_name == f.counterparty))})
        else:
            uid = db.scalar(select(SysUser.id).where(SysUser.name == f.counterparty))
            ids = set(db.scalars(select(BizExpenseReport.id).where(BizExpenseReport.applicant_id == uid,
                                                                   BizExpenseReport.amount == f.amount,
                                                                   BizExpenseReport.pay_status == 0)))
            truth = ("expense", ids)
        s = data(client.get(f"{API}/flows/{f.id}/suggestions", headers=cashier))
        if s and s[0]["kind"] == truth[0] and (truth[1] is None or s[0]["target_id"] in truth[1]):
            hit += 1
    assert hit / len(flows) >= 0.8, f"准确率 {hit}/{len(flows)}"


# ====================== 核对确认 → 回款/日记账/报销联动 + 撤销 ======================
def _flow_of(db, imp_id, row):
    from app.models import FinBankFlow
    return db.scalar(select(FinBankFlow).where(FinBankFlow.import_id == imp_id, FinBankFlow.source_row == row))


def test_confirm_payment_and_undo(client, cashier, cmb_sample, db):
    from app.models import BizContract, BizPayment, FinCashJournal
    e = next(x for x in cmb_sample["meta"]["expected"] if x["kind"] == "payment")
    f = _flow_of(db, cmb_sample["report"]["id"], e["row"])
    c = db.get(BizContract, e["target_id"])
    recv0 = sum((p.received_amount for p in c.plans), D(0))
    r = data(client.post(f"{API}/flows/{f.id}/match", headers=cashier, json={"kind": "payment", "target_id": c.id}))
    assert r["match_status"] == 2 and r["contract_no"] == c.contract_no
    db.expire_all()
    pay = db.scalar(select(BizPayment).where(BizPayment.bank_flow_id == f.id))
    assert pay.amount == f.amount and pay.source_type == 1 and pay.payment_date == f.trade_date
    j = db.scalar(select(FinCashJournal).where(FinCashJournal.bank_flow_id == f.id))
    assert j.category == "回款" and j.amount == f.amount and j.source_type == 1 and j.project_code == c.project_code
    c = db.get(BizContract, c.id)
    assert sum((p.received_amount for p in c.plans), D(0)) == recv0 + f.amount
    # 重复确认 → 冲突
    assert client.post(f"{API}/flows/{f.id}/match", headers=cashier,
                       json={"kind": "payment", "target_id": c.id}).json()["code"] == 40901
    # 撤销：回款与日记账回滚
    data(client.post(f"{API}/flows/{f.id}/unmatch", headers=cashier))
    db.expire_all()
    assert db.scalar(select(BizPayment).where(BizPayment.bank_flow_id == f.id)) is None
    assert db.scalar(select(FinCashJournal).where(FinCashJournal.bank_flow_id == f.id)) is None
    assert db.get(FinBankFlow_(), f.id).match_status == 0
    c = db.get(BizContract, c.id)
    assert sum((p.received_amount for p in c.plans), D(0)) == recv0


def FinBankFlow_():
    from app.models import FinBankFlow
    return FinBankFlow


def test_confirm_expense_project_cost_public(client, cashier, cmb_sample, db):
    from app.models import BizExpenseReport, BizProjectCost, FinCashJournal
    exp = cmb_sample["meta"]["expected"]
    imp = cmb_sample["report"]["id"]
    # 报销
    e = next(x for x in exp if x["kind"] == "expense")
    f = _flow_of(db, imp, e["row"])
    data(client.post(f"{API}/flows/{f.id}/match", headers=cashier, json={"kind": "expense", "target_id": e["target_id"]}))
    db.expire_all()
    r = db.get(BizExpenseReport, e["target_id"])
    assert r.pay_status == 1 and r.bank_flow_id == f.id
    assert db.get(FinBankFlow_(), f.id).match_status == 1
    j = db.scalar(select(FinCashJournal).where(FinCashJournal.bank_flow_id == f.id))
    assert j.category == "报销" and j.biz_ref == r.approval_no
    # 金额不一致的报销 → 冲突
    other = next(x for x in exp if x["kind"] == "expense" and x["row"] != e["row"])
    f2 = _flow_of(db, imp, other["row"])
    assert client.post(f"{API}/flows/{f2.id}/match", headers=cashier,
                       json={"kind": "expense", "target_id": e["target_id"]}).json()["code"] == 40901
    # 撤销报销核对
    data(client.post(f"{API}/flows/{f.id}/unmatch", headers=cashier))
    db.expire_all()
    assert db.get(BizExpenseReport, e["target_id"]).pay_status == 0
    # 新登记项目成本 → 撤销时删除
    e = next(x for x in exp if x["kind"] == "project_cost" and x["target_id"] is None)
    f = _flow_of(db, imp, e["row"])
    r = data(client.post(f"{API}/flows/{f.id}/match", headers=cashier,
                         json={"kind": "project_cost", "project_code": e["target_no"], "cost_type": "采购"}))
    db.expire_all()
    cost = db.get(BizProjectCost, r["project_cost_id"])
    assert cost.project_code == e["target_no"] and cost.bank_flow_id == f.id and cost.amount == f.amount
    data(client.post(f"{API}/flows/{f.id}/unmatch", headers=cashier))
    db.expire_all()
    assert db.get(BizProjectCost, r["project_cost_id"]) is None
    # 公户支出；收入流水不能标公户
    e = next(x for x in exp if x["kind"] == "public")
    f = _flow_of(db, imp, e["row"])
    data(client.post(f"{API}/flows/{f.id}/match", headers=cashier, json={"kind": "public"}))
    db.expire_all()
    assert db.get(FinBankFlow_(), f.id).match_status == 3
    inc = next(x for x in exp if x["kind"] == "payment")
    assert client.post(f"{API}/flows/{_flow_of(db, imp, inc['row']).id}/match", headers=cashier,
                       json={"kind": "public"}).json()["code"] == 40001


def test_auto_match_high_confidence(client, cashier, cmb_sample, db):
    from app.models import FinBankFlow
    imp = cmb_sample["report"]["id"]
    ids = list(db.scalars(select(FinBankFlow.id).where(FinBankFlow.import_id == imp, FinBankFlow.match_status == 0)))
    dry = data(client.post(f"{API}/flows/auto-match", headers=cashier, json={"flow_ids": ids, "dry_run": True}))
    assert dry["matched"] >= 4
    r = data(client.post(f"{API}/flows/auto-match", headers=cashier, json={"flow_ids": ids}))
    assert r["matched"] == dry["matched"]
    assert all(x["score"] >= r["threshold"] for x in r["items"])
    db.expire_all()
    exp = {x["row"]: x for x in cmb_sample["meta"]["expected"]}
    for it in r["items"]:
        f = db.get(FinBankFlow, it["flow_id"])
        assert f.match_status != 0
        e = exp.get(f.source_row)
        if e:
            assert it["kind"] == e["kind"]
    # 低置信（新登记项目成本）不会被自动核对
    new_cost = next(x for x in cmb_sample["meta"]["expected"] if x["kind"] == "project_cost" and x["target_id"] is None)
    assert _flow_of(db, imp, new_cost["row"]).match_status == 0


def test_workbench_and_list(client, cashier):
    wb = data(client.get(f"{API}/flows/workbench", headers=cashier))
    assert wb["unmatched"] >= 1 and wb["can_match"] is True
    assert all(x["match_status"] == 0 for x in wb["items"])
    lst = data(client.get(f"{API}/flows", headers=cashier, params={"status": "0", "direction": 2, "page_size": 5}))
    assert all(x["match_status"] == 0 and x["direction"] == 2 for x in lst["items"])
    assert D(lst["summary"]["expense"]) > 0
    imps = data(client.get(f"{API}/flows/imports", headers=cashier))
    assert imps["total"] >= 3 and imps["items"][0]["filename"]


# ====================== FR-05-03 出纳日记账 ======================
def test_journal_balance_consistency(client, cashier, db):
    from app.core.timeutil import today
    from app.models import FinCashJournal
    from app.services import journal_service
    acct = "招商银行基本户 7559****0001"
    m = data(client.get(f"{API}/journal", headers=cashier, params={"mode": "month", "account": acct}))
    card = next(a for a in m["accounts"] if a["account"] == acct)
    assert D(card["closing"]) == D(card["opening"]) + D(card["income"]) - D(card["expense"])
    # 期末余额 = 期初 + 全部日记账净额
    op = journal_service.openings(db)[acct]
    rows = db.execute(select(FinCashJournal.direction, FinCashJournal.amount).where(
        FinCashJournal.bank_account == acct, FinCashJournal.account_type == "bank",
        FinCashJournal.journal_date <= today())).all()
    net = sum((a if d == 1 else -a for d, a in rows), D(0))
    assert D(card["closing"]) == D(str(op["amount"])) + net
    # 日视图衔接：今日期初 = 昨日期末
    t = data(client.get(f"{API}/journal", headers=cashier, params={"mode": "day", "account": acct}))
    from datetime import timedelta
    y = data(client.get(f"{API}/journal", headers=cashier,
                        params={"mode": "day", "account": acct, "day": (today() - timedelta(days=1)).isoformat()}))
    tc = next(a for a in t["accounts"] if a["account"] == acct)
    yc = next(a for a in y["accounts"] if a["account"] == acct)
    assert tc["opening"] == yc["closing"]
    # 逐笔余额连续
    if m["entries"]:
        last = m["entries"][-1]
        assert D(last["balance"]) == D(card["closing"])
    assert len(m["daily"]) >= 20


def test_journal_manual_cash_crud(client, cashier, db):
    from app.models import LogOperation
    v0 = data(client.get(f"{API}/journal", headers=cashier, params={"mode": "day", "account": "cash"}))
    c0 = next(a for a in v0["accounts"] if a["account"] == "cash")
    r = data(client.post(f"{API}/journal", headers=cashier, json={"journal_date": v0["start"], "direction": 2,
                                                                  "amount": "123.40", "summary": "备用金-快递费",
                                                                  "category": "快递费"}))
    v1 = data(client.get(f"{API}/journal", headers=cashier, params={"mode": "day", "account": "cash"}))
    c1 = next(a for a in v1["accounts"] if a["account"] == "cash")
    assert D(c1["closing"]) == D(c0["closing"]) - D("123.40")
    data(client.put(f"{API}/journal/{r['id']}", headers=cashier, json={"journal_date": v0["start"], "direction": 2,
                                                                       "amount": "100.00", "summary": "备用金-快递费（更正）"}))
    data(client.delete(f"{API}/journal/{r['id']}", headers=cashier))
    acts = set(db.scalars(select(LogOperation.action).where(LogOperation.target_type == "journal",
                                                            LogOperation.target_id == str(r["id"]))))
    assert acts == {"journal.create", "journal.update", "journal.delete"}
    # 流水生成的日记账不可手工改
    auto = next(e for e in v0["entries"] if e["source_type"] == 1) if any(e["source_type"] == 1 for e in v0["entries"]) else None
    m = data(client.get(f"{API}/journal", headers=cashier, params={"mode": "month"}))
    auto = auto or next(e for e in m["entries"] if e["source_type"] == 1)
    assert client.delete(f"{API}/journal/{auto['id']}", headers=cashier).json()["code"] == 40901


def test_journal_export(client, cashier):
    from openpyxl import load_workbook
    r = client.get(f"{API}/journal/export", headers=cashier)
    assert r.status_code == 200
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["银行日记账", "现金日记账", "口径说明"]
    assert wb["银行日记账"].max_row > 10


# ====================== 报销归集 ======================
def test_expense_list_stats_filter(client, fin):
    lst = data(client.get(f"{API}/expenses", headers=fin, params={"status": "pending", "page_size": 100}))
    assert lst["total"] >= 1 and all(x["status"] == 2 for x in lst["items"])
    assert lst["summary"]["pending_count"] == lst["total"]
    pub = data(client.get(f"{API}/expenses", headers=fin, params={"status": "allocated", "project": "__public__"}))
    assert all(x["project_code"] is None for x in pub["items"])
    st = data(client.get(f"{API}/expenses/stats", headers=fin))
    k = st["kpi"]
    assert D(k["total_amount"]) > 0 and 0 < float(k["allocated_rate"]) <= 1
    assert D(k["total_amount"]) == sum(D(x["amount"]) for x in st["by_project"])
    assert st["by_dept"] and st["by_category"] and st["trend"]


def test_expense_allocate_and_project_rollup(client, fin, db):
    from app.models import BizExpenseReport, LogOperation
    pend = data(client.get(f"{API}/expenses", headers=fin, params={"status": "pending", "page_size": 100}))["items"]
    ids = [x["id"] for x in pend[:2]]
    code = "JC-XY-2026-004"
    before = data(client.get(f"{API}/expenses", headers=fin, params={"status": "allocated", "project": code}))["summary"]["amount"]
    data(client.post(f"{API}/expenses/allocate", headers=fin, json={"ids": ids, "project_code": code, "remark": "补归集"}))
    after = data(client.get(f"{API}/expenses", headers=fin, params={"status": "allocated", "project": code}))["summary"]["amount"]
    assert D(after) == D(before) + sum(D(x["amount"]) for x in pend[:2])
    db.expire_all()
    for i in ids:
        r = db.get(BizExpenseReport, i)
        assert r.status == 4 and r.project_code == code and r.allocated_by
        assert r.detail["allocation_log"][-1]["to"] == code
    assert db.scalar(select(func.count()).select_from(LogOperation).where(
        LogOperation.action == "expense.allocate", LogOperation.target_id.in_([str(i) for i in ids]))) == 2
    # 与项目利润口径一致（profit_service 读取同一张表）
    det = data(client.get(f"{API}/expenses/{ids[0]}", headers=fin))
    assert det["project_code"] == code and det["allocation_log"][-1]["by_name"] == "陈静"
    assert det["approval"]["sp_no"] == det["approval_no"] and det["approval"]["exists"] is True
    assert "所属项目" in det["approval"]["fields"]
    # 公共费用
    if len(pend) > 2:
        data(client.post(f"{API}/expenses/allocate", headers=fin, json={"ids": [pend[2]["id"]], "project_code": None}))
        db.expire_all()
        r = db.get(BizExpenseReport, pend[2]["id"])
        assert r.status == 4 and r.project_code is None
    # 无效项目
    assert client.post(f"{API}/expenses/allocate", headers=fin,
                       json={"ids": ids, "project_code": "JC-XX-0000-000"}).json()["code"] == 40001


def test_expense_export_matches_filter(client, fin):
    from openpyxl import load_workbook
    params = {"status": "allocated", "category": "差旅费"}
    lst = data(client.get(f"{API}/expenses", headers=fin, params={**params, "page_size": 1}))
    r = client.get(f"{API}/expenses/export", headers=fin, params=params)
    wb = load_workbook(io.BytesIO(r.content))
    ws = wb["报销归集明细"]
    assert ws.max_row - 2 == lst["total"]  # 表头 + 合计
    assert D(str(ws.cell(ws.max_row, 7).value)).quantize(D("0.01")) == D(lst["summary"]["amount"])
    assert "口径说明" in wb.sheetnames


# ====================== FR-08 柠檬云 ======================
def test_lemon_seed_batches_and_api_sync(client, fin):
    b = data(client.get(f"{API}/finance-data/lemon/batches", headers=fin))
    assert b["total"] >= 3 and {"api", "file"} <= {x["channel"] for x in b["items"]}
    r = data(client.post(f"{API}/finance-data/lemon/sync", headers=fin, json={}))
    assert r["channel"] == "api" and r["voucher_count"] > 0
    assert r["reconcile_status"] == "pass", r["diffs"][:3]
    assert D(r["debit_total"]) == D(r["credit_total"])
    bal = data(client.get(f"{API}/finance-data/balances", headers=fin))
    assert bal["items"] and bal["balanced"] is True
    bank = next(x for x in bal["items"] if x["subject_code"] == "1002")
    from app.services import journal_service
    from app.core.db import SessionLocal
    from app.core.timeutil import month_range
    s = SessionLocal()
    _, end = month_range(bal["period"])
    jb = sum((journal_service.summarize(s, a["key"], end, end)["closing"]
              for a in journal_service.account_list(s) if a["type"] == "bank"), D(0))
    s.close()
    assert D(bank["debit_balance"]) - D(bank["credit_balance"]) == jb  # 银行存款余额 = 银行日记账余额


def test_lemon_file_100pct_and_consistency(client, fin, db):
    from app.core.timeutil import prev_month, today
    from scripts.make_samples_finance import build_lemon_sample
    per = prev_month(f"{today():%Y-%m}")
    content = build_lemon_sample(db, per)
    r = data(up(client, "/finance-data/lemon/upload", fin, "lemon_vouchers_sample.xlsx", content))
    assert r["errors"] == [] and r["parse_rate"] == 1.0 and r["parsed_rows"] == r["total_rows"] > 0
    assert r["reconcile_status"] == "pass", r["diffs"][:3]
    c = data(client.get(f"{API}/finance-data/lemon/consistency", headers=fin, params={"period": per}))
    assert c["status"] == "pass" and c["diff_count"] == 0
    assert c["api"]["debit"] == c["file"]["debit"] and c["api"]["vouchers"] == c["file"]["vouchers"]


def test_lemon_file_row_errors_and_diff(client, fin, db):
    from openpyxl import load_workbook
    from app.core.timeutil import prev_month, today
    from scripts.make_samples_finance import build_lemon_sample
    per = prev_month(f"{today():%Y-%m}")
    wb = load_workbook(io.BytesIO(build_lemon_sample(db, per)))
    ws = wb.active
    ws.cell(5, 4).value = None  # 第 5 行科目编码缺失
    ws.cell(6, 1).value = "2026/99/01"  # 第 6 行日期异常
    ws.cell(7, 6).value = "abc"  # 第 7 行金额异常
    buf = io.BytesIO()
    wb.save(buf)
    r = data(up(client, "/finance-data/lemon/upload", fin, "bad.xlsx", buf.getvalue()))
    assert r["reconcile_status"] == "error"
    locs = {(e["row"], e["field"]) for e in r["errors"]}
    assert (5, "科目编码") in locs and (6, "凭证日期") in locs and any(x[0] == 7 for x in locs)
    # 金额被篡改的文件：解析成功但对账产生差异（与来源/合计行不符）
    wb = load_workbook(io.BytesIO(build_lemon_sample(db, per)))
    ws = wb.active
    v = ws.cell(4, 6).value or ws.cell(4, 7).value
    col = 6 if ws.cell(4, 6).value else 7
    ws.cell(4, col).value = float(v) + 100
    buf = io.BytesIO()
    wb.save(buf)
    r = data(up(client, "/finance-data/lemon/upload", fin, "tampered.xlsx", buf.getvalue()))
    assert r["reconcile_status"] == "diff"
    types = {d["type"] for d in r["diffs"]}
    assert {"unbalanced", "declared_total", "source_mismatch"} & types
    c = data(client.get(f"{API}/finance-data/lemon/consistency", headers=fin, params={"period": per}))
    assert c["status"] == "diff" and c["diff_count"] >= 1
    # 恢复：重新上传正确文件，一致性恢复通过
    data(up(client, "/finance-data/lemon/upload", fin, "lemon_ok.xlsx", build_lemon_sample(db, per)))
    assert data(client.get(f"{API}/finance-data/lemon/consistency", headers=fin, params={"period": per}))["status"] == "pass"


def test_lemon_supply(client, fin):
    from app.core.timeutil import prev_month, today
    s = data(client.get(f"{API}/finance-data/lemon/supply", headers=fin, params={"period": prev_month(f"{today():%Y-%m}")}))
    assert s["projects"] and s["subjects"]


# ====================== FR-09-05 税务申报 ======================
def test_tax_upload_and_list(client, fin, db):
    from app.core.timeutil import today
    from scripts.make_samples_finance import build_tax_sample
    t = data(client.get(f"{API}/finance-data/tax", headers=fin))
    periods = {x["period"] for x in t["items"]}
    assert len(periods) >= 3 and all(x["file"] for x in t["items"])
    r = data(up(client, "/finance-data/tax/upload", fin, "tax_vat_sample.xlsx", build_tax_sample("2026-06")))
    reg = {x["tax_type"]: x for x in r["registered"]}
    assert set(reg) == {"增值税", "附加税"}
    assert D(reg["增值税"]["payable_amount"]) == D("193740.00") and reg["增值税"]["period"] == "2026-06"
    assert D(reg["增值税"]["taxable_amount"]) == D("6458000.00")
    assert D(reg["附加税"]["payable_amount"]) == D("23248.80")
    t = data(client.get(f"{API}/finance-data/tax", headers=fin, params={"period": "2026-06"}))
    assert len(t["items"]) == 2 and t["items"][0]["file"]["category"] == "tax"
    # 再次上传同期 → 覆盖不重复
    r = data(up(client, "/finance-data/tax/upload", fin, "tax_vat_sample.xlsx", build_tax_sample("2026-06")))
    assert all(not x["created"] for x in r["registered"])
    # 无法识别 → 定位错误
    from openpyxl import Workbook
    wb = Workbook()
    wb.active.append(["随便一个表"])
    buf = io.BytesIO()
    wb.save(buf)
    r = data(up(client, "/finance-data/tax/upload", fin, "x.xlsx", buf.getvalue()))
    assert not r["registered"] and {e["field"] for e in r["errors"]} >= {"税种", "所属期"}


# ====================== 项目采购成本 ======================
def test_project_cost_crud(client, fin, db):
    from app.models import LogOperation
    r = data(client.post(f"{API}/finance-data/costs", headers=fin, json={
        "project_code": "JC-AF-2026-001", "cost_date": "2026-10-10", "cost_type": "分包", "amount": "58000",
        "summary": "测试分包"}))
    lst = data(client.get(f"{API}/finance-data/costs", headers=fin, params={"project": "JC-AF-2026-001", "keyword": "测试分包"}))
    assert lst["total"] == 1 and D(lst["amount"]) == D("58000")
    data(client.put(f"{API}/finance-data/costs/{r['id']}", headers=fin, json={
        "project_code": "JC-AF-2026-001", "cost_date": "2026-10-10", "cost_type": "分包", "amount": "60000", "summary": "测试分包"}))
    data(client.delete(f"{API}/finance-data/costs/{r['id']}", headers=fin))
    assert db.scalar(select(func.count()).select_from(LogOperation).where(
        LogOperation.target_type == "project_cost", LogOperation.target_id == str(r["id"]))) == 3
    assert client.post(f"{API}/finance-data/costs", headers=fin, json={
        "project_code": "NOPE", "cost_date": "2026-10-10", "amount": "1"}).json()["code"] == 40001


# ====================== RBAC ======================
def test_rbac(client, login):
    cashier, acct, mkt = login("zhaoli"), login("sunyan"), login("zhengyu")
    # 出纳：可导入/核对/记账，可看报销，不可补归集，无财务数据菜单
    assert client.get(f"{API}/flows/workbench", headers=cashier).json()["code"] == 0
    assert client.get(f"{API}/expenses", headers=cashier).json()["code"] == 0
    assert client.post(f"{API}/expenses/allocate", headers=cashier, json={"ids": [1], "project_code": None}).json()["code"] == 40301
    assert client.get(f"{API}/finance-data/tax", headers=cashier).json()["code"] == 40301
    # 会计：可查看流水、可补归集、可同步柠檬云；无导入/核对按钮，不可补录日记账
    assert client.get(f"{API}/flows", headers=acct).json()["code"] == 0
    assert up(client, "/flows/import", acct, "a.csv", b"x").json()["code"] == 40301
    assert client.post(f"{API}/flows/auto-match", headers=acct, json={"dry_run": True}).json()["code"] == 40301
    assert client.get(f"{API}/finance-data/lemon/batches", headers=acct).json()["code"] == 0
    assert client.get(f"{API}/journal", headers=acct).json()["code"] == 0
    assert client.post(f"{API}/journal", headers=acct, json={"journal_date": "2026-10-01", "direction": 2, "amount": "1",
                                                            "summary": "x"}).json()["code"] == 40301
    # 市场专员：全部 403
    for url in ("/expenses", "/flows", "/journal", "/finance-data/tax", "/finance-data/costs"):
        assert client.get(API + url, headers=mkt).json()["code"] == 40301, url
    assert up(client, "/flows/import", mkt, "a.csv", b"x").json()["code"] == 40301


# ====================== 定时任务 ======================
def test_jobs_bank_dir_and_lemon(db):
    from app.jobs.registry import run_job
    from app.services.bankflow_service import inbox_dir
    from scripts.make_samples_finance import build_cmb_500
    d = inbox_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "auto_cmb.xlsx").write_bytes(build_cmb_500(30))
    log = run_job("bank_flow_auto_parse", trigger="manual", force=True)
    assert log.status == "success" and "解析 1 个文件" in log.message
    log2 = run_job("bank_flow_auto_parse", trigger="manual", force=True)
    assert "解析 0 个文件" in log2.message  # 幂等：同一文件不重复解析
    log3 = run_job("lemoncloud_sync", trigger="manual", force=True)
    assert log3.status == "success" and log3.rows_affected > 0


# ====================== 前端调用接口冒烟 ======================
def test_view_endpoints_smoke(client, fin, cashier):
    from app.core.timeutil import prev_month, today
    per = prev_month(f"{today():%Y-%m}")
    o = data(client.get(f"{API}/expenses/options", headers=fin))
    assert o["projects"] and o["categories"] and o["can_allocate"] is True
    t = data(client.get(f"{API}/flows/templates", headers=cashier))
    assert {x["key"] for x in t["templates"]} >= {"cmb", "icbc", "ccb"}
    accts = data(client.get(f"{API}/flows/accounts", headers=cashier))
    assert accts and all("unmatched" in a for a in accts)
    lst = data(client.get(f"{API}/flows", headers=cashier, params={"status": "matched", "page_size": 3}))
    f = lst["items"][0]
    d = data(client.get(f"{API}/flows/{f['id']}", headers=cashier))
    assert d["journal"] and d["suggestions"] == []
    un = data(client.get(f"{API}/flows", headers=cashier, params={"status": "0", "direction": 1, "page_size": 1}))["items"][0]
    cands = data(client.get(f"{API}/flows/{un['id']}/candidates", headers=cashier, params={"kind": "payment"}))
    assert cands and all(D(c["balance"]) > 0 for c in cands)
    ja = data(client.get(f"{API}/journal/accounts", headers=cashier))
    assert ja["accounts"][0]["key"] == "cash" and ja["can_write"] is True
    b = data(client.get(f"{API}/finance-data/lemon/batches", headers=fin, params={"page_size": 50}))
    bd = data(client.get(f"{API}/finance-data/lemon/batches/{b['items'][-1]['id']}", headers=fin))
    assert "diffs" in bd
    rr = data(client.post(f"{API}/finance-data/lemon/batches/{bd['id']}/reconcile", headers=fin))
    assert rr["reconcile_status"] in ("pass", "diff")
    v = data(client.get(f"{API}/finance-data/lemon/vouchers", headers=fin, params={"period": per, "subject": "1002"}))
    assert v["total"] > 0 and all(x["subject_code"].startswith("1002") for x in v["items"])
    m = data(client.get(f"{API}/journal", headers=cashier, params={"mode": "month", "period": per}))
    assert m["start"].startswith(per) and m["daily"]
