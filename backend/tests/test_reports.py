"""报表中心 + 项目利润（FR-04 / 3.5 / 7.1 验收）。

运行：cd backend && TEST_DB=jucheng_test_rpt .venv/bin/python -m pytest tests/test_reports.py -q
"""
import io
import time
from datetime import datetime, timedelta
from decimal import Decimal

import openpyxl
import pytest
from sqlalchemy import delete, func, select

from tests.conftest import data

D = Decimal
TYPES = ["daily_expense", "daily_bankflow", "weekly_profit", "company_weekly", "company_monthly", "project_operation"]
FIN = "chenjing"  # CW-01


def _download(client, f) -> bytes:
    r = client.get(f["url"])
    assert r.status_code == 200, r.text[:200]
    return r.content


def _gen(client, login, report_type, period=None, user=FIN, **kw):
    return data(client.post("/api/v1/reports/generate", headers=login(user),
                            json={"report_type": report_type, "period": period, **kw}))


# ---------------------------------------------------------------- 目录 / 列表 / 种子
def test_catalog_and_seeded_history(client, login):
    t = data(client.get("/api/v1/reports/types", headers=login(FIN)))
    keys = {x["report_type"] for x in t["types"]}
    assert set(TYPES) <= keys and t["can_generate"]
    for x in t["types"]:
        assert x["caliber"] and x["schedule"]
    lst = data(client.get("/api/v1/reports", params={"report_type": "weekly_profit", "page_size": 50}, headers=login(FIN)))
    assert lst["total"] >= 4  # 种子：近 4 周
    assert all(i["trigger"] in ("schedule", "manual", "mcp") and i["files"] for i in lst["items"])


@pytest.mark.parametrize("rtype", TYPES)
def test_generate_each_type_files_valid(client, login, rtype):
    r = _gen(client, login, rtype)
    assert r["version"] >= 1 and r["summary"] and r["files"]
    for f in r["files"]:
        assert f["filename"].endswith((".xlsx", ".docx"))
        blob = _download(client, f)
        if f["filename"].endswith(".xlsx"):
            wb = openpyxl.load_workbook(io.BytesIO(blob))
            assert "口径说明" in wb.sheetnames
            ws = wb[wb.sheetnames[0]]
            assert ws.freeze_panes == "A2"
        else:
            import docx
            d = docx.Document(io.BytesIO(blob))
            assert any("口径" in p.text for p in d.paragraphs)


def test_files_archived_by_type_and_month(db, client, login):
    from app.models import RptReport, SysFile
    r = _gen(client, login, "daily_expense", "2026-10-15")
    rpt = db.get(RptReport, r["id"])
    for fid in rpt.file_ids:
        assert db.get(SysFile, fid).storage_key.startswith("report/daily_expense/2026-10/")


# ---------------------------------------------------------------- 一致性：每日支出合计 == 数据库聚合
def _db_daily_total(db, d):
    from app.core.timeutil import day_bounds
    from app.models import BizExpenseReport, FinBankFlow
    t0, t1 = day_bounds(d)
    exp = db.scalar(select(func.coalesce(func.sum(BizExpenseReport.amount), 0)).where(
        BizExpenseReport.approved_at >= t0, BizExpenseReport.approved_at < t1, BizExpenseReport.status.in_([2, 4])))
    flow = db.scalar(select(func.coalesce(func.sum(FinBankFlow.amount), 0)).where(
        FinBankFlow.trade_date == d, FinBankFlow.direction == 2, FinBankFlow.match_status.in_([3, 4])))
    return D(exp), D(flow)


@pytest.mark.parametrize("back", [1, 2, 5, 8])
def test_daily_expense_total_equals_db(db, client, login, back):
    from app.core.timeutil import today
    from app.models import RptDailyExpense
    d = today() - timedelta(days=back)
    r = _gen(client, login, "daily_expense", d.isoformat())
    m = data(client.get(f"/api/v1/reports/{r['id']}", headers=login(FIN)))["metrics"]
    exp, flow = _db_daily_total(db, d)
    assert D(m["expense_total"]) == exp and D(m["flow_total"]) == flow and D(m["total"]) == exp + flow
    assert sum(D(x["total"]) for x in m["by_dept"]) == exp + flow
    assert sum(D(x["total"]) for x in m["by_project"]) == exp + flow
    snaps = db.scalars(select(RptDailyExpense).where(RptDailyExpense.report_id == r["id"])).all()
    assert sum(s.total_amount for s in snaps if s.dimension == "dept") == exp + flow
    assert sum(s.total_amount for s in snaps if s.dimension == "project") == exp + flow
    # xlsx 明细合计行与数据库一致
    wb = openpyxl.load_workbook(io.BytesIO(_download(client, r["files"][0])))
    ws = wb["支出明细"]
    total_row = [row for row in ws.iter_rows(values_only=True) if row[0] == "合计"][0]
    assert D(str(total_row[9])) == exp + flow


