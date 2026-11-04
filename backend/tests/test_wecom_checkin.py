"""企业微信打卡拉取：聚合上下班、判定与汇总。"""
from datetime import datetime

from app.core.timeutil import TZ
from app.integration.wecom.credentials import Gateway


def _stamp(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=TZ).timestamp())


def test_pull_wecom_checkin_builds_records(client, login, monkeypatch, db):
    from app.models import HrAttendanceRecord, SysUser
    user = db.query(SysUser).filter(SysUser.wecom_userid.isnot(None), SysUser.wecom_userid != "").first()
    assert user, "种子数据里应有绑定企微 UserID 的账号"

    def fake(cls, credential, method, path, **kwargs):
        assert credential == "checkin"
        return {"errcode": 0, "checkindata": [
            {"userid": user.wecom_userid, "checkin_time": _stamp("2026-08-10T09:05:00")},
            {"userid": user.wecom_userid, "checkin_time": _stamp("2026-08-10T18:40:00")},
            {"userid": user.wecom_userid, "checkin_time": _stamp("2026-08-11T09:40:00")},
        ]}

    monkeypatch.setattr(Gateway, "call", classmethod(fake))
    response = client.post("/api/v1/hr/attendance/pull-wecom", headers=login("admin"), json={"period": "2026-08"})
    body = response.json()["data"]
    assert body["pull"]["records"] == 2
    assert body["pull"]["employees"] == 1

    rows = db.query(HrAttendanceRecord).filter(HrAttendanceRecord.user_id == user.id,
                                               HrAttendanceRecord.work_date == datetime(2026, 8, 10).date()).all()
    assert len(rows) == 1
    assert rows[0].check_in.strftime("%H:%M") == "09:05"
    assert rows[0].check_out.strftime("%H:%M") == "18:40"
    # 8/11 只有上班一次打卡，判定为缺卡
    late = db.query(HrAttendanceRecord).filter(HrAttendanceRecord.user_id == user.id,
                                               HrAttendanceRecord.work_date == datetime(2026, 8, 11).date()).one()
    assert "缺卡" in (late.exception_type or "")


def test_pull_without_permission_is_forbidden(client, login):
    response = client.post("/api/v1/hr/attendance/pull-wecom", headers=login("chenjing"), json={"period": "2026-08"})
    assert response.status_code == 403
