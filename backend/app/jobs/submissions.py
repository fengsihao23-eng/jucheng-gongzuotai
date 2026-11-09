"""企业微信汇报提交同步（近实时，每 10 分钟拉取近 3 天记录）。

企微汇报接口未授权时不让任务失败告警刷屏，只把原因写进任务日志的 message（以「未同步：」开头），
由汇报页的同步状态条提示管理员去开权限。
"""
from datetime import timedelta

from app.core.timeutil import now
from app.integration.wecom.live import WeComApiError
from app.jobs.registry import job
from app.services import submission_service as ss


@job("wecom_submission_sync", "同步企业微信汇报提交", {"minute": "*/10"}, "每 10 分钟")
def wecom_submission_sync(db, params):
    end = now()
    days = max(1, min(int(params.get("days") or 3), 62))
    try:
        r = ss.sync_journals(db, end - timedelta(days=days), end)
    except WeComApiError as exc:
        return {"rows": 0, "message": f"未同步：{ss.wecom_error_hint(exc)}"}
    return {"rows": r["rows"],
            "message": f"近 {days} 天拉取 {r['fetched']} 条，新增 {r['inserted']}，更新 {r['updated']}，跳过 {r['skipped']}"}
