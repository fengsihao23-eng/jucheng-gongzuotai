"""Opt-in source checks. These jobs never post amounts into financial ledgers."""
from datetime import date

from sqlalchemy import select

from app.api.source_checks import FINANCE, RESULT, ROBOT, _read_result, can_manage, signature, spreadsheet_view
from app.core.deps import build_principal
from app.core.timeutil import today
from app.jobs.registry import job
from app.models import SysNotify, SysUser
from app.services import dict_service as cfg, notify_service, wecom_import_service as reader


@job("workbench_finance_read", "已核对财务源表试读", {"hour": 17, "minute": 20}, "每日 17:20")
def read_verified_sources(db, params):
    count = 0
    for key, source in cfg.items(db, FINANCE).items():
        if not source.get("scheduled") or not source.get("verified_on"):
            continue
        user = db.get(SysUser, source["owner_id"])
        if not user or user.status != 1 or user.is_deleted or not can_manage(build_principal(user)):
            cfg.put(db, RESULT, key, {"checked_on": today().isoformat(), "state": "unauthorized"})
            continue
        try:
            record = reader.start(build_principal(user), url=source["url"], table_id=source.get("table_id", ""))
            record.update(workbench_source=key, workbench_signature=signature(source))
            reader._save(record)
            reader.prepare(user.id, record["id"])
            result = _read_result(spreadsheet_view(reader._load(user.id, record["id"])))
            if result["state"] == "ready":
                count += 1
        except Exception:  # API details are available in the owner's manual preview only.
            result = {"checked_on": today().isoformat(), "state": "failed"}
        cfg.put(db, RESULT, key, result)
        db.commit()
    return {"rows": count, "message": f"已完成 {count} 份只读预览；未写业务台账"}


@job("workbench_robot_renewal", "企业微信机器人授权续期提醒", {"hour": 9, "minute": 10}, "每日 09:10")
def remind_renewal(db, params):
    config = cfg.get(db, ROBOT, "renewal")
    if not config or (date.fromisoformat(config["expires_on"]) - today()).days > config["advance_days"]:
        return {"rows": 0, "message": "暂无已配置且临近到期的授权"}
    reference = config["expires_on"] + ":" + today().isoformat()
    ids = {config["owner_id"], config.get("backup_id")} - {None}
    notified = set(db.scalars(select(SysNotify.user_id).where(SysNotify.ref_type == ROBOT, SysNotify.ref_id == reference)))
    users = db.scalars(select(SysUser).where(SysUser.id.in_(ids - notified))).all()
    rows = notify_service.send(db, users, "robot_renewal", "企业微信功能授权待核对",
        f"{config['feature']} 记录的到期日为 {config['expires_on']}，请核实续期后更新记录。",
        link="/admin?tab=robot", ref_type=ROBOT, ref_id=reference, wecom=False)
    db.commit()
    return {"rows": len(rows), "message": f"已生成 {len(rows)} 条站内提醒"}