# ---------------------------------------------------------------- 一致性：报销归集 → 日报表 → 周利润表
def test_expense_consistent_across_allocation_daily_and_weekly_profit(db, client, login):
    from app.core.timeutil import TZ, today, week_no, week_range
    from app.models import BizExpenseReport, BizProject, SysUser
    from app.services import profit_service
    T = today()
    d = T - timedelta(days=1)  # 本周一（FAKE_TODAY=周二）
    wk = week_no(d)
    code = db.scalar(select(BizProject.project_code).where(BizProject.status == 2).order_by(BizProject.project_code))
    u = db.scalar(select(SysUser).where(SysUser.wecom_userid == "xuhao"))
    before = data(client.get("/api/v1/reports/profit", params={"week": wk, "project_code": code}, headers=login(FIN)))
    b_row = before["rows"][0] if before["rows"] else {"period_expense": "0", "cost": "0"}
    amt = D("3580.37")
    e = BizExpenseReport(approval_no="RPT-TEST-BX-0451", applicant_id=u.id, dept_code="XM", project_code=code,
                         amount=amt, expense_date=d, approved_at=datetime(d.year, d.month, d.day, 11, 23, tzinfo=TZ),
                         category="差旅费", reason="一致性验收", status=4)
    db.add(e)
    db.commit()
    try:
        # ① 报销归集：台账中该单已归集到项目
        assert db.scalar(select(BizExpenseReport.project_code).where(BizExpenseReport.approval_no == e.approval_no)) == code
        # ② 日报表：明细中出现且金额一致；项目维度含该金额
        r = _gen(client, login, "daily_expense", d.isoformat())
        m = data(client.get(f"/api/v1/reports/{r['id']}", headers=login(FIN)))["metrics"]
        hit = [i for i in m["items"] if i["ref"] == e.approval_no]
        assert len(hit) == 1 and D(hit[0]["amount"]) == amt and hit[0]["project_key"] == code
        wb = openpyxl.load_workbook(io.BytesIO(_download(client, r["files"][0])))
        xl = [row for row in wb["支出明细"].iter_rows(values_only=True) if row[1] == e.approval_no]
        assert len(xl) == 1 and D(str(xl[0][9])) == amt
        # ③ 周利润表：该项目本周报销费用增加同一金额；与 profit_service 一致
        w = _gen(client, login, "weekly_profit", wk)
        wm = data(client.get(f"/api/v1/reports/{w['id']}", headers=login(FIN)))["metrics"]
        prow = next(x for x in wm["rows"] if x["project_code"] == code)
        assert D(prow["period_expense"]) - D(b_row["period_expense"]) == amt
        assert D(prow["cost"]) - D(b_row["cost"]) == amt
        svc = next(x for x in profit_service.project_profit(db, *week_range(wk))["rows"] if x["project_code"] == code)
        assert D(prow["period_expense"]) == svc["period_expense"]
        # 利润下钻：归集报销清单含该单
        dr = data(client.get(f"/api/v1/reports/profit/{code}", params={"week": wk}, headers=login(FIN)))
        dx = [x for x in dr["detail"]["expenses"] if x["approval_no"] == e.approval_no]
        assert len(dx) == 1 and D(dx[0]["amount"]) == amt and dx[0]["in_period"]
        wbx = openpyxl.load_workbook(io.BytesIO(_download(client, w["files"][0])))
        hdr = [c.value for c in wbx["项目利润表"][1]]
        row = [r_ for r_ in wbx["项目利润表"].iter_rows(values_only=True) if r_[0] == code][0]
        assert D(str(row[hdr.index("本周报销费用")])) == D(prow["period_expense"])
    finally:
        db.execute(delete(BizExpenseReport).where(BizExpenseReport.approval_no == "RPT-TEST-BX-0451"))
        db.commit()


