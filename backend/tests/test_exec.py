"""总经办：经营看板（FR-03）、个性化门户（FR-02）、合同台账与归档（FR-05-01/06）、汇报提交与催交（FR-07-05）。

FAKE_TODAY=2026-10-20：当月回款 4,860,000 / 目标 7,000,000 / 达成率 0.6943（核心种子基线）。
本文件中会改变回款/签约的用例放在数值核对用例之后，并且断言写成增量。
"""
import io
import time
from datetime import date, datetime, timedelta
from decimal import Decimal

from openpyxl import load_workbook
from sqlalchemy import func, select

from tests.conftest import data

D = Decimal


def _ov(client, login, user="limingyuan", refresh=1):
    return data(client.get("/api/v1/board/overview", params={"refresh": refresh}, headers=login(user)))


# ------------------------------------------------------------------ FR-03 看板数值与台账一致
def test_board_payment_baseline_and_sql_consistency(client, login, db):
    from app.models import BizContract, BizPayment
    ov = _ov(client, login)
    pay = ov["payment"]
    assert D(pay["amount"]) == D("4860000") and D(pay["target"]) == D("7000000")
    assert pay["rate"] == 0.6943
    # 抽样 1：当月回款 = biz_payment 聚合
    s = db.scalar(select(func.sum(BizPayment.amount)).join(BizPayment.contract).where(
        BizContract.is_deleted.is_(False), BizPayment.payment_date >= date(2026, 10, 1),
        BizPayment.payment_date <= date(2026, 10, 20)))
    assert D(pay["amount"]) == s
    # 按业务线分布之和 = 合计；目标来自字典
    assert sum(D(x["amount"]) for x in pay["by_line"]) == s
    assert {x["line"]: D(x["target"]) for x in pay["by_line"]}["XY"] == D("2800000")
    # 每日累计曲线最后一个有效点 = 当月回款
    actual = [x for x in pay["daily"]["actual"] if x is not None]
    assert len(actual) == 20 and D(actual[-1]) == s and len(pay["daily"]["days"]) == 31
    # 抽样 2：当月签订额 = biz_contract 聚合
    sign = db.scalar(select(func.sum(BizContract.amount)).where(
        BizContract.is_deleted.is_(False), BizContract.status != 3, BizContract.sign_date >= date(2026, 10, 1),
        BizContract.sign_date <= date(2026, 10, 20)))
    assert D(ov["sign"]["month"]["amount"]) == sign
    assert ov["sign"]["today"]["count"] >= 1  # 核心种子：今日 1 条签约
    year = db.scalar(select(func.sum(BizContract.amount)).where(
        BizContract.is_deleted.is_(False), BizContract.status != 3, BizContract.sign_date >= date(2026, 1, 1),
        BizContract.sign_date <= date(2026, 10, 20)))
    assert D(ov["sign"]["year"]["amount"]) == year and ov["sign"]["year"]["yoy"] is not None
    # 抽样 3：商机本月新增 = biz_opportunity 聚合
    from app.core.timeutil import TZ
    from app.models import BizOpportunity
    n = db.scalar(select(func.count()).select_from(BizOpportunity).where(
        BizOpportunity.is_deleted.is_(False), BizOpportunity.created_at >= datetime(2026, 10, 1, tzinfo=TZ),
        BizOpportunity.created_at < datetime(2026, 10, 21, tzinfo=TZ)))
    assert ov["opportunity"]["month"]["count"] == n
    assert {f["stage"] for f in ov["opportunity"]["funnel"]} >= {"lead", "contact", "quote", "negotiation", "won"}
    # ⑤ 招标/在建
    from app.models import BizProject
    assert ov["tender"]["bidding_count"] == db.scalar(select(func.count()).select_from(BizProject).where(
        BizProject.status == 1, BizProject.is_deleted.is_(False)))
    assert ov["tender"]["building_count"] == db.scalar(select(func.count()).select_from(BizProject).where(
        BizProject.status == 2, BizProject.is_deleted.is_(False)))
    # ⑥ 提交率 + 毛利摘要 + 下钻目标
    assert ov["submission"]["weekly"]["required"] > 0 and ov["submission"]["weekly"]["by_dept"]
    assert ov["profit"]["gross_margin"] is not None
    assert pay["drill"] == {"path": "/contracts", "query": {"paid_month": "2026-10"}}


