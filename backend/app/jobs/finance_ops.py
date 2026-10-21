"""财务运营定时任务：银行流水目录自动解析（FR-09-04）、柠檬云每晚增量同步+对账（FR-08-01/03）。"""


from app.core.timeutil import prev_month, today
from app.jobs.registry import job


@job("bank_flow_auto_parse", "流水目录自动解析", {"hour": 7, "minute": 0}, "每日 07:00")
def bank_flow_auto_parse(db, params):
    """扫描 STORAGE_DIR 同级 inbox/bankflow/ 新文件（按 sha256 幂等，已解析的文件跳过）。"""
    from app.integration.bankflow.parser import auto_parse_dir
    r = auto_parse_dir(db)
    db.commit()
    msg = f"解析 {r['files']} 个文件，新增 {r['rows']} 笔流水，跳过已解析 {r['skipped']} 个"
    if r["files"]:
        from app.services import notify_service
        notify_service.send(db, notify_service.users_by_roles(db, ["CW-02", "CW-01"]), "report", "银行流水已自动解析",
                            msg + "，请到流水核对工作台确认匹配建议。", link="/flows")
        db.commit()
    return {"rows": r["rows"], "message": msg}


@job("lemoncloud_sync", "柠檬云凭证/余额同步", {"hour": 23, "minute": 0}, "每晚 23:00",
     idempotency=lambda p: f"lemoncloud_sync:{p.get('period') or today().isoformat()}")
def lemoncloud_sync(db, params):
    """增量同步当月凭证+科目余额并对账；每月前 3 天同时补同步上月（月结后凭证调整）。"""
    from app.services import lemoncloud_service
    t = today()
    periods = [params["period"]] if params.get("period") else [f"{t:%Y-%m}"]
    if not params.get("period") and t.day <= 3:
        periods.insert(0, prev_month(f"{t:%Y-%m}"))
    msgs, rows, diffs = [], 0, 0
    for per in periods:
        b = lemoncloud_service.sync_api(db, per)
        db.commit()
        rows += b.voucher_count
        diffs += len(b.diffs or [])
        msgs.append(f"{per} {b.batch_no} {b.voucher_count} 张凭证，对账{ {'pass': '通过', 'diff': '有差异', 'error': '失败'}.get(b.reconcile_status, b.reconcile_status)}")
        if b.reconcile_status == "error":
            raise RuntimeError(f"柠檬云同步失败：{(b.errors or [{}])[0].get('message')}")
    if diffs:
        from app.services import notify_service
        notify_service.send(db, notify_service.users_by_roles(db, ["CW-01", "CW-03"]), "alert", "柠檬云对账存在差异",
                            "；".join(msgs), link="/finance-data")
        db.commit()
    return {"rows": rows, "message": "；".join(msgs)}


