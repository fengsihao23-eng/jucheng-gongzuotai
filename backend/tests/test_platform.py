"""平台核心：认证/RBAC、企微回调加解密、审批分发幂等、兜底对账、通讯录同步、通知、任务引擎、文件签名。"""
import time

from sqlalchemy import select

from tests.conftest import data


# ---------- FR-01 认证 / RBAC ----------
def test_login_and_profile(client, login):
    me = data(client.get("/api/v1/auth/me", headers=login("chenjing")))
    assert me["dept_code"] == "CW" and me["portal"] == "finance"
    assert {"expenses", "flows", "journal"} <= set(me["menus"])
    assert "admin" not in me["menus"]


def test_wrong_password_and_disabled_account(client):
    r = client.post("/api/v1/auth/login", json={"username": "chenjing", "password": "bad"})
    assert r.json()["code"] == 40101
    from app.core.config import settings
    r = client.post("/api/v1/auth/login", json={"username": "tianye", "password": settings.demo_password})
    assert r.json()["code"] == 40301  # 已离职


def test_no_token_and_bad_token(client):
    assert client.get("/api/v1/auth/me").json()["code"] == 40101
    assert client.get("/api/v1/auth/me", headers={"Authorization": "Bearer x.y.z"}).json()["code"] == 40101


def test_menu_matrix(client, login):
    """财务仅可见财务与公共菜单；普通员工看不到台账中心。"""
    fin = set(data(client.get("/api/v1/auth/me", headers=login("zhaoli")))["menus"])
    assert "contracts" not in fin and "flows" in fin
    emp = set(data(client.get("/api/v1/auth/me", headers=login("zengli")))["menus"])
    assert emp == {"portal", "kb", "mcp"}
    assert client.get("/api/v1/admin/users", headers=login("chenjing")).json()["code"] == 40301
    assert data(client.get("/api/v1/admin/users", headers=login("admin")))["total"] >= 40


def test_wecom_oauth_mock(client):
    r = data(client.post("/api/v1/auth/wecom/oauth", json={"code": "mock-limingyuan"}))
    assert r["profile"]["name"] == "李明远"
    assert client.post("/api/v1/auth/wecom/oauth", json={"code": "bad"}).json()["code"] == 40101


def test_sliding_renewal(client):
    from app.core.security import create_jwt
    from app.core.db import SessionLocal
    from app.models import SysUser
    s = SessionLocal()
    uid = s.scalar(select(SysUser.id).where(SysUser.wecom_userid == "chenjing"))
    s.close()
    token, _ = create_jwt(uid, minutes=10)
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.headers.get("X-Refresh-Token")


# ---------- FR-07 企微回调 + FR-05-04/05 审批自动登记 ----------
def _payload(client, login, template, fields, applicant="songjia", sp_no=None):
    body = {"template": template, "applicant": applicant, "fields": fields}
    if sp_no:
        body["sp_no"] = sp_no
    return data(client.post("/api/v1/dev/mock/wecom-callback-payload", json=body, headers=login("admin")))


def test_callback_url_verify(client):
    from app.integration.wecom import get_crypto
    c = get_crypto()
    echo = c.encrypt("echo-123")
    ts, nonce = str(int(time.time())), "n1"
    r = client.get("/api/v1/wecom/callback", params={"msg_signature": c.signature(ts, nonce, echo), "timestamp": ts,
                                                      "nonce": nonce, "echostr": echo})
    assert r.text == "echo-123"
    bad = client.get("/api/v1/wecom/callback", params={"msg_signature": "x", "timestamp": ts, "nonce": nonce, "echostr": echo})
    assert bad.status_code == 403


