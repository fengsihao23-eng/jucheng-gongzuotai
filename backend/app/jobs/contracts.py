"""合同台账固定来源同步（企业微信在线表格 → 聚成工作台）。

频率按小时：企业微信「获取成员文档内容」对同一成员每天限 20 篇，留出余量；
需要更快可改 cron，但先把每天的读取次数控制在限额内。
"""
import logging

from sqlalchemy import select

from app.core.response import BizError
from app.jobs.registry import job
from app.models import ContractWorkbook
from app.services import contract_workbook_sync, contracts_sync, dict_service

log = logging.getLogger(__name__)


@job("contracts_source_sync", "合同台账固定来源同步", {"minute": 5}, "每小时第 5 分钟")
def sync_sources(db, params):
    configs = dict_service.items(db, contracts_sync.DICT_TYPE)
    lines, failures = [], []
    for key, config in configs.items():
        if not isinstance(config, dict) or config.get("enabled") is not True:
            continue
        try:
            result = contracts_sync.run(db, key, config)
            summary = result["summary"]
            lines.append(f"{key}：源表 {summary['rows']} 行（新增 {summary['create']}、变更 {summary['update']}、"
                         f"未变 {summary['unchanged']}、冲突 {summary['conflict']}）")
            applied = result.get("applied")
            if applied:
                lines.append(f"{key} 已自动落库：新增 {applied['created']}、更新 {applied['updated']}、"
                             f"跳过 {applied['skipped']}")
                if applied["failed"]:
                    lines.append(f"{key} 待人工处理：" + "；".join(
                        f"{f['contract_no'] or '无编号'}（{'，'.join(f['errors'])}）" for f in applied["failed"][:10]))
        except Exception as exc:
            db.rollback()
            if not isinstance(exc, BizError):
                log.warning("合同固定来源同步异常：%s (%s)", key, type(exc).__name__)
            failures.append(f"{key}：{exc.message if isinstance(exc, BizError) else '配置或源表格式无效'}")
    if failures:
        raise RuntimeError("；".join(lines + ["失败：" + "；".join(failures)])[:1200])
    if not lines:
        return {"rows": 0, "message": "没有启用的合同固定来源"}
    return {"rows": 1, "message": "；".join(lines)[:1200]}


@job(contract_workbook_sync.JOB, "企业微信合同工作簿同步", {"minute": f"*/{contract_workbook_sync.POLL_MINUTES}"},
     f"每 {contract_workbook_sync.POLL_MINUTES} 分钟检查版本")
def sync_workbooks(db, params):
    enabled = [(key, cfg) for key, cfg in db.execute(select(ContractWorkbook.source_key, ContractWorkbook.config))
               if cfg["enabled"]]
    lines, rows, errors = [], 0, 0
    for key, _ in enabled:
        try:
            result = contract_workbook_sync.sync(db, key)
            if result["changed"]:
                summary = result["summary"]
                rows += summary.get("rows", 0)
                lines.append(f"已更新 {summary['sheets']} 张子表、{summary.get('rows', 0)} 行；"
                             f"待复核 {summary.get('review', 0)} 行")
            else:
                lines.append("企微版本未变")
        except BizError as exc:
            db.rollback()
            if exc.code == 40901:
                lines.append("该来源正在同步")
            else:
                errors += 1
                lines.append("有来源读取失败，完整数据仍保留；详情见合同台账同步页面")
        except Exception:
            db.rollback()
            errors += 1
            log.exception("企业微信合同工作簿同步失败")
            lines.append("有来源未完成，详情见合同台账同步页面")
    return {"rows": rows, "message": "；".join(lines)[:1200] or "没有启用的合同工作簿",
            "status": "failed" if errors else "success"}