# ---------------------------------------------------------------- 周利润表 == profit_service
def test_weekly_profit_equals_profit_service(db, client, login):
    from app.core.timeutil import last_week, week_range
    from app.models import RptProjectProfit
    from app.services import profit_service
    wk = last_week()
    r = _gen(client, login, "weekly_profit", wk)
    m = data(client.get(f"/api/v1/reports/{r['id']}", headers=login(FIN)))["metrics"]
    svc = profit_service.project_profit(db, *week_range(wk))
    assert D(m["totals"]["gross_profit"]) == svc["totals"]["gross_profit"]
    assert D(m["totals"]["income"]) == svc["totals"]["income"]
    assert D(m["public_expense"]) == svc["public_expense"]
    by = {x["project_code"]: x for x in svc["rows"]}
    assert len(m["rows"]) == len(by)
    for row in m["rows"]:
        s = by[row["project_code"]]
        assert D(row["gross_profit"]) == s["gross_profit"] and D(row["period_cost"]) == s["period_cost"]
        if s["gross_margin"] is not None:
            assert D(row["gross_margin"]) == s["gross_margin"]
    snaps = db.scalars(select(RptProjectProfit).where(RptProjectProfit.report_id == r["id"])).all()
    assert {s.project_code: s.gross_profit for s in snaps} == {k: v["gross_profit"] for k, v in by.items()}
    # 项目利润接口与周利润表一致
    api = data(client.get("/api/v1/reports/profit", params={"week": wk}, headers=login(FIN)))
    assert D(api["kpi"]["gross_profit"]) == svc["totals"]["gross_profit"]
    assert api["report"] and api["report"]["version"] == r["version"]


def test_weekly_profit_low_margin_flag_and_public_expense(client, login):
    api = data(client.get("/api/v1/reports/profit", headers=login(FIN)))
    th = D(api["alert_margin"])
    for r in api["rows"]:
        if r["gross_margin"] is not None:
            assert r["low_margin"] == (D(r["gross_margin"]) < th)
    assert D(api["public_expense"]) == sum(D(x["amount"]) for x in api["public_detail"])


def test_profit_by_range_and_trend(client, login):
    from app.core.timeutil import last_week
    r = data(client.get("/api/v1/reports/profit", params={"start": "2026-10-01", "end": "2026-10-18"},
                        headers=login(FIN)))
    assert r["week"] is None and r["rows"]
    tr = data(client.get("/api/v1/reports/profit/trend", headers=login(FIN)))["weeks"]
    assert len(tr) == 12 and tr[-1]["week"] == last_week()
    cur = data(client.get("/api/v1/reports/profit", params={"week": last_week()}, headers=login(FIN)))
    assert D(tr[-1]["gross_profit"]) == D(cur["kpi"]["gross_profit"])
    full = data(client.get("/api/v1/reports/profit", params={"include_detail": True}, headers=login(FIN)))
    row = full["rows"][0]
    exp = sum(D(x["amount"]) for x in row["detail"]["expenses"])
    cost = sum(D(x["amount"]) for x in row["detail"]["costs"])
    assert exp == D(row["cost_expense"]) and cost == D(row["cost_project"])
    assert sum(D(c["amount"]) for c in row["detail"]["contracts"] if c["counted"]) == D(row["income"])


def test_generate_by_project(client, login):
    from app.core.timeutil import last_week
    r = _gen(client, login, "weekly_profit", last_week(), project_codes=["JC-XY-2026-001"])
    assert r["period"].endswith(":JC-XY-2026-001")
    m = data(client.get(f"/api/v1/reports/{r['id']}", headers=login(FIN)))["metrics"]
    assert [x["project_code"] for x in m["rows"]] == ["JC-XY-2026-001"]
    bad = client.post("/api/v1/reports/generate", headers=login(FIN),
                      json={"report_type": "weekly_profit", "project_codes": ["JC-NOPE"]})
    assert bad.json()["code"] == 40001


# ---------------------------------------------------------------- 版本 / 耗时 / 列表去重
def test_regenerate_bumps_version_and_list_shows_latest(client, login):
    a = _gen(client, login, "project_operation", "2026-W41")
    b = _gen(client, login, "project_operation", "2026-W41")
    assert b["version"] == a["version"] + 1
    lst = data(client.get("/api/v1/reports", params={"report_type": "project_operation", "period": "2026-W41"},
                          headers=login(FIN)))
    assert lst["total"] == 1 and lst["items"][0]["version"] == b["version"] and lst["items"][0]["versions"] >= 2
    d = data(client.get(f"/api/v1/reports/{b['id']}", headers=login(FIN)))
    assert [h["version"] for h in d["history"]][:2] == [b["version"], a["version"]]
    assert b["files"][0]["version"] == a["files"][0]["version"] + 1  # 归档文件同 key 版本递增


def test_manual_generate_under_30s(client, login):
    for rtype in TYPES:
        t0 = time.perf_counter()
        _gen(client, login, rtype)
        assert time.perf_counter() - t0 < 30


def test_regenerate_all(client, login):
    r = data(client.post("/api/v1/reports/regenerate-all", headers=login(FIN)))
    assert r["failed"] == 0 and r["success"] == r["total"] >= 10
    assert {x["report_type"] for x in r["results"]} == set(TYPES)


