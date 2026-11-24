"""市场部（P0）+ 项目部（P1）：节点引擎留痕、延期标红、扫描任务、数据范围、MCP 复用入口、市场周报、情报、复盘、周进度导出。"""
import io
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from tests.conftest import data, deletion_body

API = "/api/v1"
T = date(2026, 10, 20)


def _new_project(client, login, user="admin", **kw):
    body = {"name": kw.pop("name", "测试项目-节点引擎"), "business_line": kw.pop("business_line", "XY"),
            "customer_name": "测试客户", "status": 2, "start_date": str(T - timedelta(days=10)), **kw}
    r = data(client.post(f"{API}/projects", json=body, headers=login(user)))
    return r["project_code"]


def _detail(client, login, code, user="admin"):
    return data(client.get(f"{API}/projects/{code}", headers=login(user)))


# ---------------------------------------------------------------- 新建 + 模板
def test_create_project_from_template(client, login, db):
    from app.models import SysUser
    xu = db.scalar(select(SysUser).where(SysUser.wecom_userid == "xuhao"))
    code = _new_project(client, login, owner_id=xu.id, milestone_template="standard")
    assert code.startswith("JC-XY-2026-")
    d = _detail(client, login, code)
    names = [m["node_name"] for m in d["milestones"]]
    assert names == ["深化设计", "进度管理", "采购", "到货验收", "终验"]
    # 按工期推算计划日：start + 25 / +20 ...
    assert d["milestones"][0]["plan_date"] == str(T - timedelta(days=10) + timedelta(days=25))
    assert d["milestones"][1]["plan_date"] == str(T - timedelta(days=10) + timedelta(days=45))
    assert all(m["owner_id"] == xu.id for m in d["milestones"])
    assert d["milestones"][0]["duty"]
    # 创建留痕
    assert sum(1 for lg in d["logs"] if lg["action"] == "create") == 5


# ---------------------------------------------------------------- 节点增删改排序 + 留痕
def test_milestone_crud_reorder_and_logs(client, login, db):
    from app.models import BizMilestoneLog, BizProject
    code = _new_project(client, login, name="节点增删改测试")
    h = login("admin")
    ms = _detail(client, login, code)["milestones"]
    # 新增（插入到第 2 位）
    new = data(client.post(f"{API}/projects/{code}/milestones",
                           json={"node_name": "方案评审", "duty": "售前牵头评审", "plan_date": str(T + timedelta(days=5)),
                                 "seq": 2}, headers=h))
    after = _detail(client, login, code)["milestones"]
    assert [m["node_name"] for m in after][:3] == ["深化设计", "方案评审", "进度管理"]
    assert [m["seq"] for m in after] == list(range(1, 7))
    # 修改：名称/职责/计划日/进展
    upd = data(client.put(f"{API}/projects/{code}/milestones/{new['id']}",
                          json={"node_name": "方案评审会", "duty": "售前+项目经理", "progress_note": "已约甲方"}, headers=h))
    assert upd["node_name"] == "方案评审会" and upd["progress_note"] == "已约甲方"
    # 排序：倒序
    ids = [m["id"] for m in after][::-1]
    data(client.put(f"{API}/projects/{code}/milestones-order", json={"ids": ids}, headers=h))
    after2 = _detail(client, login, code)["milestones"]
    assert [m["id"] for m in after2] == ids
    # 上移
    moved = data(client.post(f"{API}/projects/{code}/milestones/{ids[1]}/move", json={"direction": "up"}, headers=h))
    assert moved[0]["id"] == ids[1]
    # 删除
    data(client.request("DELETE", f"{API}/projects/{code}/milestones/{ms[0]['id']}", headers=h, json=deletion_body()))
    after3 = _detail(client, login, code)["milestones"]
    assert ms[0]["id"] not in [m["id"] for m in after3] and [m["seq"] for m in after3] == list(range(1, 6))
    # 留痕：操作人/时间/前后值/source
    pid = db.scalar(select(BizProject.id).where(BizProject.project_code == code))
    logs = db.scalars(select(BizMilestoneLog).where(BizMilestoneLog.project_id == pid)).all()
    acts = [lg.action for lg in logs]
    assert {"create", "update", "reorder", "delete"} <= set(acts)
    upd_log = next(lg for lg in logs if lg.action == "update")
    assert upd_log.before["node_name"] == "方案评审" and upd_log.after["node_name"] == "方案评审会"
    assert all(lg.operator_id and lg.created_at and lg.source == "web" for lg in logs)
    api_logs = data(client.get(f"{API}/projects/{code}/logs", headers=h))
    assert any(c["field"] == "node_name" for lg in api_logs for c in lg["changes"])
    assert api_logs[0]["operator_name"] == "平台管理员"


