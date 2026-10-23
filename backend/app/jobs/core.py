"""平台级定时任务：通讯录同步、审批兜底对账、通知重试。"""
from app.jobs.registry import job
from app.services import dispatch_service, notify_service, user_service


@job("wecom_addressbook_sync", "企微通讯录同步", {"hour": 6, "minute": 0}, "每日 06:00")
def addressbook_sync(db, params):
    return user_service.sync_addressbook(db)


@job("approval_reconcile", "审批兜底拉取对账", {"minute": "*/10"}, "每 10 分钟")
def approval_reconcile(db, params):
    r = dispatch_service.reconcile(db, hours=int(params.get("hours", 24)))
    db.commit()
    return r


@job("wecom_personal_sync", "企微个人数据同步", {"minute": "*/10"}, "每 10 分钟")
def personal_sync(db, params):
    from app.services import wecom_sync_service
    return wecom_sync_service.sync_all(db)


@job("notify_retry", "企微消息失败重试", {"minute": "*/5"}, "每 5 分钟")
def notify_retry(db, params):
    n = notify_service.retry_failed(db)
    db.commit()
    return {"rows": n, "message": f"重试 {n} 条"}
