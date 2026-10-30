"""营销管控部：商机信息表（FR-05-02）、业绩统计（FR-05-07 / PRD 3.4）、项目运营看板、Excel 数据清洗助手（PRD 7.4）。"""
import io
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook
from sqlalchemy import func, select

from tests.conftest import data

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
YX = "songjia"  # 营销管控专员
D = Decimal


# ---------------- 商机信息表 ----------------
def test_funnel_matches_list(client, login):
    h = login(YX)
    f = data(client.get("/api/v1/opportunities/funnel", headers=h))
    assert [s["stage"] for s in f["stages"]] == ["lead", "contact", "quote", "negotiation", "won", "lost"]
    for s in f["stages"]:
        lst = data(client.get("/api/v1/opportunities", params={"stage": s["stage"], "page_size": 200}, headers=h))
        assert lst["total"] == s["count"]
        assert D(lst["totals"]["amount"]) == D(s["amount"])
        assert sum(D(i["amount"]) for i in lst["items"]) == D(s["amount"])
    all_ = data(client.get("/api/v1/opportunities", params={"page_size": 200}, headers=h))
    assert all_["total"] == f["total"]["count"]
    assert f["win_rate"] == round(f["won"]["count"] / (f["won"]["count"] + f["lost"]["count"]), 4)
    assert f["new_month"]["count"] >= f["new_week"]["count"]


def test_list_filters(client, login):
    h = login(YX)
    r = data(client.get("/api/v1/opportunities", params={"business_line": "KP", "page_size": 200}, headers=h))
    assert r["total"] > 0 and all(i["business_line"] == "KP" for i in r["items"])
    r = data(client.get("/api/v1/opportunities", params={"customer": "教育局", "source": 1}, headers=h))
    assert all("教育局" in i["customer_name"] and i["source_name"] == "企微商机工单自动登记" for i in r["items"])


def test_wecom_opportunity_approval_auto_registers(client, login):
    h = login(YX)
    before = data(client.get("/api/v1/opportunities/funnel", headers=h))
    body = {"template": "opportunity", "applicant": "zhengyu",
            "fields": {"商机名称": "测试-罗湖区智慧校园", "客户名称": "深圳市罗湖区教育局", "预计金额": "3200000",
                       "业务线": "XY", "商机阶段": "初步接触", "预计签约日期": "2027-01-15", "备注": "测试"}}
    r = data(client.post("/api/v1/dev/mock/wecom-approval", json=body, headers=login("admin")))
    assert r["dispatch_status"] == 1
    lst = data(client.get("/api/v1/opportunities", params={"keyword": "罗湖区智慧校园"}, headers=h))
    assert lst["total"] == 1
    o = lst["items"][0]
    assert o["source_type"] == 1 and o["source_approval_no"] == r["sp_no"] and o["stage"] == "contact"
    after = data(client.get("/api/v1/opportunities/funnel", headers=h))
    cnt = {s["stage"]: s["count"] for s in after["stages"]}
    assert cnt["contact"] == {s["stage"]: s["count"] for s in before["stages"]}["contact"] + 1
    d = data(client.get(f"/api/v1/opportunities/{o['id']}", headers=h))
    assert d["history"][0]["title"] == "企微商机工单自动登记"


def test_manual_create_advance_history_and_contract_draft(client, login, db):
    from app.models import LogOperation
    h = login(YX)
    o = data(client.post("/api/v1/opportunities", json={"name": "测试-人工商机", "customer_name": "广州市海珠区教育局",
                                                        "business_line": "AF", "amount": "880000"}, headers=h))
    assert o["source_type"] == 2 and o["stage"] == "lead" and o["opp_no"].startswith("SJ-")
    assert client.post(f"/api/v1/opportunities/{o['id']}/contract-draft", headers=h).json()["code"] == 40901
    for _ in range(3):
        o = data(client.post(f"/api/v1/opportunities/{o['id']}/advance", json={"note": "推进"}, headers=h))
    assert o["stage"] == "negotiation"
    assert client.post(f"/api/v1/opportunities/{o['id']}/advance", json={}, headers=h).json()["code"] == 40001
    o = data(client.post(f"/api/v1/opportunities/{o['id']}/advance", json={"to_stage": "won"}, headers=h))
    assert o["status"] == "won"
    stages = [x for x in o["history"] if x["action"] == "stage"]
    assert [x["stage"] for x in stages] == ["lead", "contact", "quote", "negotiation", "won"]
    assert db.scalar(select(func.count()).select_from(LogOperation).where(
        LogOperation.action == "opportunity.stage", LogOperation.target_id == str(o["id"]))) == 5
    draft = data(client.post(f"/api/v1/opportunities/{o['id']}/contract-draft", headers=h))
    assert draft["customer_name"] == "广州市海珠区教育局" and D(draft["amount"]) == D("880000")
    assert draft["source_opp_no"] == o["opp_no"]
    # 丢单需原因
    o2 = data(client.post("/api/v1/opportunities", json={"name": "测试-丢单商机", "customer_name": "某校",
                                                         "business_line": "XY", "amount": 10000, "stage": "quote"}, headers=h))
    assert client.post(f"/api/v1/opportunities/{o2['id']}/advance", json={"to_stage": "lost"}, headers=h).json()["code"] == 40001
    o2 = data(client.post(f"/api/v1/opportunities/{o2['id']}/advance", json={"to_stage": "lost", "lost_reason": "预算取消"}, headers=h))
    assert o2["lost_reason"] == "预算取消" and o2["status"] == "lost"
    # 编辑留痕
    data(client.put(f"/api/v1/opportunities/{o2['id']}", json={"amount": "12000"}, headers=h))
    assert db.scalar(select(func.count()).select_from(LogOperation).where(
        LogOperation.action == "opportunity.update", LogOperation.target_id == str(o2["id"]))) == 1