def test_board_drill_matches_contract_list(client, login):
    """下钻：当月回款 → 合同台账（paid_month），列表本月回款合计 = 看板当月回款。"""
    ov = _ov(client, login)
    q = ov["payment"]["drill"]["query"]
    r = data(client.get("/api/v1/contracts", params={**q, "page_size": 200}, headers=login("limingyuan")))
    assert D(r["sums"]["period_paid"]) == D(ov["payment"]["amount"])
    q2 = ov["sign"]["month"]["drill"]["query"]
    r2 = data(client.get("/api/v1/contracts", params=q2, headers=login("limingyuan")))
    assert D(r2["sums"]["amount"]) == D(ov["sign"]["month"]["amount"])


def test_profit_summary_shares_profit_service(client, login, db):
    from app.core.timeutil import week_range
    from app.services.profit_service import project_profit
    ov = _ov(client, login)
    ws, _ = week_range(ref=date(2026, 10, 20))
    r = project_profit(db, ws, date(2026, 10, 20))
    assert ov["profit"]["gross_margin"] == float(r["totals"]["gross_margin"])
    assert D(ov["profit"]["week_gross_profit"]) == r["totals"]["period_income"] - r["totals"]["period_cost"]


def test_delay_rule_marks_red(client, login, db):
    """finish_date > plan_date 或计划日已过未完成 → 延期▲。"""
    from app.models import BizProject
    from app.services.board_service import milestone_delayed
    ov = _ov(client, login)
    items = {r["project_code"]: r for r in ov["key_projects"]["items"]}
    for code, r in items.items():
        p = db.scalar(select(BizProject).where(BizProject.project_code == code))
        expect = any((m.finish_date and m.plan_date and m.finish_date > m.plan_date) or
                     (not m.finish_date and m.plan_date and m.plan_date < date(2026, 10, 20)) for m in p.milestones)
        assert r["delayed"] == expect, code
    assert ov["key_projects"]["delayed_count"] == sum(1 for r in items.values() if r["delayed"]) > 0
    # 延期项目排在前面；每个重点项目带最新进展反馈
    flags = [r["delayed"] for r in ov["key_projects"]["items"]]
    assert flags == sorted(flags, reverse=True)
    assert all(r["latest_feedback"] for r in ov["key_projects"]["items"])

    # 计划日已过未完成 → 延期（本测试新建节点验证后回滚）
    from app.models import BizProjectMilestone
    m = BizProjectMilestone(project_id=1, node_name="x", seq=99, plan_date=date(2026, 10, 1), status=2)
    assert milestone_delayed(m, date(2026, 10, 20))
    m2 = BizProjectMilestone(project_id=1, node_name="x", seq=99, plan_date=date(2026, 10, 1),
                             finish_date=date(2026, 9, 30), status=3)
    assert not milestone_delayed(m2, date(2026, 10, 20))


def test_cache_and_refresh(client, login):
    a = _ov(client, login, refresh=1)
    b = _ov(client, login, refresh=0)
    assert a["_cached_at"] == b["_cached_at"]  # 5 分钟内命中缓存
    time.sleep(0.01)
    c = _ov(client, login, refresh=1)
    assert c["_cached_at"] != a["_cached_at"]


# ------------------------------------------------------------------ FR-03-04 对比
def test_compare_api(client, login):
    r = data(client.get("/api/v1/board/compare", params={"metric": "payment_rate", "a": "2026-10", "b": "2026-09"},
                        headers=login("limingyuan")))
    assert r["a"]["value"] == 0.6943 and r["b"]["value"] == 0.9415
    assert r["diff"] == -0.2472
    assert "下降" in r["conclusion"] and "百分点" in r["conclusion"] and "时间进度" in r["conclusion"]
    r2 = data(client.get("/api/v1/board/compare", params={"metric": "payment", "a": "2026-09", "b": "2026-08"},
                         headers=login("limingyuan")))
    assert D(r2["a"]["value"]) == D("6120000")
    assert r2["change_rate"] is not None and "万元" in r2["conclusion"]
    for m in ("sign", "opportunity"):
        x = data(client.get("/api/v1/board/compare", params={"metric": m}, headers=login("wangting")))
        assert x["a"]["period"] == "2026-10" and x["b"]["period"] == "2026-09" and x["conclusion"]
    assert client.get("/api/v1/board/compare", params={"metric": "bad"}, headers=login("limingyuan")).json()["code"] == 40001