def test_future_and_bad_period(client, login):
    assert client.post("/api/v1/reports/generate", headers=login(FIN),
                       json={"report_type": "daily_expense", "period": "2027-01-01"}).json()["code"] == 40001
    assert client.post("/api/v1/reports/generate", headers=login(FIN),
                       json={"report_type": "weekly_profit", "period": "W99"}).json()["code"] == 40001


# ---------------------------------------------------------------- 月报：柠檬云缺失注明 / 有数据时汇总
def test_monthly_marks_missing_lemon_data(db, client, login):
    from app.models import FinVoucher
    r = _gen(client, login, "company_monthly", "2026-04")
    m = data(client.get(f"/api/v1/reports/{r['id']}", headers=login(FIN)))["metrics"]
    has = db.scalar(select(func.count()).select_from(FinVoucher).where(FinVoucher.voucher_date.between(
        datetime(2026, 4, 1).date(), datetime(2026, 4, 30).date())))
    if not has:
        assert "柠檬云凭证" in m["missing"]
        wb = openpyxl.load_workbook(io.BytesIO(_download(client, next(f for f in r["files"] if f["filename"].endswith("xlsx")))))
        assert wb["凭证汇总-科目大类"]["A2"].value == "数据缺失"


def test_monthly_lemon_summary_matches_vouchers(db, client, login):
    from app.models import FinVoucher
    rows = [FinVoucher(voucher_no="记-T01", line_no=1, voucher_date=datetime(2026, 3, 10).date(), summary="确认收入",
                       amount=D("100000"), direction=1, subject_code="1122", subject_name="应收账款", sync_batch="RPT-T"),
            FinVoucher(voucher_no="记-T01", line_no=2, voucher_date=datetime(2026, 3, 10).date(), summary="确认收入",
                       amount=D("100000"), direction=2, subject_code="6001", subject_name="主营业务收入", sync_batch="RPT-T"),
            FinVoucher(voucher_no="记-T02", line_no=1, voucher_date=datetime(2026, 3, 12).date(), summary="结转成本",
                       amount=D("60000"), direction=1, subject_code="6401", subject_name="主营业务成本", sync_batch="RPT-T"),
            FinVoucher(voucher_no="记-T03", line_no=1, voucher_date=datetime(2026, 3, 15).date(), summary="办公费",
                       amount=D("5000"), direction=1, subject_code="6602", subject_name="管理费用", sync_batch="RPT-T")]
    existing = db.scalar(select(func.count()).select_from(FinVoucher).where(FinVoucher.voucher_date.between(
        datetime(2026, 3, 1).date(), datetime(2026, 3, 31).date())))
    if existing:
        pytest.skip("2026-03 已有柠檬云种子数据")
    db.add_all(rows)
    db.commit()
    try:
        r = _gen(client, login, "company_monthly", "2026-03")
        m = data(client.get(f"/api/v1/reports/{r['id']}", headers=login(FIN)))["metrics"]
        assert m["lemon"]["available"] and "柠檬云凭证" not in m["missing"]
        cls = {c["class"]: c for c in m["lemon"]["by_class"]}
        assert D(cls["损益类"]["credit"]) == D("100000") and D(cls["资产类"]["debit"]) == D("100000")
        a = m["lemon"]["analysis"]
        assert D(a["income"]) == D("100000") and D(a["expense"]) == D("5000")
        assert D(a["cost"]) == 0  # 摘要含「结转」不计入
    finally:
        db.execute(delete(FinVoucher).where(FinVoucher.sync_batch == "RPT-T"))
        db.commit()


def test_bankflow_balance_check(db, client, login):
    from app.models import FinBankFlow
    d = db.scalar(select(FinBankFlow.trade_date).where(FinBankFlow.direction == 1).order_by(FinBankFlow.trade_date.desc()))
    r = _gen(client, login, "daily_bankflow", d.isoformat())
    m = data(client.get(f"/api/v1/reports/{r['id']}", headers=login(FIN)))["metrics"]
    for a in m["accounts"]:
        if a["opening"] is not None:
            assert D(a["opening"]) + D(a["income"]) - D(a["outflow"]) == D(a["closing"])
    cnt = db.scalar(select(func.count()).select_from(FinBankFlow).where(FinBankFlow.trade_date == d))
    assert m["flow_count"] == cnt
    assert m["unmatched_count"] == db.scalar(select(func.count()).select_from(FinBankFlow).where(
        FinBankFlow.trade_date == d, FinBankFlow.match_status == 0))


