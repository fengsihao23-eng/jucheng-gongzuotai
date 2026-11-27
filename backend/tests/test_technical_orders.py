from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.api.dev import build_callback
from app.core.config import settings
from app.core.security import hash_password
from app.core.timeutil import TZ
from app.integration.wecom.mock import put_approval
from app.models import SyncWecomApproval, SysDepartment, SysUser, SysUserPermission
from app.services import dispatch_service, technical_orders
from app.services.technical_order_source import DETAIL_KEY
from conftest import data


def _time(day, hour, minute=0, month=10):
    return datetime(2026, month, day, hour, minute, tzinfo=TZ)


@pytest.mark.parametrize(("submitted", "deadline"), [
    (_time(9, 8, 10), _time(9, 11, 30)),
    (_time(9, 15), _time(9, 18)),
    (_time(9, 17), _time(10, 10, 30)),
    (_time(9, 18), _time(10, 11, 30)),
    (_time(31, 21), _time(1, 11, 30, month=11)),
])
def test_response_deadline(submitted, deadline):
    assert technical_orders.response_deadline(submitted) == deadline


def _approval(number, owner, submitted, accepted=None, completed=None, *, status=None, transferred=None):
    def event(action, when, account=owner):
        return {"sp_yj": action, "sptime": int(when.timestamp()) if when else 0, "userid": account}

    handlers = [{"node_type": 3, "sp_status": 14 if accepted else 1, "apv_rel": 1,
                 "sub_node_list": [event(14 if accepted else 1, accepted, "delegate" if transferred else owner)]}]
    if transferred:
        handlers.insert(0, {"node_type": 3, "sp_status": 15, "apv_rel": 1, "sub_node_list": [event(15, transferred)]})
    return {
        "sp_no": f"TECH-TEST-{number}",
        "template_id": "project-order-test",
        "sp_name": "项目工单",
        "sp_status": status or (2 if completed else 1),
        "apply_time": submitted.isoformat(),
        "applicant_userid": "admin",
        "fields": {"工单负责人": owner, "工单主题": "设备故障", "所属项目": "测试项目"},
        "approved_time": (completed + timedelta(minutes=40)).isoformat() if completed else None,
        "process_list": {"node_list": [
            *handlers,
            {"node_type": 2, "sub_node_list": [event(2, submitted, "admin")]},
            {"node_type": 1, "sp_status": 2 if completed else 1, "apv_rel": 2,
             "sub_node_list": [event(2 if completed else 1, completed)]},
            {"node_type": 1, "sp_status": 2 if completed else 1, "apv_rel": 1,
             "sub_node_list": [event(2, completed + timedelta(minutes=40), "admin")] if completed else []},
        ]},
    }


def _deliver(client, source):
    put_approval(source)
    callback = build_callback(source)
    result = client.post("/api/v1/wecom/callback", params=callback["query"], content=callback["body"])
    assert result.status_code == 200
    assert result.text == "success"