# ------------------------------------------------------------------ FR-03-05 目标配置
def test_target_config_with_audit(client, login, db):
    from app.models import LogOperation
    h = login("wangting")
    before = data(client.get("/api/v1/board/targets", params={"period": "2026-12"}, headers=h))
    r = data(client.put("/api/v1/board/targets", json={"period": "2026-12", "values": {"XY": 3200000, "KP": 950000}},
                        headers=h))
    assert {x["line"]: D(x["target"]) for x in r["lines"]}["XY"] == D("3200000")
    assert D(r["total"]) >= D("4150000") and r["total"] != before["total"]
    log = db.scalar(select(LogOperation).where(LogOperation.action == "board.target", LogOperation.target_id == "2026-12")
                    .order_by(LogOperation.id.desc()))
    assert log and log.after["value"]["XY"] == 3200000
    # 无 board:target 按钮（财务经理）→ 403
    assert client.put("/api/v1/board/targets", json={"period": "2026-12", "values": {"XY": 1}},
                      headers=login("chenjing")).json()["code"] == 40301
    assert client.put("/api/v1/board/targets", json={"period": "2026-12", "values": {"ZZ": 1}},
                      headers=h).json()["code"] == 40001


# ------------------------------------------------------------------ FR-03-03 晨报
def test_morning_report_push_outbox_and_idempotent(client, login, db):
    from app.integration.wecom.mock import read_outbox
    from app.jobs import registry
    from app.models import SysFile, SysNotify
    r = data(client.get("/api/v1/board/morning", headers=login("limingyuan")))
    assert "经营晨报" in r["text_markdown"] and "486.0万" in r["text_markdown"] and "69.4%" in r["text_markdown"]
    assert r["image"]["filename"] == "morning-2026-10-20.png" and r["summary"]["payment_rate"] == 0.6943
    f = db.get(SysFile, r["image"]["id"])
    from app.core.storage import read_bytes
    assert read_bytes(f)[:8] == b"\x89PNG\r\n\x1a\n"

    n0 = len(read_outbox(500))
    log = registry.run_job("morning_board_push", {"date": "2026-10-20"}, trigger="manual", db=db)
    assert log.status == "success", log.error_msg
    out = read_outbox(500)[n0:]
    mr = [m for m in out if m["title"].startswith("经营晨报")]
    assert mr and set(mr[0]["to"]) >= {"zhangzhenhua", "limingyuan", "wangting"} and mr[0]["type"] == "markdown"
    assert "/board" in mr[0]["content"] and "回款" in mr[0]["content"]
    assert db.scalar(select(func.count()).select_from(SysNotify).where(SysNotify.msg_type == "morning_report",
                                                                        SysNotify.channel == "inapp")) >= 3
    again = registry.run_job("morning_board_push", {"date": "2026-10-20"}, trigger="manual", db=db)
    assert again.status == "skipped"
    assert len([m for m in read_outbox(500)[n0:] if m["title"].startswith("经营晨报")]) == len(mr)


