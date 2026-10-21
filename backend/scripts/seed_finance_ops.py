"""财务运营领域种子：银行模板/柠檬云模板/日记账期初等口径字典；流水指纹统一口径；柠檬云同步批次（mock 通道，
当月 + 上月，上月含文件通道批次以演示双通道一致性）；近 3 个月税务申报（附生成的申报表 xlsx 原件）；1 笔待付款的项目采购成本。
"""
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app.core.timeutil import month_range, prev_month
from app.integration.bankflow.parser import BUILTIN_TEMPLATES
from app.integration.lemoncloud.base import DateRange
from app.integration.lemoncloud.mock import MockLemonCloud
from app.models import BizProjectCost, FinBankFlow, FinCashJournal
from app.services import bankflow_service, dict_service, lemoncloud_service, reconcile_service, tax_service

D = Decimal


def seed(db, ctx):
    T = ctx.T
    U = ctx.users
    # ---------- 口径字典 ----------
    for k, v in BUILTIN_TEMPLATES.items():
        dict_service.put(db, "bank_template", k, v, f"网银流水列映射：{v['name']}（可在系统管理-字典维护）")
    for k, v in bankflow_service.BUILTIN_ACCOUNTS.items():
        dict_service.put(db, "bank_account", k, v, "本公司银行账户（展示名 ↔ 账号尾号）")
    dict_service.put(db, "lemon_template", "standard", lemoncloud_service.BUILTIN_TEMPLATE, "柠檬云凭证导出列映射")
    dict_service.put(db, "reconcile_rule", "default", reconcile_service.DEFAULT_RULE, "流水智能匹配规则与自动核对阈值")
    dict_service.put(db, "tax_template", "default", tax_service.DEFAULT_RULE, "税务申报表识别关键字")

    # ---------- 流水指纹统一为导入口径（账号+日期+方向+金额+摘要+对方+余额） ----------
    flows = db.scalars(select(FinBankFlow)).all()
    for f in flows:
        f.fingerprint = bankflow_service.flow_fingerprint(f)
    db.flush()

    # ---------- 日记账期初（账户启用日 = 首笔流水日；期初 = 核心种子开户余额） ----------
    from scripts.seed_core import ACCOUNTS
    base = {ACCOUNTS["cmb"]: 16_000_000, ACCOUNTS["icbc"]: 3_500_000}
    for acct, amt in base.items():
        first = db.scalar(select(func.min(FinBankFlow.trade_date)).where(FinBankFlow.bank_account == acct))
        dict_service.put(db, "journal_opening", acct, {"date": first.isoformat(), "amount": amt}, "银行日记账启用期初")
    first_cash = db.scalar(select(func.min(FinCashJournal.journal_date)).where(FinCashJournal.account_type == "cash"))
    dict_service.put(db, "journal_opening", "cash", {"date": (first_cash or T).isoformat(), "amount": 0}, "现金日记账启用期初")

    # ---------- 待付款的项目采购成本（流水核对演示：项目成本匹配） ----------
    db.add(BizProjectCost(project_code="JC-XY-2026-001", cost_date=T - timedelta(days=2), cost_type="采购",
                          amount=D("86400.00"), summary="锐捷网络股份有限公司 核心交换机采购（待付款）",
                          created_by=U["sunyan"].id))
    db.flush()

    # ---------- 柠檬云同步批次（mock） ----------
    cur = f"{T:%Y-%m}"
    prev = prev_month(cur)
    lemoncloud_service.sync_api(db, prev)
    ps, pe = month_range(prev)
    vs = MockLemonCloud(db).fetch_vouchers(DateRange(ps, pe))
    data = lemoncloud_service.export_vouchers_xlsx(vs, f"凭证列表 {prev}")
    lemoncloud_service.sync_file(db, data, f"柠檬云凭证导出_{prev}.xlsx", uploader_id=U["sunyan"].id, period=prev)
    lemoncloud_service.sync_api(db, cur)
    db.flush()

    # ---------- 税务申报（近 3 个月，按次月 15 日缴纳的“增值税及附加”流水反推） ----------
    for back in (3, 2, 1):
        per = cur
        for _ in range(back):
            per = prev_month(per)
        ns, ne = month_range(_next(per))
        paid = db.scalar(select(func.sum(FinBankFlow.amount)).where(
            FinBankFlow.summary == "增值税及附加", FinBankFlow.trade_date.between(ns, ne))) or D("150000")
        vat = (D(paid) / D("1.12")).quantize(D("0.01"))
        sur = D(paid) - vat
        sales = (vat / D("0.03")).quantize(D("0.01"))  # 演示：按 3% 实际税负率反推计税销售额
        filing_date = ne.replace(day=15) if ne.day >= 15 else ne
        items = [{"tax_type": "增值税", "period": per, "taxable": sales, "payable": vat, "paid": vat, "filing_date": filing_date},
                 {"tax_type": "附加税", "period": per, "taxable": vat, "payable": sur, "paid": sur, "filing_date": filing_date},
                 {"tax_type": "个人所得税", "period": per, "taxable": D(596_000 + back * 7_300), "payable": D(19_860 + back * 412),
                  "paid": D(19_860 + back * 412), "filing_date": filing_date}]
        if per.endswith(("-03", "-06", "-09", "-12")):
            items.append({"tax_type": "企业所得税", "period": per, "taxable": D("1286000.00"), "payable": D("64300.00"),
                          "paid": D("0"), "filing_date": filing_date})
        tax_service.register(db, tax_service.make_tax_xlsx(items), f"税务申报表_{per}.xlsx", U["sunyan"].id)
    db.flush()


def _next(period: str) -> str:
    y, m = map(int, period.split("-"))
    return f"{y + (m == 12)}-{m % 12 + 1:02d}"