def test_seed_stage_history(client, login, db):
    from app.models import BizOpportunity
    h = login(YX)
    won = db.scalar(select(BizOpportunity).where(BizOpportunity.name == "松山湖实验学校二期"))
    hist = data(client.get(f"/api/v1/opportunities/{won.id}/history", headers=h))
    assert [x["stage"] for x in hist if x["action"] == "stage"][-1] == "won"
    lost = db.scalar(select(BizOpportunity).where(BizOpportunity.name == "佛山禅城教育网升级"))
    assert data(client.get(f"/api/v1/opportunities/{lost.id}", headers=h))["lost_reason"]


def test_opportunity_export_matches_list(client, login):
    h = login(YX)
    r = client.get("/api/v1/opportunities/export", params={"business_line": "XY"}, headers=h)
    assert r.status_code == 200
    wb = load_workbook(io.BytesIO(r.content))
    assert "口径说明" in wb.sheetnames
    total = data(client.get("/api/v1/opportunities", params={"business_line": "XY"}, headers=h))["total"]
    assert wb["商机信息表"].max_row == total + 2  # 表头 + 合计


def test_opportunity_rbac(client, login):
    assert client.get("/api/v1/opportunities", headers=login("zhaoli")).json()["code"] == 40301
    # 市场专员可看不可写
    assert client.get("/api/v1/opportunities", headers=login("zhengyu")).json()["code"] == 0
    r = client.post("/api/v1/opportunities", json={"name": "x", "customer_name": "y", "amount": 1}, headers=login("zhengyu"))
    assert r.json()["code"] == 40301


# ---------------- 业绩统计 ----------------
def _sql_sign(db, start, end):
    from app.models import BizContract
    return D(db.scalar(select(func.coalesce(func.sum(BizContract.amount), 0)).where(
        BizContract.is_deleted.is_(False), BizContract.status != 3, BizContract.sign_date.between(start, end))))


def _sql_pay(db, start, end):
    from app.models import BizContract, BizPayment
    return D(db.scalar(select(func.coalesce(func.sum(BizPayment.amount), 0)).join(BizPayment.contract).where(
        BizContract.is_deleted.is_(False), BizPayment.payment_date.between(start, end))))


def test_performance_matches_sql(client, login, db):
    ov = data(client.get("/api/v1/performance/overview", params={"period": "2026-10"}, headers=login(YX)))
    T = date(2026, 10, 20)
    assert D(ov["month_sign"]["amount"]) == _sql_sign(db, date(2026, 10, 1), T)
    assert D(ov["year_sign"]["amount"]) == _sql_sign(db, date(2026, 1, 1), T)
    assert D(ov["month_pay"]["amount"]) == _sql_pay(db, date(2026, 10, 1), T)
    assert D(ov["month_pay"]["amount"]) >= D("4860000")  # 核心基线（其他测试可能新增回款）
    assert D(ov["month_pay"]["target"]) == D("7000000")
    assert D(ov["year_sign"]["last_year"]) == _sql_sign(db, date(2025, 1, 1), date(2025, 10, 20))
    assert sum(D(x["month_sign"]) for x in ov["by_line"]) == D(ov["month_sign"]["amount"])
    assert sum(D(x["year_sign"]) for x in ov["by_owner"]) == D(ov["year_sign"]["amount"])
    assert D(ov["year_sign"]["target"]) == D("50000000")
    assert len(ov["trend"]) == 12 and ov["trend"][-1]["period"] == "2026-10"
    assert D(ov["trend"][-1]["sign"]) == D(ov["month_sign"]["amount"])
    sept = next(t for t in ov["trend"] if t["period"] == "2026-09")
    assert D(sept["sign"]) == _sql_sign(db, date(2026, 9, 1), date(2026, 9, 30))
    # 历史月份统计至月末
    prev = data(client.get("/api/v1/performance/overview", params={"period": "2026-09"}, headers=login(YX)))
    assert D(prev["month_pay"]["amount"]) == _sql_pay(db, date(2026, 9, 1), date(2026, 9, 30))