def test_permission_engineer_cannot_edit(client, login):
    """项目工程师（无 milestone:write、非负责人）不可改节点；项目经理(owner) 可改。"""
    code = "JC-XY-2026-001"  # 负责人 xuhao
    d = _detail(client, login, code, "xuhao")
    mid = d["milestones"][-1]["id"]
    eng = [m["owner_id"] for m in d["milestones"]]
    r = client.put(f"{API}/projects/{code}/milestones/{mid}", json={"progress_note": "x"}, headers=login("dingrui"))
    assert r.json()["code"] in (40301,)  # dingrui 若为节点责任人可见但不可改；否则不可见
    r = client.put(f"{API}/projects/{code}/milestones/{mid}", json={"progress_note": "终验资料准备中"},
                   headers=login("xuhao"))
    assert data(r)["progress_note"] == "终验资料准备中"
    assert eng  # 种子含节点责任人


# ---------------------------------------------------------------- 延期自动标红
def test_complete_late_sets_alert_and_overdue_marks_delayed(client, login):
    code = _new_project(client, login, name="延期标红测试")
    h = login("admin")
    ms = _detail(client, login, code)["milestones"]
    m1, m2 = ms[0], ms[1]
    # 计划日 T+15，完成日 T+20 > 计划日 → 已完成 + alert_flag
    r = data(client.put(f"{API}/projects/{code}/milestones/{m1['id']}", json={"finish_date": str(T + timedelta(days=20))},
                        headers=h))
    assert r["status"] == 3 and r["alert_flag"] is True and r["late_finished"]
    # 按期完成 → 不标红
    r = data(client.put(f"{API}/projects/{code}/milestones/{m1['id']}",
                        json={"status": 3, "finish_date": str(T)}, headers=h))
    assert r["status"] == 3 and r["alert_flag"] is False
    # 进行中节点计划日改为过去 → 延期 status=4 标红
    r = data(client.put(f"{API}/projects/{code}/milestones/{m2['id']}",
                        json={"status": 2, "plan_date": str(T - timedelta(days=2))}, headers=h))
    assert r["status"] == 4 and r["alert_flag"] is True and r["is_delayed"]
    # 计划日调回未来 → 恢复进行中
    r = data(client.put(f"{API}/projects/{code}/milestones/{m2['id']}", json={"plan_date": str(T + timedelta(days=3))},
                        headers=h))
    assert r["status"] == 2 and r["alert_flag"] is False
    lst = data(client.get(f"{API}/projects", params={"keyword": "延期标红测试"}, headers=h))["items"][0]
    assert lst["done_nodes"] == 1 and lst["total_nodes"] == 5