# ------------------------------------------------------------------ FR-07-05 汇报提交与催交
def test_submission_stats_match_oa_submission(client, login, db):
    from app.models import OaSubmission
    from app.services import submission_service as ss
    r = data(client.get("/api/v1/submissions", params={"type": "weekly", "period": "2026-W42"}, headers=login("wangting")))
    req = ss.required_users(db, "weekly")
    sub = {s.user_id for s in db.scalars(select(OaSubmission).where(OaSubmission.report_type == "weekly",
                                                                     OaSubmission.period == "2026-W42"))}
    expect_missing = {u.id for u in req if u.id not in sub}
    assert {m["user_id"] for m in r["missing"]} == expect_missing
    assert r["required"] == len(req) and r["submitted"] == len(req) - len(expect_missing)
    assert sum(d["required"] for d in r["by_dept"]) == r["required"]
    assert len(r["trend"]) == 8 and r["trend"][-1]["period"] == "2026-W42"
    assert "提交率" in r["summary"] and "较上期" in r["summary"]
    # 应提交名单：排除 ADMIN 与 EMP、离职人员
    codes = {u.wecom_userid for u in req}
    assert not codes & {"admin", "zengli", "panhui", "jinbo", "tianye"}
    # 日报只统计项目部、市场部
    assert {u.dept_code for u in ss.required_users(db, "daily")} <= {"XM", "SC"}
    # 月报 / 日报视图（10-20 默认统计已结束的 9 月月报；日报默认今日）
    m = data(client.get("/api/v1/submissions", params={"type": "monthly"}, headers=login("wangting")))
    assert m["period"] == "2026-09" and m["submitted"] > 0 and len(m["trend"]) == 6
    dly = data(client.get("/api/v1/submissions", params={"type": "daily", "period": "2026-10-19"}, headers=login("caofang")))
    assert dly["required"] == len(ss.required_users(db, "daily")) and dly["submitted"] > 0
    assert client.get("/api/v1/submissions", params={"type": "x"}, headers=login("wangting")).json()["code"] == 40001
    assert client.get("/api/v1/board/compare", params={"a": "bad"}, headers=login("wangting")).json()["code"] == 40001


def test_remind_one_click_and_rbac(client, login, db):
    from app.integration.wecom.mock import read_outbox
    from app.models import LogOperation
    n0 = len(read_outbox(1000))
    r = data(client.post("/api/v1/submissions/remind", json={"type": "weekly", "period": "2026-W42"},
                         headers=login("wangting")))
    assert r["count"] == len(r["reminded"]) > 0
    msgs = [m for m in read_outbox(1000)[n0:] if "周报提交提醒" in m["title"]]
    assert len(msgs) == r["count"]
    assert db.scalar(select(LogOperation).where(LogOperation.action == "submission.remind").order_by(LogOperation.id.desc()))
    # 无 submissions:remind 按钮（市场经理）→ 403
    assert client.post("/api/v1/submissions/remind", json={"type": "weekly"}, headers=login("wujun")).json()["code"] == 40301
    # 汇报菜单：项目经理无 → 403
    assert client.get("/api/v1/submissions", headers=login("xuhao")).json()["code"] == 40301


def test_journal_sync_and_reminder_job(client, login, db):
    """oa_journal_sync 幂等 upsert；journal_reminder 识别未提交人员 = oa_submission 缺失名单，并给总办发汇总。"""
    from app.integration.wecom import mock
    from app.integration.wecom.mock import read_outbox
    from app.jobs import registry
    from app.models import OaSubmission, SysUser
    from app.services import submission_service as ss
    u = db.scalar(select(SysUser).where(SysUser.wecom_userid == "hanxiao"))
    assert not db.scalar(select(OaSubmission.id).where(OaSubmission.user_id == u.id, OaSubmission.report_type == "weekly",
                                                       OaSubmission.period == "2026-W43"))
    from datetime import timedelta

    from app.core.timeutil import now
    rt = (now() - timedelta(minutes=10)).isoformat()  # 相对“现在”，不受真实时刻影响
    items = mock._load("journals.json")
    items.append({"report_id": "wk-2026-W43-hanxiao-x", "userid": "hanxiao", "template_name": "【周报】产品中心周报",
                  "report_time": rt})
    items.append({"report_id": "dy-x-unknown", "userid": "nobody", "template_name": "日报", "report_time": rt})
    mock.put_journals(items)
    log = registry.run_job("oa_journal_sync", {"hours": 72}, trigger="manual", db=db)
    assert log.status == "success", log.error_msg
    db.expire_all()
    row = db.scalar(select(OaSubmission).where(OaSubmission.user_id == u.id, OaSubmission.report_type == "weekly",
                                               OaSubmission.period == "2026-W43"))
    assert row and row.wecom_report_id == "wk-2026-W43-hanxiao-x"
    cnt = db.scalar(select(func.count()).select_from(OaSubmission))
    registry.run_job("oa_journal_sync", {"hours": 72}, trigger="manual", db=db)
    assert db.scalar(select(func.count()).select_from(OaSubmission)) == cnt  # 幂等

    expect = {m["name"] for m in ss.stats(db, "weekly", "2026-W43")["missing"]}
    n0 = len(read_outbox(2000))
    log = registry.run_job("journal_reminder", {"kind": "weekly", "period": "2026-W43", "date": "2026-10-23"},
                           trigger="manual", db=db)
    assert log.status == "success", log.error_msg
    out = read_outbox(2000)[n0:]
    reminded = {m["to"][0] for m in out if "周报提交提醒" in m["title"]}
    names = {x.wecom_userid: x.name for x in db.scalars(select(SysUser))}
    assert {names[x] for x in reminded} == expect and "韩晓" not in expect
    summary = [m for m in out if m["title"].startswith("周报提交情况")]
    assert summary and set(summary[0]["to"]) >= {"zhangzhenhua", "wangting"}
    # 非催交日自动跳过
    assert registry.run_job("journal_reminder", {"date": "2026-10-20"}, trigger="manual", db=db).message.endswith("跳过")