def test_operation_board(client, login, db):
    from app.models import BizContract, BizPayment, BizPaymentPlan, BizProject
    b = data(client.get("/api/v1/performance/board", headers=login(YX)))
    codes = {r["project_code"] for r in b["projects"]}
    expect = set(db.scalars(select(BizProject.project_code).where(BizProject.status.in_([2, 3, 4]),
                                                                  BizProject.is_deleted.is_(False))))
    assert codes == expect
    T = date(2026, 10, 20)
    for r in b["projects"]:
        amt = D(db.scalar(select(func.coalesce(func.sum(BizContract.amount), 0)).where(
            BizContract.project_code == r["project_code"], BizContract.status != 3, BizContract.is_deleted.is_(False))))
        rec = D(db.scalar(select(func.coalesce(func.sum(BizPayment.amount), 0)).join(BizPayment.contract).where(
            BizContract.project_code == r["project_code"], BizContract.status != 3, BizContract.is_deleted.is_(False))))
        assert D(r["contract_amount"]) == amt and D(r["received"]) == rec
        assert r["pay_rate"] == (round(float(rec / amt), 4) if amt else None)
    # 账龄：到期未收清的计划
    rows = db.execute(select(BizPaymentPlan.amount, BizPaymentPlan.received_amount, BizPaymentPlan.due_date)
                      .join(BizPaymentPlan.contract).where(BizContract.project_code.in_(codes), BizContract.status != 3,
                                                           BizContract.is_deleted.is_(False),
                                                           BizPaymentPlan.due_date < T)).all()
    outstanding = [(a - r_, (T - d).days) for a, r_, d in rows if a > r_]
    assert D(b["aging"]["total"]) == sum((x for x, _ in outstanding), D(0))
    b180 = next(x for x in b["aging"]["buckets"] if x["bucket"] == "180+")
    assert D(b180["amount"]) == sum((x for x, d in outstanding if d > 180), D(0))
    assert len(b["top_customers"]) <= 10
    assert sum(D(x["contract_amount"]) for x in b["by_line"]) == D(b["summary"]["contract_amount"])
    # 毛利与 profit_service 一致
    from app.services import profit_service
    prof = {x["project_code"]: x for x in profit_service.project_profit(db, date(2026, 1, 1), T, list(codes))["rows"]}
    for r in b["projects"]:
        if r["project_code"] in prof:
            assert D(r["gross_profit"]) == prof[r["project_code"]]["gross_profit"]


def test_performance_exports_and_jobs(client, login, db):
    from app.jobs.registry import run_job
    from app.models import RptReport, SysNotify
    h = login(YX)
    for url, sheet in [("/api/v1/performance/export", "按业务线"), ("/api/v1/performance/board/export", "运营项目")]:
        r = client.get(url, headers=h)
        assert r.status_code == 200
        wb = load_workbook(io.BytesIO(r.content))
        assert sheet in wb.sheetnames and "口径说明" in wb.sheetnames
    import app.jobs
    app.jobs.load_all()
    n0 = db.scalar(select(func.count()).select_from(SysNotify).where(SysNotify.msg_type == "report"))
    log = run_job("performance_snapshot", {}, trigger="manual", force=True)
    assert log.status == "success", log.error_msg
    log = run_job("operation_board_weekly", {}, trigger="manual", force=True)
    assert log.status == "success", log.error_msg
    db.expire_all()
    rpt = db.scalar(select(RptReport).where(RptReport.report_type == "performance_monthly", RptReport.period == "2026-09"))
    assert rpt and rpt.file_ids
    assert db.scalar(select(RptReport.id).where(RptReport.report_type == "operation_board"))
    assert db.scalar(select(func.count()).select_from(SysNotify).where(SysNotify.msg_type == "report")) > n0
    reps = data(client.get("/api/v1/performance/reports", headers=h))
    assert any(x["report_type"] == "performance_monthly" and x["files"] for x in reps)