# ---------------------------------------------------------------- 扫描任务
def test_milestone_scan_delay_remind_idempotent(client, login, db):
    from app.jobs.registry import run_job
    from app.models import BizMilestoneLog, BizProject, SysNotify, SysUser
    code = _new_project(client, login, name="扫描任务测试")
    h = login("admin")
    ms = _detail(client, login, code)["milestones"]
    xu = db.scalar(select(SysUser).where(SysUser.wecom_userid == "xuhao"))
    peng = db.scalar(select(SysUser).where(SysUser.wecom_userid == "pengbo"))
    data(client.put(f"{API}/projects/{code}", json={"owner_id": peng.id}, headers=h))
    # 节点1：计划日即将过去（扫描日 T+1 时已过期），责任人 xuhao；节点2：扫描日后 2 天到期
    data(client.put(f"{API}/projects/{code}/milestones/{ms[0]['id']}",
                    json={"plan_date": str(T), "owner_id": xu.id, "status": 2}, headers=h))
    data(client.put(f"{API}/projects/{code}/milestones/{ms[1]['id']}",
                    json={"plan_date": str(T + timedelta(days=3)), "owner_id": xu.id, "status": 2}, headers=h))
    scan_day = str(T + timedelta(days=1))
    before_n = db.scalar(select(func.count(SysNotify.id)).where(SysNotify.msg_type == "milestone_delay",
                                                                SysNotify.user_id.in_([xu.id, peng.id])))
    log = run_job("project_milestone_scan", {"date": scan_day}, trigger="manual")
    assert log.status == "success", log.error_msg
    db.expire_all()
    d = _detail(client, login, code)["milestones"]
    assert d[0]["status"] == 4 and d[0]["alert_flag"] is True
    assert d[1]["status"] == 2
    # 延期提醒责任人 + 项目经理
    after_n = db.scalar(select(func.count(SysNotify.id)).where(SysNotify.msg_type == "milestone_delay",
                                                               SysNotify.user_id.in_([xu.id, peng.id])))
    assert after_n > before_n
    assert db.scalar(select(func.count(SysNotify.id)).where(SysNotify.msg_type == "milestone_delay",
                                                            SysNotify.user_id == peng.id,
                                                            SysNotify.ref_id == str(ms[0]["id"]))) >= 1
    # 到期前 3 天提醒责任人
    assert db.scalar(select(func.count(SysNotify.id)).where(SysNotify.msg_type == "milestone_due",
                                                            SysNotify.ref_id == str(ms[1]["id"]))) >= 1
    # 系统留痕
    pid = db.scalar(select(BizProject.id).where(BizProject.project_code == code))
    assert db.scalar(select(func.count(BizMilestoneLog.id)).where(BizMilestoneLog.project_id == pid,
                                                                  BizMilestoneLog.source == "system")) == 1
    # 幂等：同日再跑跳过
    again = run_job("project_milestone_scan", {"date": scan_day}, trigger="manual")
    assert again.status == "skipped"
    assert again.idempotency_key == f"milestone_scan:{scan_day}"
    # 强制重跑也不会重复标记/重复提醒延期
    n1 = db.scalar(select(func.count(SysNotify.id)).where(SysNotify.ref_id == str(ms[0]["id"]),
                                                          SysNotify.msg_type == "milestone_delay"))
    run_job("project_milestone_scan", {"date": scan_day}, trigger="manual", force=True)
    n2 = db.scalar(select(func.count(SysNotify.id)).where(SysNotify.ref_id == str(ms[0]["id"]),
                                                          SysNotify.msg_type == "milestone_delay"))
    assert n1 == n2