def test_work_order_callback_monthly_report_and_permissions(client, login, db, monkeypatch):
    monkeypatch.setattr(technical_orders, "now", lambda: _time(12, 15, month=11))
    dept = SysDepartment(dept_code="TQATECH", name="技术服务部")
    db.add(dept)
    db.flush()
    first = SysUser(wecom_userid="tech_test_owner", name="售后甲", department_id=dept.id, position="售后工程师",
                    password_hash=hash_password(settings.demo_password))
    second = SysUser(wecom_userid="tech_test_empty", name="售后乙", department_id=dept.id, position="售后工程师",
                     password_hash=first.password_hash)
    db.add_all([first, second])
    db.flush()
    db.add(SysUserPermission(user_id=second.id, menus=["technical_orders"], buttons=[], data_scope="all", sections={}))
    db.commit()

    sources = [
        _approval("cross-month", first.wecom_userid, _time(31, 17), _time(1, 10, 30, 11), _time(2, 10, 30, 11)),
        _approval("late", first.wecom_userid, _time(11, 8, 30), _time(11, 12), _time(12, 13)),
        _approval("overdue", first.wecom_userid, _time(12, 9), _time(12, 10)),
        _approval("waiting", first.wecom_userid, _time(13, 9)),
        _approval("transferred", first.wecom_userid, _time(30, 13), _time(30, 17), _time(31, 17),
                  transferred=_time(30, 14)),
        _approval("outside-team", "admin", _time(14, 11), _time(14, 12), _time(14, 15)),
        _approval("revoked", first.wecom_userid, _time(14, 10), _time(14, 11), _time(14, 12), status=6),
    ]
    for source in sources:
        _deliver(client, source)
    _deliver(client, sources[0])

    endpoint = "/api/v1/technical-orders"
    october = data(client.get(endpoint, params={"month": "2026-10"}, headers=login("admin")))
    assert october["scope"] == "after_sales" and october["basis"] == "submitted"
    assert october["total"] == october["totals"]["order_count"] == 5
    assert october["totals"]["submitted_completed_count"] == 3 and october["totals"]["completed_count"] == 2
    source = next(row for row in october["items"] if row["sp_no"] == "TECH-TEST-cross-month")
    assert source["accepted_at"] == "2026-11-01T10:30:00+08:00"
    assert source["completed_at"] == "2026-11-02T10:30:00+08:00"
    assert source["response"] == source["completion"] == "on_time"
    transfer = next(row for row in october["items"] if row["sp_no"] == "TECH-TEST-transferred")
    assert transfer["transferred_at"] == "2026-10-30T14:00:00+08:00"
    assert transfer["accepted_at"] == "2026-10-30T17:00:00+08:00" and transfer["accepted_by"] == "delegate"
    assert transfer["response"] == transfer["completion"] == "on_time"
    assert next(row for row in october["items"] if row["sp_no"] == "TECH-TEST-overdue")["completion"] == "overdue"
    after_sales = october["after_sales"]
    assert (after_sales["headcount"], after_sales["completed"], after_sales["average"]) == (2, 2, 1)
    person = next(item for item in after_sales["people"] if item["owner_id"] == first.wecom_userid)
    assert (person["response_rate"], person["completion_rate"], person["average_rate"]) == (3 / 5, 2 / 4, 2)
    params = {"month": "2026-11", "stage": "completed"}
    submitted_november = data(client.get(endpoint, params=params, headers=login("admin")))
    assert submitted_november["total"] == 0 and submitted_november["totals"]["submitted_completed_count"] == 0
    november = data(client.get(endpoint, params={**params, "basis": "completed"}, headers=login("admin")))
    assert november["totals"]["order_count"] == 0
    assert november["totals"]["completed_count"] == november["after_sales"]["completed"] == november["total"] == 1
    assert november["totals"]["carryover_completed_count"] == november["after_sales"]["carryover_completed"] == 1
    assert november["items"][0]["sp_no"] == source["sp_no"]
    assert data(client.get(endpoint, params={"month": "2026-11", "basis": "either", "sp_no": source["sp_no"]},
                           headers=login("admin")))["total"] == 1
    outside = {"month": "2026-10", "sp_no": "TECH-TEST-outside-team"}
    assert data(client.get(endpoint, params=outside, headers=login("admin")))["total"] == 0
    assert data(client.get(endpoint, params={**outside, "scope": "all"}, headers=login("admin")))["total"] == 1
    filtered = data(client.get(endpoint, params={"month": "2026-10", "scope": "all", "keyword": "测试项目",
                                               "page_size": 2, "page": 2},
                               headers=login("admin")))
    assert filtered["total"] == 6 and len(filtered["items"]) == 2
    own = data(client.get(endpoint, params={"month": "2026-10", "scope": "all"}, headers=login(first.wecom_userid)))
    assert own["scope"] == "mine" and own["total"] == 5 and own["after_sales"] is None
    blocked = data(client.get(endpoint, params={"month": "2026-10", "owner": first.wecom_userid},
                              headers=login(second.wecom_userid)))
    assert blocked["total"] == 0 and blocked["after_sales"] is None
    assert client.get(endpoint, headers=login("songjia")).status_code == 403


def test_reconcile_refreshes_existing_work_orders_and_preserves_missing_node(db, monkeypatch):
    submitted = _time(5, 8, 30)
    initial = _approval("reconcile", "some-owner", submitted)
    original = SyncWecomApproval(
        sp_no=initial["sp_no"], sp_name="项目工单", template_id=initial["template_id"], template_key="unknown",
        status=1, apply_time=submitted, dispatch_status=3, detail={"fields": initial["fields"]},
    )
    db.add(original)
    db.commit()
    changed = _approval("reconcile", "some-owner", submitted, submitted + timedelta(hours=1))
    put_approval(changed)
    monkeypatch.setattr(dispatch_service, "now", lambda: _time(20, 15))
    dispatch_service.reconcile(db)
    db.refresh(original)
    assert original.dispatch_status == 3
    assert original.detail[DETAIL_KEY]["accepted_at"] == int(_time(5, 9, 30).timestamp())

    closed = _approval("reconcile", "some-owner", submitted, _time(5, 9, 30), _time(5, 14))
    closed.pop("process_list")
    put_approval(closed)
    dispatch_service.reconcile(db)
    db.refresh(original)
    assert original.status == 2 and original.detail[DETAIL_KEY]["completed_at"] is None
    assert original.detail["approved_time"] is not None
    assert db.scalar(select(SyncWecomApproval).where(SyncWecomApproval.sp_no == initial["sp_no"])).id == original.id