# ---------------- Excel 数据清洗 ----------------
def test_parsers():
    from app.services.clean_service import norm_customer, norm_line, norm_project_code, parse_date, parse_money
    for s in ["2026/10/1", "2026.10.01", "20261001", "2026年10月1日", "10/1/2026", 46296, "十月一日", "2026-10-01"]:
        assert parse_date(s)[0] == date(2026, 10, 1), s
    for s, v in [("¥1,234.50", "1234.50"), ("￥１２３４", "1234.00"), ("RMB 5,000元", "5000.00"), ("(300)", "-300.00"),
                 ("12.5万", "125000.00"), ("1.2亿", "120000000.00")]:
        assert parse_money(s)[0] == D(v), s
    assert parse_money("30", "万元")[0] == D("300000")
    assert norm_project_code("JC-XY-2026-1")[0] == "JC-XY-2026-001"
    assert norm_line("综合智慧安防")[0] == "AF" and norm_line("运维服务")[0] == "YW"
    assert norm_customer(" 广东海洋大学（盖章） ")[0] == "广东海洋大学"


def _preview(client, h, path: Path, **fields):
    with open(path, "rb") as f:
        r = client.post("/api/v1/clean/preview", files={"file": (path.name, f.read())}, data=fields, headers=h)
    return data(r)


def _accuracy(job, expected):
    rows = {r["_row"]: r for r in job["rows"]}
    total = hit = 0
    misses = []
    for e in expected["rows"]:
        r = rows.get(e["source_row"])
        total += 1
        if r and r["_status"] == e["status"]:
            hit += 1
        else:
            misses.append((e["seq"], "status", e["status"], r and r["_status"]))
        if e["status"] == "error":
            total += 1
            if r and any(e["issue_contains"] in i for i in r["_issues"]):
                hit += 1
            else:
                misses.append((e["seq"], "issue", e["issue_contains"], r and r["_issues"]))
        for k, v in e.get("fields", {}).items():
            total += 1
            got = r.get(k) if r else None
            if got == v:
                hit += 1
            else:
                misses.append((e["seq"], k, v, got))
    return hit / total, misses


def test_clean_contracts_accuracy(client, login):
    exp = json.loads((SAMPLES / "dirty_contracts_expected.json").read_text("utf-8"))
    job = _preview(client, login(YX), SAMPLES / "dirty_contracts.xlsx", purpose="这份文件用来导入合同台账")
    assert job["target"] == "contract" and job["header_row"] == 3
    acc, misses = _accuracy(job, exp)
    assert acc >= 0.95, misses
    st = job["stats"]
    assert st["total"] == 30 and st["duplicate"] == 4 and st["error"] == 3
    assert st["ok"] + st["fixed"] == 23
    assert {s["reason"][:2] for s in st["skipped_rows"]} >= {"空行", "备注"}
    errs = [r for r in job["rows"] if r["_status"] == "error"]
    assert all(r["_issues"] for r in errs)
    fixed = [r for r in job["rows"] if r["_status"] == "fixed"]
    assert all(r["_changes"] and all("from" in c and "to" in c for c in r["_changes"]) for r in fixed)
    mapped = {m["header"]: m["field"] for m in job["mapping"]}
    assert mapped["甲方"] == "customer_name" and mapped["类别"] == "business_line" and mapped["所属项目"] == "project_code"


