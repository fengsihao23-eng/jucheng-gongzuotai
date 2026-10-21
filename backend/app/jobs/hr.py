"""人事定时任务：合同/试用期到期扫描（PRD 6.5 hr_contract_alert）、月度考勤汇总 + 人力分析 PPT。"""
from app.core.storage import file_brief
from app.core.timeutil import prev_month, today
from app.jobs.registry import job
from app.services import attendance_service as att
from app.services import hr_service, notify_service


@job("hr_contract_alert", "合同/试用期到期扫描", {"hour": 9, "minute": 0}, "每日 09:00",
     idempotency=lambda p: f"hr_alert:{p.get('date') or today().isoformat()}")
def hr_contract_alert(db, params):
    r = hr_service.scan_alerts(db, notify=params.get("notify", True))
    db.commit()
    return {"rows": r["created"], "message": f"命中 {r['hits']} 条（新增 {r['created']}），通知 {r['notified_users']} 人；"
                                            f"提前量 合同 {r['rule']['contract_lead_days']} 天 / 试用期 {r['rule']['probation_lead_days']} 天"}


@job("hr_monthly_analysis", "月度考勤汇总与人力分析 PPT", {"day": 2, "hour": 9, "minute": 30}, "每月 2 日 09:30",
     idempotency=lambda p: f"hr_monthly:{p.get('period') or prev_month(f'{today():%Y-%m}')}")
def hr_monthly_analysis(db, params):
    period = params.get("period") or prev_month(f"{today():%Y-%m}")
    hr_users = notify_service.users_by_roles(db, ["RS-01"])
    ppt = hr_service.build_ppt(db, period)
    lines = [f"**{period} 月度人力分析**", hr_service.analysis_brief(db, period)]
    att_file = None
    if att.latest_import(db, period):
        s = att.attendance_summary(db, period)
        att_file = s["file"]
        lines.append(s["analysis"].split("\n")[0])
        lines.append(f"[考勤汇总表]({att_file['url']})")
    else:
        lines.append(f"> {period} 考勤 Excel 尚未导入，请在人事中心上传后重新生成考勤汇总。")
    lines.append(f"[人力分析 PPT 初稿]({file_brief(ppt)['url']})")
    notify_service.send(db, hr_users, "hr_monthly", f"{period} 考勤汇总与人力分析", "\n".join(lines),
                        link="/hr?tab=analysis", ref_type="hr_monthly", ref_id=period)
    db.commit()
    return {"rows": 1 + bool(att_file), "message": f"{period} PPT 已生成，推送 HR {len(hr_users)} 人"}