# ---------------------------------------------------------------- 定时任务：幂等 + 推送 outbox
def test_jobs_registered_with_prd_names():
    from app.jobs import load_all, registry
    load_all()
    for name in ("daily_expense_report", "weekly_project_profit", "company_weekly_report", "monthly_company_report",
                 "project_operation_report", "daily_bankflow_report"):
        assert name in registry.JOBS


def test_daily_expense_job_idempotent_and_pushes(db):
    from app.integration.wecom.mock import read_outbox
    from app.jobs import load_all, registry
    from app.models import RptReport, SysNotify
    from app.services import notify_service
    load_all()
    period = "2026-10-12"
    n0 = db.scalar(select(func.count()).select_from(RptReport).where(RptReport.report_type == "daily_expense",
                                                                     RptReport.period == period))
    e1 = registry.run_job("daily_expense_report", {"period": period})
    assert e1.status == "success", e1.error_msg
    assert e1.idempotency_key == f"daily_expense_report:{period}"
    e2 = registry.run_job("daily_expense_report", {"period": period})
    assert e2.status == "skipped"
    db.expire_all()
    n1 = db.scalar(select(func.count()).select_from(RptReport).where(RptReport.report_type == "daily_expense",
                                                                     RptReport.period == period))
    assert n1 == n0 + 1
    rpt = db.scalar(select(RptReport).where(RptReport.report_type == "daily_expense", RptReport.period == period)
                    .order_by(RptReport.version.desc()))
    assert rpt.trigger == "schedule"
    out = [o for o in read_outbox(200) if rpt.title in o["title"]]
    assert out and f"/reports?id={rpt.id}" in (out[-1]["url"] or "")
    to = set(out[-1]["to"])
    expect = {u.wecom_userid for u in notify_service.users_by_roles(db, ["CW-01", "ZB-01"])}
    assert to == expect
    inapp = db.scalars(select(SysNotify).where(SysNotify.ref_type == "rpt_report", SysNotify.ref_id == str(rpt.id),
                                               SysNotify.channel == "inapp")).all()
    assert len(inapp) == len(expect)


def test_weekly_and_monthly_jobs(db):
    from app.jobs import load_all, registry
    load_all()
    for name, params in (("weekly_project_profit", {"period": "2026-W38"}), ("company_weekly_report", {"period": "2026-W38"}),
                         ("project_operation_report", {"period": "2026-W38"}), ("monthly_company_report", {"period": "2026-08"}),
                         ("daily_bankflow_report", {"period": "2026-10-11"})):
        e = registry.run_job(name, params)
        assert e.status == "success", (name, e.error_msg)
        assert registry.run_job(name, params).status == "skipped"


# ---------------------------------------------------------------- RBAC
def test_rbac_market_staff_cannot_access_profit(client, login):
    sc = login("zhengyu")  # SC-02 市场专员
    assert client.get("/api/v1/reports/profit", headers=sc).json()["code"] == 40301
    assert client.get("/api/v1/reports/profit/trend", headers=sc).json()["code"] == 40301
    assert client.get("/api/v1/reports/profit/JC-XY-2026-001", headers=sc).json()["code"] == 40301
    types = {t["report_type"] for t in data(client.get("/api/v1/reports/types", headers=sc))["types"]}
    assert not types & {"weekly_profit", "daily_expense", "company_monthly", "daily_bankflow"}
    lst = data(client.get("/api/v1/reports", params={"report_type": "weekly_profit"}, headers=sc))
    assert lst["total"] == 0
    r = client.post("/api/v1/reports/generate", headers=sc, json={"report_type": "weekly_profit"})
    assert r.json()["code"] == 40301


def test_rbac_detail_and_generate_buttons(client, login):
    lst = data(client.get("/api/v1/reports", params={"report_type": "weekly_profit"}, headers=login(FIN)))
    rid = lst["items"][0]["id"]
    assert client.get(f"/api/v1/reports/{rid}", headers=login("zhengyu")).json()["code"] == 40301
    # 出纳：可看每日支出/流水报表，但无 report:generate
    cashier = login("zhaoli")
    types = {t["report_type"] for t in data(client.get("/api/v1/reports/types", headers=cashier))["types"]}
    assert {"daily_expense", "daily_bankflow"} <= types
    assert client.post("/api/v1/reports/generate", headers=cashier,
                       json={"report_type": "daily_expense"}).json()["code"] == 40301
    # 总办可访问项目利润
    assert data(client.get("/api/v1/reports/profit", headers=login("limingyuan")))["rows"]
    # 普通员工无报表中心
    assert client.get("/api/v1/reports/types", headers=login("zengli")).json()["code"] == 40301