# ------------------------------------------------------------------ FR-05-01 合同台账
def test_contract_crud_export_and_audit(client, login, db):
    from app.models import LogOperation
    h = login("songjia")
    body = {"name": "测试人工合同", "customer_name": "测试学校甲", "amount": "1234567.89", "sign_date": "2026-10-18",
            "business_line": "XY", "project_code": "JC-XY-2026-004", "remark": "人工录入"}
    c = data(client.post("/api/v1/contracts", json=body, headers=h))
    assert c["source_type"] == 2 and c["contract_no"].startswith("HT-2026-") and D(c["amount"]) == D("1234567.89")
    u = data(client.put(f"/api/v1/contracts/{c['id']}", json={"amount": "1300000", "remark": "金额调整"}, headers=h))
    assert D(u["amount"]) == D("1300000")
    log = db.scalar(select(LogOperation).where(LogOperation.action == "contract.update",
                                               LogOperation.target_id == str(c["id"])))
    assert log.before["amount"] == "1234567.89" and log.after["amount"] == "1300000.00"
    # 筛选：业务线 + 签订月份 + 来源 + 关键字
    q = {"business_line": "XY", "sign_month": "2026-10", "source_type": 2}
    lst = data(client.get("/api/v1/contracts", params=q, headers=h))
    assert c["id"] in [x["id"] for x in lst["items"]] and all(x["business_line"] == "XY" for x in lst["items"])
    assert data(client.get("/api/v1/contracts", params={"keyword": "测试学校甲"}, headers=h))["total"] == 1
    # 导出与筛选一致
    for params in ({}, {"business_line": "AF"}, {"status": 2}, {"customer": "教育局", "sign_month": "2026-06"}):
        total = data(client.get("/api/v1/contracts", params=params, headers=h))["total"]
        resp = client.get("/api/v1/contracts/export", params=params, headers=h)
        assert resp.status_code == 200 and resp.headers["x-export-rows"] == str(total)
        wb = load_workbook(io.BytesIO(resp.content))
        assert "口径说明" in wb.sheetnames
        rows = [r for r in wb["合同台账"].iter_rows(min_row=2, values_only=True) if r[0] and str(r[0]).startswith("HT-")]
        assert len(rows) == total
    # 必填校验 / 权限
    assert client.post("/api/v1/contracts", json={"customer_name": "x"}, headers=h).json()["code"] == 40001
    assert client.post("/api/v1/contracts", json=body, headers=login("chenjing")).json()["code"] == 40301  # 无 write
    assert client.get("/api/v1/contracts/export", headers=login("wujun")).json()["code"] == 40301  # 无 export


def test_contract_detail_traces_approval(client, login, db):
    from app.models import BizContract
    c = db.scalar(select(BizContract).where(BizContract.source_type == 1, BizContract.status == 2))
    d = data(client.get(f"/api/v1/contracts/{c.id}", headers=login("songjia")))
    assert d["approval"]["sp_no"] == c.source_approval_no and d["approval"]["fields"]["客户名称"] == c.customer_name
    assert len(d["plans"]) == 4 and d["files"] and d["files"][0]["current"] and d["project"]["project_code"]
    assert d["archive_file"]["filename"].endswith(".docx")