# ---------------------------------------------------------------- 数据范围
def test_pm_data_scope(client, login, db):
    from app.models import BizProject, BizProjectMilestone, SysUser
    xu = db.scalar(select(SysUser).where(SysUser.wecom_userid == "xuhao"))
    items = data(client.get(f"{API}/projects", headers=login("xuhao")))["items"]
    codes = {i["project_code"] for i in items}
    assert {"JC-XY-2026-001", "JC-KP-2026-001", "JC-KP-2025-001"} <= codes
    # 只看到自己负责或自己是节点责任人的项目
    for c in codes:
        p = db.scalar(select(BizProject).where(BizProject.project_code == c))
        assert p.owner_id == xu.id or db.scalar(select(func.count()).where(
            BizProjectMilestone.project_id == p.id, BizProjectMilestone.owner_id == xu.id))
    assert "JC-AF-2026-001" not in codes  # 蒋涛负责
    r = client.get(f"{API}/projects/JC-AF-2026-001", headers=login("xuhao"))
    assert r.status_code in (403, 404) and r.json()["code"] in (40301, 40401)
    r = client.put(f"{API}/projects/JC-AF-2026-001/milestones-order", json={"ids": []}, headers=login("xuhao"))
    assert r.json()["code"] in (40301, 40401)
    # 市场经理全量
    all_items = data(client.get(f"{API}/projects", headers=login("wujun")))["items"]
    assert len(all_items) > len(items)
    # 无 projects 菜单 → 403
    assert client.get(f"{API}/projects", headers=login("zhaoli")).json()["code"] == 40301
    # 项目经理只能对自己项目写反馈
    ok_r = client.post(f"{API}/market/feedback", json={"project_code": "JC-XY-2026-001", "content": "现场布线完成 80%"},
                       headers=login("xuhao"))
    assert data(ok_r)["reporter_name"] == "许浩"
    bad = client.post(f"{API}/market/feedback", json={"project_code": "JC-AF-2026-001", "content": "x"},
                      headers=login("xuhao"))
    assert bad.json()["code"] == 40301


# ---------------------------------------------------------------- MCP 复用入口
def test_update_milestone_for_mcp(db):
    from app.core.deps import build_principal
    from app.core.response import BizError
    from app.models import BizMilestoneLog, SysUser
    from app.services import project_service as ps
    xu = db.scalar(select(SysUser).where(SysUser.wecom_userid == "xuhao"))
    jt = db.scalar(select(SysUser).where(SysUser.wecom_userid == "jiangtao"))
    px, pj = build_principal(xu), build_principal(jt)
    q = ps.update_milestone(db, "JC-KP-2026-001", action="query", operator=px)
    assert q["project"]["project_code"] == "JC-KP-2026-001" and len(q["milestones"]) == 5
    target = next(m for m in q["milestones"] if m["status"] != 3)
    # 按名称模糊定位项目（「素质教育综合评价」）+ 中文状态
    res = ps.update_milestone(db, "素质教育综合评价", target["node_name"], "update", status="已完成",
                              finish_date=str(T), note="WorkBuddy 更新", operator=px, source="mcp")
    db.commit()
    assert res["milestone"]["status"] == 3 and res["milestone"]["finish_date"] == T
    assert "已更新" in res["message"]
    lg = db.scalar(select(BizMilestoneLog).where(BizMilestoneLog.milestone_id == target["id"])
                   .order_by(BizMilestoneLog.id.desc()))
    assert lg.source == "mcp" and lg.operator_id == xu.id and lg.action == "complete"
    assert lg.before["status"] != 3 and lg.after["status"] == 3
    one = ps.update_milestone(db, "JC-KP-2026-001", target["node_name"], "query", operator=px)
    assert one["milestones"][0]["status"] == 3
    # 他人项目：拒绝
    try:
        ps.update_milestone(db, "JC-KP-2026-001", target["node_name"], "update", status=2, operator=pj)
        raise AssertionError("should forbid")
    except BizError as e:
        assert e.code == 40301
    db.rollback()