def test_three_templates_dispatch_idempotent_within_5s(client, login, db):
    from app.models import BizContract, BizExpenseReport, BizOpportunity, SyncWecomApproval
    cases = [
        ("contract", {"合同名称": "测试学校智慧教室合同", "客户名称": "测试学校", "合同金额": "880000",
                      "所属项目": "JC-XY-2026-004", "业务线": "XY"}),
        ("expense", {"报销金额": "3580", "费用类别": "差旅费", "费用日期": "2026-10-19",
                     "所属项目": "JC-XY-2026-001 南沙区中小学数字校园建设", "报销事由": "现场出差"}),
        ("opportunity", {"商机名称": "测试商机", "客户名称": "测试教育局", "预计金额": "1200000", "业务线": "KP",
                         "商机阶段": "初步接触"}),
    ]
    for template, fields in cases:
        p = _payload(client, login, template, fields)
        t0 = time.perf_counter()
        for _ in range(3):  # 同一事件重复推送 3 次
            r = client.post("/api/v1/wecom/callback", params=p["query"], content=p["body"])
            assert r.text == "success"
        assert time.perf_counter() - t0 < 5
        db.expire_all()
        rec = db.scalar(select(SyncWecomApproval).where(SyncWecomApproval.sp_no == p["sp_no"]))
        assert rec.dispatch_status == 1, rec.error_msg
        model = {"contract": BizContract, "expense": BizExpenseReport, "opportunity": BizOpportunity}[template]
        col = {"contract": BizContract.source_approval_no, "expense": BizExpenseReport.approval_no,
               "opportunity": BizOpportunity.source_approval_no}[template]
        assert len(db.scalars(select(model).where(col == p["sp_no"])).all()) == 1
    exp = db.scalar(select(BizExpenseReport).where(BizExpenseReport.amount == 3580, BizExpenseReport.expense_date == __import__("datetime").date(2026, 10, 19), BizExpenseReport.reason == "现场出差"))
    assert exp.project_code == "JC-XY-2026-001" and exp.status == 4


def test_expense_without_project_goes_to_pending_pool(client, login, db):
    from app.models import BizExpenseReport
    p = _payload(client, login, "expense", {"报销金额": "120", "费用类别": "交通费", "所属项目": "不存在的项目"})
    client.post("/api/v1/wecom/callback", params=p["query"], content=p["body"])
    db.expire_all()
    e = db.scalar(select(BizExpenseReport).where(BizExpenseReport.approval_no == p["sp_no"]))
    assert e.status == 2 and e.project_code is None


def test_bad_approval_goes_to_manual_queue_and_alerts(client, login, db):
    from app.models import SyncWecomApproval, SysNotify
    p = _payload(client, login, "contract", {"客户名称": "某校", "合同金额": "abc"})
    client.post("/api/v1/wecom/callback", params=p["query"], content=p["body"])
    db.expire_all()
    rec = db.scalar(select(SyncWecomApproval).where(SyncWecomApproval.sp_no == p["sp_no"]))
    assert rec.dispatch_status == 2 and "金额" in rec.error_msg
    assert db.scalar(select(SysNotify).where(SysNotify.ref_id == p["sp_no"], SysNotify.msg_type == "alert"))
    q = data(client.get("/api/v1/admin/approvals", params={"status": 2}, headers=login("admin")))
    assert any(i["sp_no"] == p["sp_no"] for i in q["items"])


def test_reconcile_recovers_lost_callback(client, login, db):
    from app.models import BizContract
    r = data(client.post("/api/v1/dev/mock/wecom-approval", headers=login("admin"), json={
        "template": "contract", "applicant": "songjia", "deliver": "drop",
        "fields": {"合同名称": "回调丢失合同", "客户名称": "回调丢失学校", "合同金额": "500000", "业务线": "AF"}}))
    assert not db.scalar(select(BizContract).where(BizContract.source_approval_no == r["sp_no"]))
    run = data(client.post("/api/v1/admin/jobs/approval_reconcile/run", headers=login("admin"), json={}))
    assert run["status"] == "success"
    db.expire_all()
    c = db.scalar(select(BizContract).where(BizContract.source_approval_no == r["sp_no"]))
    assert c and c.source_type == 1 and c.business_line == "AF"
    # 再跑一次不重复登记
    client.post("/api/v1/admin/jobs/approval_reconcile/run", headers=login("admin"), json={})
    assert len(db.scalars(select(BizContract).where(BizContract.source_approval_no == r["sp_no"])).all()) == 1