def test_approval_pass_updates_board_sign_realtime(client, login):
    before = _ov(client, login)
    fields = {"合同名称": "看板实时测试合同", "客户名称": "广州市测试教育局", "合同金额": "880000",
              "所属项目": "JC-AF-2026-002", "业务线": "AF", "备注": ""}
    r = data(client.post("/api/v1/dev/mock/wecom-approval", json={"template": "contract", "applicant": "songjia",
                                                                   "fields": fields}, headers=login("admin")))
    assert r["dispatch_status"] == 1
    after = _ov(client, login)
    assert D(after["sign"]["today"]["amount"]) - D(before["sign"]["today"]["amount"]) == D("880000")
    assert D(after["sign"]["month"]["amount"]) - D(before["sign"]["month"]["amount"]) == D("880000")
    assert after["sign"]["today"]["count"] == before["sign"]["today"]["count"] + 1


# ------------------------------------------------------------------ FR-05-06 合同归档
def test_archive_docx_parses_stages_and_links_flow(client, login, db):
    from app.models import BizContract, BizPayment, FinBankFlow, SysNotify
    from scripts.seed_exec import contract_docx
    h = login("songjia")
    c = data(client.post("/api/v1/contracts", json={
        "name": "归档测试合同", "customer_name": "肇庆市测试中学", "amount": "2000000", "sign_date": "2026-06-30",
        "business_line": "KP"}, headers=h))
    flow = FinBankFlow(bank_account="招商银行基本户 7559****0001", trade_date=date(2026, 7, 15), direction=1,
                       amount=D("600000.00"), counterparty="肇庆市测试中学", summary="归档测试 预付款",
                       fingerprint="test-exec-archive-flow", match_status=0)
    db.add(flow)
    db.commit()
    obj = db.get(BizContract, c["id"])
    doc = contract_docx(obj, "归档测试项目")
    r = client.post(f"/api/v1/contracts/{c['id']}/archive", headers=h,
                    files={"file": ("归档测试合同.docx", doc, "application/octet-stream")})
    res = data(r)
    stages = res["parsed"]["stages"]
    assert [s["stage_name"] for s in stages] == ["预付款", "进度款", "验收款", "质保金"]
    assert [s["ratio"] for s in stages] == [0.3, 0.4, 0.25, 0.05]
    assert D(stages[0]["amount"]) == D("600000") and sum(D(s["amount"]) for s in stages) == D("2000000")
    assert "预付款" in stages[0]["condition_text"] and stages[0]["due_date"] > "2026-06-30"
    assert len(res["linked"]) == 1 and res["linked"][0]["stage_name"] == "预付款"
    db.expire_all()
    pay = db.scalar(select(BizPayment).where(BizPayment.bank_flow_id == flow.id))
    assert pay and pay.source_type == 3 and pay.contract_id == c["id"]
    assert db.get(FinBankFlow, flow.id).match_status == 2
    d = data(client.get(f"/api/v1/contracts/{c['id']}", headers=h))
    assert d["status"] == 2 and d["plans"][0]["status"] == 4 and d["files"][0]["version"] == 1
    assert db.scalar(select(SysNotify).where(SysNotify.title.contains(c["contract_no"]), SysNotify.title.contains("自动关联")))
    # 前端确认修改计划后保存（保留已回款）
    st = [{**s, "amount": s["amount"]} for s in stages]
    st[1]["due_date"] = "2026-12-31"
    saved = data(client.put(f"/api/v1/contracts/{c['id']}/plans", json={"stages": st}, headers=h))
    assert saved["plans"][1]["due_date"] == "2026-12-31" and D(saved["plans"][0]["received_amount"]) == D("600000")
    # 再次上传 → 版本 +1；pdf 归档但提示转换
    r2 = data(client.post(f"/api/v1/contracts/{c['id']}/archive", headers=h,
                          files={"file": ("归档测试合同.pdf", b"%PDF-1.4 fake", "application/pdf")}))
    assert r2["file"]["version"] == 2 and r2["parsed"]["warnings"] and "docx" in r2["parsed"]["warnings"][0]
    assert len(data(client.get(f"/api/v1/contracts/{c['id']}", headers=h))["plans"]) == 4  # 未覆盖计划
    # 无归档按钮（市场经理）→ 403
    assert client.post(f"/api/v1/contracts/{c['id']}/archive", headers=login("wujun"),
                       files={"file": ("a.docx", doc, "application/octet-stream")}).json()["code"] == 40301