# ---------------------------------------------------------------- 市场周报
def test_market_weekly_report_consistent(client, login, db):
    from docx import Document
    from app.models import BizContract, BizPayment, BizProject, RptReport
    from app.core.storage import read_bytes
    from app.models import SysFile
    h = login("wujun")
    # 本测试新增反馈 & 回款，确保报告读取到
    data(client.post(f"{API}/market/feedback", json={"project_code": "JC-XY-2026-002", "kind": "risk",
                                                      "content": "甲方要求增加录播教室点位，变更待确认",
                                                      "feedback_date": "2026-10-14"}, headers=h))
    r = data(client.post(f"{API}/market/reports/generate", json={"period_type": "weekly", "period": "2026-W42"},
                         headers=h))
    assert r["report_type"] == "market_weekly" and r["period"] == "2026-W42" and r["files"]
    s, e = date(2026, 10, 12), date(2026, 10, 18)
    key = db.scalars(select(BizProject).where(BizProject.is_key.is_(True), BizProject.is_deleted.is_(False))).all()
    assert r["data"]["totals"]["projects"] == len(key)
    exp_recv = Decimal(db.scalar(
        select(func.coalesce(func.sum(BizPayment.amount), 0)).join(BizPayment.contract)
        .where(BizContract.project_code.in_([k.project_code for k in key]), BizContract.status != 3,
               BizContract.is_deleted.is_(False), BizPayment.payment_date >= s, BizPayment.payment_date <= e)))
    assert Decimal(r["data"]["totals"]["period_received"]) == exp_recv
    exp_total = Decimal(db.scalar(select(func.coalesce(func.sum(BizContract.amount), 0)).where(
        BizContract.project_code.in_([k.project_code for k in key]), BizContract.status != 3,
        BizContract.is_deleted.is_(False))))
    assert Decimal(r["data"]["totals"]["contract_total"]) == exp_total
    # docx 内容含进度、反馈、合同/回款
    f = db.get(SysFile, r["files"][0]["id"])
    doc = Document(io.BytesIO(read_bytes(f)))
    text = "\n".join(p.text for p in doc.paragraphs) + "\n".join(c.text for t in doc.tables for row in t.rows for c in row.cells)
    assert "东莞松山湖实验学校智慧校园" in text and "录播教室点位" in text
    assert "本期进度" in text and "合同签订与回款动态" in text and "风险与下期计划" in text
    p2 = next(x for x in r["data"]["projects"] if x["project_code"] == "JC-XY-2026-002")
    assert f"{Decimal(p2['received']) / 10000:,.1f} 万" in text
    assert p2["alert"] is True
    # 列表 / 详情 / 下载 / 汇总稿
    lst = data(client.get(f"{API}/market/reports", headers=h))
    assert any(x["id"] == r["id"] for x in lst["items"])
    det = data(client.get(f"{API}/market/reports/{r['id']}", headers=h))
    assert "重点项目周报" in det["text"]
    dl = client.get(f"{API}/market/reports/{r['id']}/download", headers=h)
    assert dl.status_code == 200 and dl.content[:2] == b"PK"
    summ = data(client.get(f"{API}/market/reports/summary", headers=h))
    assert "重点项目周报" in summ["text"] and "2026-W43" in summ["text"]
    # 版本递增
    r2 = data(client.post(f"{API}/market/reports/generate", json={"period_type": "weekly", "period": "2026-W42"}, headers=h))
    assert r2["version"] == r["version"] + 1
    assert db.scalar(select(func.count(RptReport.id)).where(RptReport.report_type == "market_weekly")) >= 2
    # 月报
    m = data(client.post(f"{API}/market/reports/generate", json={"period_type": "monthly", "period": "2026-09"}, headers=h))
    assert m["report_type"] == "market_monthly" and m["metrics"]["project_count"] == len(key)


def test_market_jobs_push(db):
    from app.jobs.registry import run_job
    from app.models import RptReport, SysNotify
    lg = run_job("market_weekly_report", {"period": "2026-W41"}, trigger="manual")
    assert lg.status == "success", lg.error_msg
    assert db.scalar(select(func.count(RptReport.id)).where(RptReport.report_type == "market_weekly",
                                                            RptReport.period == "2026-W41")) == 1
    assert db.scalar(select(func.count(SysNotify.id)).where(SysNotify.msg_type == "report",
                                                            SysNotify.title.contains("2026-W41"))) >= 3
    assert run_job("market_weekly_report", {"period": "2026-W41"}, trigger="manual").status == "skipped"
    lg = run_job("project_weekly_progress", {"week": "2026-W42"}, trigger="manual")
    assert lg.status == "success", lg.error_msg
    assert db.scalar(select(func.count(SysNotify.id)).where(SysNotify.msg_type == "project_weekly")) >= 5
    assert run_job("market_monthly_report", {"period": "2026-09"}, trigger="manual").status == "success"