def test_contract_registration_pushes_wecom_message(client, login):
    from app.integration.wecom.mock import read_outbox
    data(client.post("/api/v1/dev/mock/wecom-approval", headers=login("admin"), json={
        "template": "contract", "applicant": "songjia",
        "fields": {"合同名称": "推送测试", "客户名称": "推送测试学校", "合同金额": "100000", "业务线": "KP"}}))
    assert any("推送测试学校" in m["content"] for m in read_outbox(20))


# ---------- FR-01-02 通讯录同步 ----------
def test_addressbook_sync_disables_left_member(client, login, db):
    from app.models import SysUser
    r = data(client.post("/api/v1/admin/addressbook/sync", headers=login("admin")))
    assert r["status"] == "success"
    db.expire_all()
    assert db.scalar(select(SysUser).where(SysUser.wecom_userid == "tianye")).status == 2


# ---------- FR-11 任务引擎 ----------
def test_job_list_and_logs(client, login):
    jobs = data(client.get("/api/v1/admin/jobs", headers=login("admin")))
    names = {j["name"] for j in jobs}
    assert {"wecom_addressbook_sync", "approval_reconcile"} <= names
    logs = data(client.get("/api/v1/admin/job-logs", headers=login("admin")))
    assert logs["total"] >= 1


def test_job_idempotency(db):
    from app.jobs.registry import JOBS, job, run_job
    calls = []

    @job("t_idem", "测试幂等", {"hour": 1}, "-", idempotency=lambda p: f"t_idem:{p.get('d')}")
    def _f(db, params):
        calls.append(1)
        return {"rows": 1}
    assert run_job("t_idem", {"d": "x"}).status == "success"
    assert run_job("t_idem", {"d": "x"}).status == "skipped"
    assert run_job("t_idem", {"d": "x"}, force=True).status == "success"
    assert len(calls) == 2
    JOBS.pop("t_idem")


def test_job_failure_alerts_admin(db):
    from app.jobs.registry import JOBS, job, run_job
    from app.models import SysNotify

    @job("t_fail", "测试失败", {"hour": 1}, "-")
    def _f(db, params):
        raise RuntimeError("boom")
    e = run_job("t_fail")
    assert e.status == "failed" and "boom" in e.error_msg
    assert db.scalar(select(SysNotify).where(SysNotify.msg_type == "job_failed", SysNotify.content.contains("boom")))
    JOBS.pop("t_fail")


# ---------- 文件签名 / 审计 ----------
def test_signed_file_link(client, db):
    from app.core.storage import save_bytes, signed_url
    f = save_bytes(db, data=b"hello", filename="t.txt", category="report")
    db.commit()
    assert client.get(signed_url(f)).content == b"hello"
    assert client.get(signed_url(f).replace("sig=", "sig=0")).json()["code"] == 40301
    assert client.get(f"/api/v1/files/{f.id}/download?exp=1&sig=x").json()["code"] == 40301
    f2 = save_bytes(db, data=b"v2", filename="t.txt", category="report", key=f.storage_key)
    assert f2.version == f.version + 1


def test_mask():
    from app.core.audit import mask
    m = mask({"mobile": "13812345678", "id": "440106199001011234", "password": "x", "q": "电话13812345678"})
    assert m["mobile"] == "138****5678" and m["id"].startswith("440106****") and m["password"] == "***"
    assert "1381234" not in m["q"]


def test_job_lock_released_even_when_job_commits(db):
    """回归：任务内部提交后锁仍能释放，连续执行不会被误判为“另一进程正在执行”。"""
    from app.jobs.registry import JOBS, job, run_job

    @job("t_lock", "测试锁释放", {"hour": 1}, "-")
    def _f(db, params):
        for _ in range(3):
            db.commit()
        return {"rows": 0}
    assert [run_job("t_lock").status for _ in range(5)] == ["success"] * 5
    JOBS.pop("t_lock")