def test_parse_terms_variants():
    from app.services.contract_service import parse_payment_terms
    text = ("一、付款方式：合同签订之日起七日内支付合同总价百分之二十作为预付款；设备到货签收后支付合同金额的50%；"
            "终验合格后支付25%；剩余5%为质保金，质保期满两年后支付。逾期按日万分之五支付违约金。增值税税率13%。")
    r = parse_payment_terms(text, D("1000000"), date(2026, 1, 1))
    assert [s["stage_name"] for s in r["stages"]] == ["预付款", "到货款", "验收款", "质保金"]
    assert [s["ratio"] for s in r["stages"]] == [0.2, 0.5, 0.25, 0.05]
    assert r["stages"][0]["due_date"] == date(2026, 1, 8) and not r["warnings"]
    assert parse_payment_terms("本合同无付款约定", D("1"), date(2026, 1, 1))["warnings"]


def test_manual_payment_and_weekly_refresh_job(client, login, db):
    from app.jobs import registry
    from app.models import BizContract, BizPayment, FinBankFlow
    h = login("chenjing")
    c = db.scalar(select(BizContract).where(BizContract.status == 2, BizContract.customer_name == "佛山市教育局")
                  .order_by(BizContract.id))
    n0 = db.scalar(select(func.count()).select_from(BizPayment).where(BizPayment.contract_id == c.id))
    r = data(client.post(f"/api/v1/contracts/{c.id}/payments", json={"amount": "1000", "payment_date": "2026-10-19",
                                                                     "remark": "承兑汇票"}, headers=h))
    assert r["payment_id"] and any(x["remark"] == "承兑汇票" for x in r["detail"]["payments"])
    assert client.post(f"/api/v1/contracts/{c.id}/payments", json={"amount": "-1", "payment_date": "2026-10-19"},
                       headers=h).json()["code"] == 40001
    # 每周一刷新：已归档合同按回款计划自动关联新到账流水
    pending = db.scalars(select(FinBankFlow).where(FinBankFlow.direction == 1, FinBankFlow.match_status == 0)).all()
    log = registry.run_job("contract_payment_refresh", {"date": "2026-10-19"}, trigger="manual", db=db)
    assert log.status == "success", log.error_msg
    db.expire_all()
    linked = [f for f in pending if db.get(FinBankFlow, f.id).match_status == 2]
    for f in linked:
        p = db.scalar(select(BizPayment).where(BizPayment.bank_flow_id == f.id))
        assert p.source_type == 3
    assert log.rows_affected == len(linked)
    assert db.scalar(select(func.count()).select_from(BizPayment).where(BizPayment.contract_id == c.id)) >= n0 + 1


# ------------------------------------------------------------------ FR-02 门户
SCHEME_USERS = {"exec": "limingyuan", "exec_staff": "wangting", "finance": "chenjing", "cashier": "zhaoli",
                "market": "zhengyu", "marketing": "songjia", "product": "xiekai", "hr": "caofang", "pm": "xuhao",
                "service": "yangyue", "admin": "admin", "employee": "zengli"}