def test_board(client, login):
    b = data(client.get(f"{API}/market/board", headers=login("wujun")))
    assert b["summary"]["count"] == len(b["projects"]) > 0
    c = b["projects"][0]
    for k in ("current_node", "progress", "delayed_nodes", "contract_total", "received", "payment_rate",
              "market_owner_name", "latest_feedback"):
        assert k in c
    assert any(p["alert"] for p in b["projects"])  # 种子存在延期节点
    assert client.get(f"{API}/market/board", headers=login("xuhao")).json()["code"] == 40301  # 无 market 菜单


# ---------------------------------------------------------------- 情报
def test_intel_crud_search_and_brief(client, login):
    h = login("zhengyu")
    lst = data(client.get(f"{API}/market/intel", headers=h))
    assert lst["total"] >= 20 and "希沃" in lst["facets"]["competitors"]
    new = data(client.post(f"{API}/market/intel", json={
        "title": "测试竞品X发布校园安防新品", "kind": "competitor", "competitor": "竞品X", "summary": "主打 AI 周界防范",
        "tags": ["安防", "测试标签"], "importance": 3, "published_on": "2026-10-19"}, headers=h))
    assert new["author_name"] == "郑宇"
    assert data(client.get(f"{API}/market/intel", params={"keyword": "AI 周界"}, headers=h))["total"] == 1
    assert data(client.get(f"{API}/market/intel", params={"tag": "测试标签"}, headers=h))["total"] == 1
    assert data(client.get(f"{API}/market/intel", params={"competitor": "希沃"}, headers=h))["total"] >= 2
    assert all(i["importance"] >= 3 for i in data(client.get(f"{API}/market/intel", params={"importance": 3}, headers=h))["items"])
    upd = data(client.put(f"{API}/market/intel/{new['id']}", json={**new, "summary": "主打 AI 周界防范与一键报警"}, headers=h))
    assert "一键报警" in upd["summary"]
    brief = data(client.post(f"{API}/market/intel/brief", json={"start": "2026-09-20", "end": "2026-10-20", "to_kb": True},
                             headers=h))
    groups = [s["group"] for s in brief["sections"]]
    assert "竞品X" in groups and "希沃" in groups and brief["file"]["url"]
    sx = next(s for s in brief["sections"] if s["group"] == "竞品X")
    assert "综合智慧安防" in sx["impact"] and sx["suggestion"]
    assert brief["kb"] is not None  # 沉淀结果（知识库接口不存在时为跳过说明）
    data(client.request("DELETE", f"{API}/market/intel/{new['id']}", headers=h, json=deletion_body()))
    assert client.get(f"{API}/market/intel/{new['id']}", headers=h).json()["code"] == 40401
    # 无 intel:write
    assert client.post(f"{API}/market/intel", json={"title": "x", "summary": "y"}, headers=login("limingyuan")).json()["code"] == 40301