def test_clean_remap_and_import(client, login, db):
    from app.models import BizContract
    h = login(YX)
    job = _preview(client, h, SAMPLES / "dirty_contracts.xlsx", target="contract")
    col = next(m["col"] for m in job["mapping"] if m["field"] == "remark")
    j2 = data(client.post(f"/api/v1/clean/jobs/{job['id']}/remap", json={"mapping": {str(col): None}}, headers=h))
    assert not any(m["field"] == "remark" for m in j2["mapping"])
    dup = {str(m["col"]): "customer_name" for m in j2["mapping"] if m["field"] == "amount"}
    assert client.post(f"/api/v1/clean/jobs/{job['id']}/remap", json={"mapping": dup}, headers=h).json()["code"] == 40001
    n0 = db.scalar(select(func.count()).select_from(BizContract).where(BizContract.source_type == 3))
    done = data(client.post(f"/api/v1/clean/jobs/{job['id']}/import", headers=h))
    st = done["stats"]
    assert done["status"] == "imported"
    assert st["imported"] == st["ok"] + st["fixed"] == 23 and st["skipped"] == 7
    db.expire_all()
    n1 = db.scalar(select(func.count()).select_from(BizContract).where(BizContract.source_type == 3))
    assert n1 - n0 == 23
    c = db.scalar(select(BizContract).where(BizContract.contract_no == "HT-2026-0802"))
    assert c.customer_name == "广东海洋大学" and c.amount == D("860000") and c.business_line == "AF"
    assert db.scalar(select(func.count()).select_from(BizContract).where(BizContract.contract_no == "HT-2026-0818")) == 0
    # 报告可打开
    rep = done["report_file"]
    r = client.get(rep["url"], headers=h)
    assert r.status_code == 200
    wb = load_workbook(io.BytesIO(r.content))
    assert {"概览", "清洗明细", "原值新值对照", "跳过与异常行", "口径说明"} <= set(wb.sheetnames)
    assert wb["跳过与异常行"].max_row - 1 >= 7
    assert wb["原值新值对照"].max_row > 20
    assert client.post(f"/api/v1/clean/jobs/{job['id']}/import", headers=h).json()["code"] == 40901
    hist = data(client.get("/api/v1/clean/jobs", headers=h))
    assert hist["items"][0]["status"] in ("imported", "preview")
    # 再次清洗同一文件：已导入行全部识别为库内重复
    again = _preview(client, h, SAMPLES / "dirty_contracts.xlsx", target="contract")
    assert again["stats"]["ok"] + again["stats"]["fixed"] == 0


def test_clean_opportunities(client, login, db):
    from app.models import BizOpportunity
    h = login(YX)
    job = _preview(client, h, SAMPLES / "dirty_opportunities.xlsx", purpose="导入商机表")
    assert job["target"] == "opportunity" and job["header_row"] == 2
    st = job["stats"]
    assert st["total"] == 12 and st["duplicate"] == 2 and st["error"] == 3
    rows = {r["name"]: r for r in job["rows"] if r.get("name")}
    assert rows["花都区校园安防联网"]["amount"] == "2600000.00" and rows["花都区校园安防联网"]["stage"] == "quote"
    assert rows["南沙区学生综合评价二期"]["amount"] == "1455000.00"
    assert rows["惠城区教育城域网运维"]["customer_name"] == "惠州市惠城区教育局"
    n0 = db.scalar(select(func.count()).select_from(BizOpportunity).where(BizOpportunity.source_type == 3))
    done = data(client.post(f"/api/v1/clean/jobs/{job['id']}/import", headers=h))
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(BizOpportunity).where(BizOpportunity.source_type == 3)) - n0 \
        == done["stats"]["imported"] == 7


def test_clean_payment_csv(client, login, db):
    from app.models import BizContract, BizPayment
    h = login(YX)
    c = db.scalar(select(BizContract).where(BizContract.contract_no == "HT-2026-0019"))
    csv_text = ("合同号,到账日期,到账金额,付款方\n"
                f"HT-2026-19,2026/10/19,\"¥120,000\",{c.customer_name}\n"
                "HT-2026-9999,2026-10-18,5000,某单位\n")
    r = client.post("/api/v1/clean/preview", files={"file": ("回款.csv", csv_text.encode("gb18030"))},
                    data={"purpose": "导入回款登记"}, headers=h)
    job = data(r)
    assert job["target"] == "payment" and job["stats"]["importable"] == 1 and job["stats"]["error"] == 1
    n0 = db.scalar(select(func.count()).select_from(BizPayment).where(BizPayment.contract_id == c.id))
    data(client.post(f"/api/v1/clean/jobs/{job['id']}/import", headers=h))
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(BizPayment).where(BizPayment.contract_id == c.id)) == n0 + 1


def test_clean_rbac_and_bad_input(client, login):
    with open(SAMPLES / "dirty_contracts.xlsx", "rb") as f:
        content = f.read()
    r = client.post("/api/v1/clean/preview", files={"file": ("a.xlsx", content)}, data={"target": "contract"},
                    headers=login("zhaoli"))
    assert r.status_code == 403 and r.json()["code"] == 40301
    assert client.get("/api/v1/clean/jobs", headers=login("zhaoli")).json()["code"] == 40301
    r = client.post("/api/v1/clean/preview", files={"file": ("a.xlsx", content)}, data={"purpose": "随便看看"},
                    headers=login(YX))
    assert r.json()["code"] == 40001
    r = client.post("/api/v1/clean/preview", files={"file": ("a.xls", b"\xd0\xcf\x11\xe0" + b"0" * 100)},
                    data={"target": "contract"}, headers=login(YX))
    assert r.json()["code"] == 42201