def test_portal_all_schemes(client, login):
    for scheme, user in SCHEME_USERS.items():
        r = data(client.get("/api/v1/portal/cards", headers=login(user)))
        assert r["scheme"] == scheme, (user, r["scheme"])
        assert r["cards"], scheme
        for c in r["cards"]:
            assert c["type"] in ("metric", "list", "chart", "shortcut", "reminder")
            assert "error" not in c, (scheme, c["key"], c.get("error"))
    ex = {c["key"]: c for c in data(client.get("/api/v1/portal/cards", headers=login("limingyuan")))["cards"]}
    amt = D(_ov(client, login, refresh=0)["payment"]["amount"])  # 前序用例可能已自动关联到账，取看板同源数
    assert ex["exec_payment"]["data"]["value"] == f"{amt / 10000:,.1f}" and ex["exec_payment"]["data"]["to"]["path"] == "/contracts"
    assert {"exec_payment", "exec_sign_today", "exec_year", "exec_opportunity", "exec_tender", "key_projects"} <= set(ex)
    emp = {c["key"] for c in data(client.get("/api/v1/portal/cards", headers=login("zengli")))["cards"]}
    assert emp == {"my_expenses", "my_approvals", "emp_entry"}


def test_portal_finance_fast_and_my_todo(client, login, db):
    from app.services import board_service
    board_service.invalidate()
    t0 = time.perf_counter()
    r = data(client.get("/api/v1/portal/cards", headers=login("chenjing")))
    assert time.perf_counter() - t0 < 2.0
    keys = [c["key"] for c in r["cards"]]
    assert keys[:4] == ["fin_today_expense", "fin_pending_allocation", "fin_unmatched_flows", "fin_week_profit"]
    from app.models import BizExpenseReport
    pend = db.scalar(select(func.count()).select_from(BizExpenseReport).where(BizExpenseReport.status == 2))
    assert {c["key"]: c for c in r["cards"]}["fin_pending_allocation"]["data"]["value"] == pend
    # 我的报销 = 申请人为本人
    emp = {c["key"]: c for c in data(client.get("/api/v1/portal/cards", headers=login("zengli")))["cards"]}
    from app.models import SysUser
    uid = db.scalar(select(SysUser.id).where(SysUser.wecom_userid == "zengli"))
    n = db.scalar(select(func.count()).select_from(BizExpenseReport).where(BizExpenseReport.applicant_id == uid))
    assert len(emp["my_expenses"]["data"]["rows"]) == min(n, 6)


def test_portal_preferences_order_and_hidden(client, login):
    h = login("caofang")
    keys = [c["key"] for c in data(client.get("/api/v1/portal/cards", headers=h))["cards"]]
    order = list(reversed(keys))
    data(client.put("/api/v1/auth/preferences", json={"card_order": order, "hidden_cards": [keys[0]]}, headers=h))
    r = data(client.get("/api/v1/portal/cards", headers=h))["cards"]
    assert [c["key"] for c in r] == order
    hid = next(c for c in r if c["key"] == keys[0])
    assert hid["hidden"] and "data" not in hid


def test_portal_cards_configurable_by_dict(client, login, db):
    from app.services import dict_service
    old = dict_service.get(db, "portal_cards", "service")
    dict_service.put(db, "portal_cards", "service", [{"key": "cs_pending", "title": "我的待处理"}, {"key": "nonexistent"}])
    db.commit()
    try:
        r = data(client.get("/api/v1/portal/cards", headers=login("huangqi")))["cards"]
        assert [c["key"] for c in r] == ["cs_pending"] and r[0]["title"] == "我的待处理"
    finally:
        dict_service.put(db, "portal_cards", "service", old)
        db.commit()


# ------------------------------------------------------------------ RBAC
def test_rbac(client, login):
    # 市场专员：无 board 菜单 → 403；无合同台账
    assert client.get("/api/v1/board/overview", headers=login("zhengyu")).json()["code"] == 40301
    assert client.get("/api/v1/contracts", headers=login("zhengyu")).json()["code"] == 40301
    # 项目经理看不到合同台账、看板
    assert client.get("/api/v1/contracts", headers=login("xuhao")).json()["code"] == 40301
    assert client.get("/api/v1/board/overview", headers=login("xuhao")).json()["code"] == 40301
    # 财务经理：看板可看（菜单含 board）、合同台账可看不可写
    assert client.get("/api/v1/board/overview", headers=login("chenjing")).json()["code"] == 0
    assert client.get("/api/v1/contracts", headers=login("chenjing")).json()["code"] == 0
    # 市场经理可看合同
    assert client.get("/api/v1/contracts", headers=login("wujun")).json()["code"] == 0
    # 未登录
    assert client.get("/api/v1/portal/cards").json()["code"] == 40101