# ---------------------------------------------------------------- 复盘
def test_review_auto_generate_on_close(client, login, db):
    from app.models import BizProjectReview
    h = login("admin")
    seeded = data(client.get(f"{API}/market/reviews", headers=h))
    assert sum(1 for r in seeded["items"] if r["status"] == 2) >= 2 and any(r["status"] == 1 for r in seeded["items"])
    code = _new_project(client, login, name="复盘自动生成测试")
    d = _detail(client, login, code)
    # 完成节点（含 1 个延期完成）
    for i, m in enumerate(d["milestones"]):
        fin = date.fromisoformat(m["plan_date"]) + timedelta(days=6 if i == 2 else 0)
        data(client.put(f"{API}/projects/{code}/milestones/{m['id']}",
                        json={"finish_date": str(fin), "progress_note": "设备到货延迟" if i == 2 else "按期完成"}, headers=h))
    pid = d["id"]
    assert db.scalar(select(func.count(BizProjectReview.id)).where(BizProjectReview.project_id == pid)) == 0
    data(client.put(f"{API}/projects/{code}", json={"status": 5}, headers=h))
    rv = db.scalar(select(BizProjectReview).where(BizProjectReview.project_id == pid))
    assert rv and rv.status == 1
    assert "合同额" in rv.goal and "计划工期" in rv.goal
    assert "实际回款" in rv.result and "毛利" in rv.result and "延期节点 1 个" in rv.result
    assert "采购" in rv.lessons and "设备到货延迟" in rv.lessons
    assert "供应链" in rv.tags
    # 再次改状态不重复生成
    data(client.put(f"{API}/projects/{code}", json={"status": 3}, headers=h))
    data(client.put(f"{API}/projects/{code}", json={"status": 5}, headers=h))
    assert db.scalar(select(func.count(BizProjectReview.id)).where(BizProjectReview.project_id == pid)) == 1
    # 定稿 + 标签检索 + 导出
    data(client.put(f"{API}/market/reviews/{rv.id}", json={"status": 2, "tags": rv.tags + ["样板经验"]}, headers=h))
    found = data(client.get(f"{API}/market/reviews", params={"tag": "样板经验"}, headers=h))
    assert [x["id"] for x in found["items"]] == [rv.id] and found["items"][0]["status_text"] == "已定稿"
    ex = client.get(f"{API}/market/reviews/{rv.id}/export", headers=h)
    assert ex.status_code == 200 and ex.content[:2] == b"PK"
    # 无 review:write
    assert client.put(f"{API}/market/reviews/{rv.id}", json={"title": "x"}, headers=login("limingyuan")).json()["code"] == 40301


# ---------------------------------------------------------------- 周进度导出
def test_weekly_progress_xlsx(client, login):
    from openpyxl import load_workbook
    r = client.get(f"{API}/projects/weekly/export", headers=login("xuhao"))
    assert r.status_code == 200
    wb = load_workbook(io.BytesIO(r.content))
    assert {"项目周进度", "节点明细", "口径说明"} <= set(wb.sheetnames)
    ws = wb["项目周进度"]
    codes = [row[0] for row in ws.iter_rows(min_row=4, values_only=True) if row[0]]
    assert "JC-XY-2026-001" in codes and "JC-AF-2026-001" not in codes  # 只含我负责的项目
    r2 = client.get(f"{API}/projects/weekly/export", params={"codes": "JC-AF-2026-001,JC-XY-2026-002"},
                    headers=login("wujun"))
    codes2 = [row[0] for row in load_workbook(io.BytesIO(r2.content))["项目周进度"].iter_rows(min_row=4, values_only=True)
              if row[0]]
    assert codes2 == ["JC-AF-2026-001", "JC-XY-2026-002"]
    g = data(client.post(f"{API}/projects/weekly/generate", json={}, headers=login("xuhao")))
    assert g["file"]["url"] and "项目周进度" in g["text"]


def test_key_flag_permission(client, login):
    code = "JC-XY-2026-004"
    assert client.put(f"{API}/projects/{code}/key", json={"is_key": True}, headers=login("zhengyu")).json()["code"] == 40301
    assert data(client.put(f"{API}/projects/{code}/key", json={"is_key": True}, headers=login("wujun")))["is_key"] is True
    data(client.put(f"{API}/projects/{code}/key", json={"is_key": False}, headers=login("wujun")))
