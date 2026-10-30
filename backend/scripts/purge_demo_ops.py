"""清理演示业务数据：报销、银行流水、出纳日记账、财务数据、项目复盘、客服工单、人事考勤。

只删业务流水，不动用户、部门、角色、知识库、资料导入、合同/商机/项目主数据。
默认 dry-run，只打印将删除的行数；生产环境执行删除需同时加 --apply --confirm-prod。

用法：
  cd backend && python -m scripts.purge_demo_ops
  cd backend && python -m scripts.purge_demo_ops --apply --confirm-prod
"""
import argparse
import sys

from sqlalchemy import delete, func, select, update

from app.core.config import settings
from app.core.db import SessionLocal
from app.models import (BizContract, BizExpenseItem, BizExpenseReport, BizOpportunity, BizPayment,
                        BizProject, BizProjectCost, BizProjectReview, CsTicket, FinBankFlow, FinBankImport,
                        FinCashJournal, FinSubjectBalance, FinVoucher, HrAttendanceImport, HrAttendanceRecord,
                        HrAttendanceSummary, HrCandidate, HrContractAlert, HrDocument, HrEmployee, HrHeadcountPlan,
                        ImportBatch, KbDocument, RptDailyExpense, SyncLemonBatch, SyncTaxFiling, SysDepartment,
                        SysDict, SysUser)

# 删除顺序：先删引用方，再删被引用方（报销/日记账/成本引用流水，考勤明细引用导入批次）
DELETE_ORDER = [
    ("报销明细", BizExpenseItem),
    ("报销单", BizExpenseReport),
    ("出纳日记账", FinCashJournal),
    ("项目成本", BizProjectCost),
    ("银行流水", FinBankFlow),
    ("流水导入记录", FinBankImport),
    ("会计凭证", FinVoucher),
    ("科目余额", FinSubjectBalance),
    ("柠檬云同步批次", SyncLemonBatch),
    ("税务申报", SyncTaxFiling),
    ("每日支出报表快照", RptDailyExpense),
    ("项目复盘", BizProjectReview),
    ("客服工单", CsTicket),
    ("考勤明细", HrAttendanceRecord),
    ("考勤月度汇总", HrAttendanceSummary),
    ("考勤导入记录", HrAttendanceImport),
    ("合同到期预警", HrContractAlert),
    ("人事文书", HrDocument),
    ("招聘候选人", HrCandidate),
    ("编制计划", HrHeadcountPlan),
    ("人事档案", HrEmployee),
]

PRESERVED = [("用户", SysUser), ("部门", SysDepartment), ("知识文档", KbDocument), ("资料导入批次", ImportBatch),
             ("合同", BizContract), ("回款", BizPayment), ("项目", BizProject), ("商机", BizOpportunity)]


def count(db, model) -> int:
    return db.scalar(select(func.count()).select_from(model)) or 0


def main() -> None:
    parser = argparse.ArgumentParser(description="清理演示业务数据")
    parser.add_argument("--apply", action="store_true", help="真正执行删除；默认只 dry-run")
    parser.add_argument("--confirm-prod", action="store_true", help="生产环境确认执行删除")
    args = parser.parse_args()

    if args.apply and settings.environment == "prod" and not args.confirm_prod:
        sys.exit("生产环境执行删除需要同时加 --confirm-prod；请先确认已做好数据库备份")

    with SessionLocal() as db:
        print(f"环境：{settings.environment}  模式：{'执行删除' if args.apply else 'dry-run（不修改数据）'}")
        print("\n保留数据（删除前后应保持不变）：")
        preserved_before = {}
        for label, model in PRESERVED:
            preserved_before[label] = count(db, model)
            print(f"  {label:8s} {preserved_before[label]:>6d}")

        print("\n将清理的业务数据：")
        before = {}
        for label, model in DELETE_ORDER:
            before[label] = count(db, model)
            print(f"  {label:12s} {before[label]:>6d}")

        if not args.apply:
            print("\n这是 dry-run。确认无误后加 --apply --confirm-prod 执行删除。")
            return

        deleted = {}
        # 先解除引用关系，避免外键阻止删除
        db.execute(update(BizPayment).where(BizPayment.bank_flow_id.is_not(None)).values(bank_flow_id=None))
        db.execute(update(CsTicket).where(CsTicket.duplicate_of.is_not(None)).values(duplicate_of=None))
        for label, model in DELETE_ORDER:
            result = db.execute(delete(model))
            deleted[label] = result.rowcount or 0
        # 日记账期初余额也是演示数据；账户配置（bank_account）保留
        deleted["日记账期初"] = db.execute(delete(SysDict).where(SysDict.dict_type == "journal_opening")).rowcount or 0
        db.commit()

        print("\n已删除：")
        for label, n in deleted.items():
            print(f"  {label:12s} {n:>6d}")

        print("\n删除后核对：")
        for label, model in DELETE_ORDER:
            left = count(db, model)
            print(f"  {label:12s} {left:>6d}")
        for label, model in PRESERVED:
            now = count(db, model)
            flag = "OK" if now == preserved_before[label] else f"变化 {preserved_before[label]} -> {now}"
            print(f"  保留 {label:6s} {now:>6d}  {flag}")


if __name__ == "__main__":
    main()
