"""企业微信打卡接入：拉取原始打卡记录，走与 Excel 相同的判定/汇总链路。

打卡接口返回的是原始打卡时间点，不包含请假审批；请假仍以企微审批同步或人工补录为准。
拉取按人按日聚合为上班/下班两次（当天最早与最晚），再交给 attendance_service.apply_eval 判定。
"""
from collections import defaultdict
from datetime import datetime, time

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.response import BizError
from app.core.timeutil import TZ, month_range
from app.integration.wecom.credentials import Gateway, WeComApiError
from app.models import HrAttendanceImport, HrAttendanceRecord, SysUser
from app.services import attendance_service

CHUNK = 100
MAX_PUNCH = 3  # opencheckindatatype=3：原始打卡记录


def _epoch(day, moment: time = time.min) -> int:
    return int(datetime.combine(day, moment, tzinfo=TZ).timestamp())


def _fetch(userids: list[str], start, end) -> dict:
    """按 100 人分批拉取，返回 {(userid, date): [datetime, ...]}。"""
    punches: dict[tuple[str, object], list[datetime]] = defaultdict(list)
    for index in range(0, len(userids), CHUNK):
        batch = userids[index:index + CHUNK]
        try:
            data = Gateway.call("checkin", "POST", "/cgi-bin/checkin/getcheckindata", json={
                "opencheckindatatype": MAX_PUNCH,
                "starttime": _epoch(start),
                "endtime": _epoch(end) + 86399,
                "useridlist": batch,
            })
        except WeComApiError as error:
            if error.errcode in (60011, 48002, 301055):
                raise BizError(50201, f"企业微信打卡接口未授权（{error.errcode}）：{error.errmsg}。"
                                      "请在企微后台开通打卡应用并配置「打卡」Secret", {"errcode": error.errcode}) from None
            raise BizError(50201, f"拉取打卡记录失败（{error.errcode}）：{error.errmsg}") from None
        for row in data.get("checkindata") or []:
            stamp = row.get("checkin_time")
            if not stamp:
                continue
            moment = datetime.fromtimestamp(int(stamp), TZ)
            punches[(row.get("userid") or "", moment.date())].append(moment)
    return punches


def pull(db: Session, period: str, uploader_id: int | None = None) -> dict:
    start, end = month_range(period)
    users = db.scalars(select(SysUser).where(SysUser.status == 1, SysUser.is_deleted.is_(False),
                                             SysUser.wecom_userid.is_not(None), SysUser.wecom_userid != "")).all()
    if not users:
        raise BizError(40401, "没有绑定企业微信 UserID 的在职账号，无法拉取打卡")
    by_userid = {u.wecom_userid: u for u in users}
    punches = _fetch(list(by_userid), start, end)
    if not punches:
        raise BizError(42201, f"{period} 没有拉取到打卡记录，请核对打卡应用可见范围与成员是否使用企微打卡")

    # 按人按日聚合：最早一次为上班，最晚一次为下班；只有一次时按时间判断
    daily: dict[tuple[int, object], tuple[datetime | None, datetime | None]] = {}
    for (userid, day), moments in punches.items():
        user = by_userid.get(userid)
        if user is None or not (start <= day <= end):
            continue
        moments.sort()
        check_in = check_out = None
        if len(moments) == 1:
            if moments[0].hour < 12:
                check_in = moments[0]
            else:
                check_out = moments[0]
        else:
            check_in, check_out = moments[0], moments[-1]
        daily[(user.id, day)] = (check_in, check_out)

    rule = attendance_service.get_rule(db)
    db.execute(delete(HrAttendanceRecord).where(HrAttendanceRecord.work_date.between(start, end)))
    imp = HrAttendanceImport(period=period, file_id=None, filename=f"企业微信打卡 {period}",
                             total_rows=len(daily), uploader_id=uploader_id, errors=[])
    db.add(imp)
    db.flush()
    exceptions = 0
    for (user_id, day), (check_in, check_out) in daily.items():
        rec = HrAttendanceRecord(user_id=user_id, work_date=day,
                                 check_in=check_in.replace(tzinfo=None) if check_in else None,
                                 check_out=check_out.replace(tzinfo=None) if check_out else None,
                                 import_id=imp.id, source_file=imp.filename, source_row=None)
        result = attendance_service.apply_eval(rule, rec)
        if set(result["types"]) - {"请假", "加班"}:
            exceptions += 1
        db.add(rec)
    imp.exception_rows = exceptions
    imp.errors = [{"kind": "note", "message": "打卡记录不含请假审批，请假请在审批通过后重新判定"}]
    db.flush()
    attendance_service.summarize(db, period)
    file = attendance_service.export_summary(db, period, uploader_id)
    db.flush()
    return {"period": period, "records": len(daily), "exception_rows": exceptions,
            "employees": len({user_id for user_id, _ in daily}),
            "skipped": len(users) - len({by_userid.get(uid).id for uid, _ in punches if by_userid.get(uid)}),
            "import_id": imp.id, "file": {"id": file.id, "name": file.filename} if file else None}
