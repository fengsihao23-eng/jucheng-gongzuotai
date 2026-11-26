"""报表引擎（PRD 3.5 / FR-04 / 7.1 / 7.2）。

每种报表 = 取数函数 collect(db, ctx) -> metrics(dict，原始 Decimal) + 渲染器 render(metrics, ctx) -> [(filename, bytes)]
+ 快照：rpt_report（同 type+period 再生成 version+1）；rpt_daily_expense / rpt_project_profit 明细快照随版本保存。
文件归档 report/{type}/{yyyy-MM}/。所有项目利润数字统一调用 profit_service.project_profit（与看板 / MCP 一致）。

对外：
  catalog(db)                                   报表类型目录（口径注释、计划时间）
  normalize_period(type, period, project_codes) 期间解析
  generate(db, report_type, period, trigger, user_id, project_codes=None) -> RptReport（只 flush，不 commit）
  regenerate_all(db, user_id)                   一键更新：重算当日/本周/本月全部报表
"""
import hashlib
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.response import encode
from app.core.permissions import BUSINESS_LINES
from app.core.storage import save_bytes
from app.core.timeutil import (day_bounds, last_week, month_range, now, prev_month, today, week_no, week_range)
from app.models import (BizContract, BizExpenseReport, BizOpportunity, BizPayment, BizPaymentPlan, BizProject,
                        BizProjectFeedback, BizProjectMilestone, FinBankFlow, FinSubjectBalance, FinVoucher,
                        OaSubmission, RptDailyExpense, RptProjectProfit, RptReport, SyncTaxFiling, SysDepartment,
                        SysFile, SysUser)
from app.models.business import EXPENSE_STATUS, MILESTONE_STATUS, PROJECT_STATUS
from app.models.finance import FLOW_MATCH_STATUS
from app.services import dict_service, profit_service
from app.services.report_render import DocxDoc, XlsxBook, pct, wan, yuan

ZERO = Decimal("0")
LINES = BUSINESS_LINES
OPP_STAGES = {"lead": "线索", "contact": "初步接触", "quote": "方案报价", "negotiation": "商务谈判", "won": "赢单",
              "lost": "丢单"}
PUBLIC = "公共费用"
UNALLOCATED = "待归集"

# ---------------------------------------------------------------- 报表目录
REPORT_TYPES: dict[str, dict] = {
    "daily_expense": {
        "name": "每日支出报表", "period_kind": "day", "job": "daily_expense_report", "schedule": "每日 08:00（昨日）",
        "prd": "FR-04-01 / 3.5", "formats": ["xlsx"], "menus": ["expenses", "board", "profit"],
        "caliber": [
            "统计区间：指定日 00:00–24:00（东八区），定时任务于次日 08:00 生成并推送财务经理与总办。",
            "报销支出：审批通过时间 approved_at 落在统计日、状态为「已通过 / 已归集」的报销单金额（不含审批中、已驳回）。",
            "流水支出：交易日为统计日、出纳已核对的银行流水支出中「公户支出 / 已关联项目成本」两类；"
            "「已关联报销」的流水是报销付款，已在报销口径内计入，不重复统计。",
            "未核对的支出流水不计入合计，单列于明细 sheet 供出纳跟进（进入待核对池）。",
            "部门维度：报销按申请人所在部门；公户支出计入「公司公户」，项目采购成本计入「项目采购（公户）」。",
            "项目维度：报销按「所属项目」归集；未归集到项目的已归集报销计入「公共费用」，已通过但未归集的计入「待归集」；"
            "项目采购成本流水按关联项目编码。",
            "合计 = 报销支出 + 流水支出；两个维度的合计相等，与数据库按同口径聚合一致。",
        ],
    },
    "daily_bankflow": {
        "name": "每日银行流水报表", "period_kind": "day", "job": "daily_bankflow_report", "schedule": "每日 08:10（昨日）",
        "prd": "FR-09 / PPT-3", "formats": ["xlsx"], "menus": ["flows", "journal"],
        "caliber": [
            "数据源：fin_bank_flow（网银流水文件解析入库，按指纹去重）。按交易日统计。",
            "账户维度：期初余额 = 统计日之前最后一笔流水的余额；期末余额 = 当日最后一笔流水的余额（无当日流水则沿用期初）。",
            "余额勾稽：期初 + 收入 − 支出 应等于期末，不等时标注「勾稽差异」。",
            "核对状态按 FLOW_MATCH_STATUS：未核对 / 已关联报销 / 已关联回款 / 公户支出 / 已关联项目成本 / 其他收入。",
            "未核对清单包含统计日当日未核对流水；另列截至统计日累计的待核对池笔数与金额。",
        ],
    },
    "weekly_profit": {
        "name": "每周项目利润表", "period_kind": "week", "job": "weekly_project_profit", "schedule": "每周一 09:00（上周）",
        "prd": "FR-04-02 / 3.3 / 3.5", "formats": ["xlsx"], "menus": ["profit"],
        "caliber": [
            "统计周期：自然周（周一至周日，ISO 周号）；每周一 09:00 生成上周报表。",
            "取数：统一调用共享利润口径 profit_service.project_profit，与经营看板、WorkBuddy fin.project_profit 数字一致。",
            "收入口径（sys_dict caliber.profit.income_basis）：contract=截至期末已签合同额；payment=截至期末已到账回款。",
            "成本口径（cost_sources）：expense=已归集到项目的报销（按费用发生日）；project_cost=人工标记的采购/分包成本；"
            "report_payable=项目报表截止日的待支付应付快照（计入累计成本，不计本期发生）；"
            "lemon_aux=柠檬云项目辅助核算成本类科目（5 开头借方）。",
            "历史项目报表按凭证记账日计入采购、施工及报销，保留负数冲销；待核对凭证和税费测算不计入。"
            "待支付按同一项目最新一期报表的应付余额计入累计成本，多期报表只取最新一期，避免跨期重复。"
            "多合同同一项目按来源报表合并核算，不向各合同猜测分摊。",
            "累计 = 截至周末；本周发生 = 周一至周日区间内发生额。毛利 = 收入 − 成本；毛利率 = 毛利 ÷ 收入。",
            "公共费用（未归集到项目的已归集报销）不摊入项目毛利，单列为「管理费用-公共」。",
            "毛利率低于预警线（sys_dict report_rule.profit_alert_margin，默认 20%）的项目标红。",
        ],
    },
    "company_weekly": {
        "name": "公司周报", "period_kind": "week", "job": "company_weekly_report", "schedule": "每周一 10:00（上周）",
        "prd": "FR-04-03 / 7.2", "formats": ["docx", "xlsx"], "menus": ["board"],
        "caliber": [
            "统计周期：自然周（周一至周日）。docx 为初稿，供总经办人工润色后发布；xlsx 为数据附表。",
            "签约：签订日期落在本周的合同金额（排除已终止）；回款：回款登记日期（=流水到账日）落在本周的回款。",
            "当月回款达成率 = 本月 1 日至周末回款 ÷ 当月回款目标（sys_dict payment_target）。",
            "商机：本周新增商机（创建时间）与当前未关闭商机漏斗（按阶段金额）。",
            "重点项目进展：is_key 项目本周的进展反馈（biz_project_feedback）与当前节点；节点延期：状态为延期的流程节点。",
            "周报提交率：以企微汇报记录（oa_submission）为准，应提交范围见 sys_dict report_rule.submit，按部门统计。",
            "本周支出：口径同每日支出报表（审批通过报销 + 已核对流水支出），按区间汇总。",
        ],
    },
    "company_monthly": {
        "name": "公司月报", "period_kind": "month", "job": "monthly_company_report", "schedule": "每月 1 日 10:00（上月）",
        "prd": "FR-04-04 / 3.5", "formats": ["xlsx", "docx"], "menus": ["board", "finance_data"],
        "caliber": [
            "统计周期：自然月。整合柠檬云凭证汇总、科目余额、银行流水、税务申报、回款/签约/商机、月报提交率。",
            "柠檬云凭证汇总：按科目编码首位归类（1 资产 / 2 负债 / 3 共同 / 4 所有者权益 / 5 成本 / 6 损益），借贷分列；"
            "未同步到柠檬云数据时注明「数据缺失」，不以其他数据代替。",
            "账务口径财务分析（来自凭证）：营业收入 = 收入类科目贷方发生额；营业成本 = 成本类科目借方发生额；"
            "期间费用 = 销售/管理/财务费用借方发生额；科目映射见 sys_dict report_rule.subject_map，摘要含「结转」的期末结转凭证不计入。",
            "业务口径财务分析（来自台账）：收入 = 当月签约额；成本 = 当月归集到项目的报销 + 采购成本；费用 = 当月公共费用；"
            "两种口径并列展示，不互相替代。",
            "银行流水：交易日在本月的收入/支出，按账户与核对状态分类。税务申报：sync_tax_filing 中本期申报记录。",
            "环比：与上月同口径数据比较；上月数据缺失时环比为空。月报提交率以 oa_submission 为准。",
        ],
    },
    "project_operation": {
        "name": "在建/运营项目报表", "period_kind": "week", "job": "project_operation_report",
        "schedule": "每周一 09:30（上周）", "prd": "7.1 / FR-05-06 / PPT-7", "formats": ["xlsx"],
        "menus": ["profit", "board", "contracts"],
        "caliber": [
            "范围：状态为在建（2）的项目列入「在建项目」，已验收（3）/运维（4）列入「运营项目」。截至统计周周末。",
            "合同额：项目下有效合同（排除已终止）金额合计；累计回款：截至周末回款合计；回款率 = 累计回款 ÷ 合同额。",
            "成本 / 毛利：调用 profit_service.project_profit（与周利润表一致）。",
            "当前节点：第一个未完成节点（按序号）；延期节点：状态为延期，或计划日早于统计截止日且未完成的节点。",
            "逾期回款：回款计划到期日早于统计截止日且未全额到账的计划。",
        ],
    },
}


def caliber_lines(db: Session, report_type: str) -> list[str]:
    """口径注释：sys_dict report_caliber.{type} 可覆盖（评审变更只改配置）。"""
    v = dict_service.get(db, "report_caliber", report_type)
    return list(v) if isinstance(v, list) and v else REPORT_TYPES[report_type]["caliber"]


def allowed(p, report_type: str) -> bool:
    """报表可见性：报表中心菜单之外，还需具备该报表数据所属的任一菜单（财务敏感报表不对市场专员开放）。"""
    from app.services.market_service import PROJECT_REPORT_TYPES
    if report_type in PROJECT_REPORT_TYPES:
        return False
    spec = REPORT_TYPES.get(report_type)
    if spec is None or p.is_admin:
        return True
    return any(p.has_menu(m) for m in spec["menus"])


# ---------------------------------------------------------------- 期间
def normalize_period(report_type: str, period: str | None = None, project_codes: list[str] | None = None) -> dict:
    """返回 {period, start, end, label, kind, project_codes}。默认：日=昨日，周=上周，月=上月。"""
    if report_type not in REPORT_TYPES:
        from app.core.response import bad_request
        raise bad_request(f"未知报表类型 {report_type}")
    kind = REPORT_TYPES[report_type]["period_kind"]
    T = today()
    try:
        if kind == "day":
            d = date.fromisoformat(period) if period else T - timedelta(days=1)
            p, s, e, label = d.isoformat(), d, d, f"{d:%Y-%m-%d}"
        elif kind == "week":
            if period and len(period) == 10:  # 允许直接给日期，取其所在周
                period = week_no(date.fromisoformat(period))
            p = period or last_week(T)
            s, e = week_range(p)
            label = f"{p}（{s:%m-%d}~{e:%m-%d}）"
        else:
            p = period[:7] if period else prev_month(f"{T:%Y-%m}")
            s, e = month_range(p)
            label = f"{s:%Y年%m月}"
    except (ValueError, IndexError) as exc:
        from app.core.response import bad_request
        raise bad_request(f"期间格式错误：{period}（日 2026-10-19 / 周 2026-W42 / 月 2026-10）") from exc
    if s > T:
        from app.core.response import bad_request
        raise bad_request("不能生成未来期间的报表")
    codes = sorted(set(project_codes or [])) or None
    if codes:
        p = f"{p}:{codes[0]}" if len(codes) == 1 else f"{p}:P{hashlib.sha1(','.join(codes).encode()).hexdigest()[:8]}"
        label += "（" + "、".join(codes[:3]) + ("等" if len(codes) > 3 else "") + "）"
    return {"period": p, "start": s, "end": e, "label": label, "kind": kind, "project_codes": codes,
            "data_end": min(e, T)}


# ---------------------------------------------------------------- 公共取数
def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else ZERO


def _ratio(a, b) -> Decimal | None:
    return (Decimal(a) / Decimal(b)).quantize(Decimal("0.0001")) if b else None


def _dept_names(db: Session) -> dict[str, str]:
    return {d.dept_code: d.name for d in db.scalars(select(SysDepartment))}


def _project_names(db: Session) -> dict[str, str]:
    return dict(db.execute(select(BizProject.project_code, BizProject.name)).all())


def _user_names(db: Session, ids) -> dict[int, str]:
    ids = {i for i in ids if i}
    return dict(db.execute(select(SysUser.id, SysUser.name).where(SysUser.id.in_(ids))).all()) if ids else {}


def _dt_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    return day_bounds(start)[0], day_bounds(end)[1]


def expense_core(db: Session, start: date, end: date) -> dict:
    """支出口径（每日支出报表 / 周报本周支出 / 月报共用）：审批通过报销[approved_at] + 已核对流水支出。"""
    t0, t1 = _dt_bounds(start, end)
    exps = db.scalars(select(BizExpenseReport).where(
        BizExpenseReport.approved_at >= t0, BizExpenseReport.approved_at < t1, BizExpenseReport.status.in_([2, 4]))
        .order_by(BizExpenseReport.approved_at, BizExpenseReport.id)).all()
    flows = db.scalars(select(FinBankFlow).where(
        FinBankFlow.trade_date >= start, FinBankFlow.trade_date <= end, FinBankFlow.direction == 2)
        .order_by(FinBankFlow.trade_date, FinBankFlow.trade_time, FinBankFlow.id)).all()
    depts, projs = _dept_names(db), _project_names(db)
    users = _user_names(db, [e.applicant_id for e in exps])
    items, excluded = [], {"unmatched": [], "reimburse_paid": []}
    for e in exps:
        if e.project_code:
            pk, pn = e.project_code, projs.get(e.project_code, e.project_code)
        elif e.status == 4:
            pk, pn = PUBLIC, "管理费用-公共"
        else:
            pk, pn = UNALLOCATED, "已通过待归集"
        items.append({"source": "报销", "ref": e.approval_no, "date": e.approved_at.astimezone(t0.tzinfo).date(),
                      "time": e.approved_at, "expense_date": e.expense_date, "who": users.get(e.applicant_id, ""),
                      "dept_key": e.dept_code or "未知", "dept_name": depts.get(e.dept_code or "", e.dept_code or "未知部门"),
                      "project_key": pk, "project_name": pn, "category": e.category, "amount": e.amount,
                      "summary": e.reason or "", "status": EXPENSE_STATUS.get(e.status, str(e.status))})
    for f in flows:
        row = {"ref": f"流水#{f.id}", "date": f.trade_date, "account": f.bank_account, "counterparty": f.counterparty,
               "amount": f.amount, "summary": f.summary or "", "status": FLOW_MATCH_STATUS.get(f.match_status, "")}
        if f.match_status == 0:
            excluded["unmatched"].append(row)
            continue
        if f.match_status == 1:
            excluded["reimburse_paid"].append(row)
            continue
        if f.match_status == 4:
            pk, pn, dk, dn, cat = f.biz_ref or "未知项目", projs.get(f.biz_ref or "", f.biz_ref or ""), "PURCHASE", "项目采购（公户）", "项目采购成本"
        else:
            pk, pn, dk, dn, cat = "CORP", "公户支出（工资/社保/税费/房租等）", "CORP", "公司公户", "公户支出"
        items.append({"source": "流水", "ref": row["ref"], "date": f.trade_date, "time": f.trade_time,
                      "expense_date": f.trade_date, "who": f.counterparty or "", "dept_key": dk, "dept_name": dn,
                      "project_key": pk, "project_name": pn, "category": cat, "amount": f.amount,
                      "summary": f.summary or "", "status": row["status"]})

    def group(key, name):
        g: dict[str, dict] = {}
        for it in items:
            r = g.setdefault(it[key], {"key": it[key], "name": it[name], "total": ZERO, "count": 0,
                                       "expense_amount": ZERO, "flow_amount": ZERO})
            r["total"] += it["amount"]
            r["count"] += 1
            r["expense_amount" if it["source"] == "报销" else "flow_amount"] += it["amount"]
        return sorted(g.values(), key=lambda r: -r["total"])

    cats: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for it in items:
        cats[it["category"]] += it["amount"]
    exp_total = sum((i["amount"] for i in items if i["source"] == "报销"), ZERO)
    flow_total = sum((i["amount"] for i in items if i["source"] == "流水"), ZERO)
    return {
        "items": items, "by_dept": group("dept_key", "dept_name"), "by_project": group("project_key", "project_name"),
        "by_category": [{"category": k, "total": v} for k, v in sorted(cats.items(), key=lambda x: -x[1])],
        "total": exp_total + flow_total, "expense_total": exp_total, "flow_total": flow_total,
        "expense_count": sum(1 for i in items if i["source"] == "报销"),
        "flow_count": sum(1 for i in items if i["source"] == "流水"),
        "excluded": {"unmatched_count": len(excluded["unmatched"]),
                     "unmatched_amount": sum((r["amount"] for r in excluded["unmatched"]), ZERO),
                     "reimburse_paid_count": len(excluded["reimburse_paid"]),
                     "reimburse_paid_amount": sum((r["amount"] for r in excluded["reimburse_paid"]), ZERO),
                     "unmatched": excluded["unmatched"]},
    }


# ================================================================ 1. 每日支出
def collect_daily_expense(db: Session, ctx: dict) -> dict:
    core = expense_core(db, ctx["start"], ctx["end"])
    core["stat_date"] = ctx["start"]
    core["summary"] = [
        {"label": "支出合计", "value": core["total"], "kind": "money"},
        {"label": "报销支出", "value": core["expense_total"], "kind": "money"},
        {"label": "流水支出", "value": core["flow_total"], "kind": "money"},
        {"label": "笔数", "value": core["expense_count"] + core["flow_count"], "kind": "int"},
        {"label": "未核对流水", "value": core["excluded"]["unmatched_count"], "kind": "int"},
    ]
    return core


def render_daily_expense(db, m: dict, ctx: dict, lines: list[str]) -> list[tuple[str, bytes]]:
    b = XlsxBook()
    dim_cols = [("name", "名称", "text"), ("key", "编码", "text"), ("count", "笔数", "int"),
                ("expense_amount", "报销支出", "money"), ("flow_amount", "流水支出", "money"), ("total", "合计", "money")]
    tot = {"name": "合计", "count": sum(r["count"] for r in m["by_dept"]), "expense_amount": m["expense_total"],
           "flow_amount": m["flow_total"], "total": m["total"]}
    b.kv_sheet("汇总", [("统计日", ctx["label"], "text"), ("支出合计", m["total"], "money"),
                       ("其中：审批通过报销", m["expense_total"], "money"), ("报销笔数", m["expense_count"], "int"),
                       ("其中：已核对流水支出", m["flow_total"], "money"), ("流水笔数", m["flow_count"], "int"),
                       ("未核对支出流水（未计入）", m["excluded"]["unmatched_amount"], "money"),
                       ("未核对笔数", m["excluded"]["unmatched_count"], "int"),
                       ("报销付款流水（已含在报销中）", m["excluded"]["reimburse_paid_amount"], "money")])
    b.sheet("按部门", dim_cols, m["by_dept"], total=tot)
    b.sheet("按项目", dim_cols, m["by_project"], total=tot,
            alert=lambda r: r["key"] == UNALLOCATED)
    b.sheet("支出明细", [("source", "来源", "text"), ("ref", "单号", "text"), ("date", "统计日期", "date"),
                     ("expense_date", "费用发生日", "date"), ("who", "申请人/对方", "text"), ("dept_name", "部门", "text"),
                     ("project_key", "项目编码", "text"), ("project_name", "项目/归集", "text"),
                     ("category", "类别", "text"), ("amount", "金额", "money"), ("summary", "事由/摘要", "text"),
                     ("status", "状态", "text")], m["items"], total={"source": "合计", "amount": m["total"]})
    b.sheet("未核对流水", [("ref", "流水", "text"), ("date", "交易日", "date"), ("account", "账户", "text"),
                      ("counterparty", "对方", "text"), ("amount", "金额", "money"), ("summary", "摘要", "text")],
            m["excluded"]["unmatched"], total={"ref": "合计", "amount": m["excluded"]["unmatched_amount"]},
            note="未核对流水不计入当日支出合计，请出纳在「流水核对」中处理。")
    b.caliber_sheet("每日支出报表", lines, {"统计日": ctx["label"], "生成时间": f"{now():%Y-%m-%d %H:%M}"})
    return [(f"每日支出报表_{ctx['period']}.xlsx", b.to_bytes())]


def snapshot_daily_expense(db: Session, report: RptReport, m: dict) -> None:
    for dim, rows in (("dept", m["by_dept"]), ("project", m["by_project"])):
        for r in rows:
            db.add(RptDailyExpense(stat_date=m["stat_date"], dimension=dim, dim_value=str(r["key"])[:64],
                                   total_amount=r["total"], expense_count=r["count"], report_id=report.id))


# ================================================================ 2. 每日银行流水
def collect_daily_bankflow(db: Session, ctx: dict) -> dict:
    d = ctx["start"]
    accounts = [a for (a,) in db.execute(select(FinBankFlow.bank_account).distinct().order_by(FinBankFlow.bank_account))]
    rows, acc_rows = [], []
    for acct in accounts:
        prev = db.scalar(select(FinBankFlow).where(FinBankFlow.bank_account == acct, FinBankFlow.trade_date < d)
                         .order_by(FinBankFlow.trade_date.desc(), FinBankFlow.trade_time.desc().nulls_last(),
                                   FinBankFlow.id.desc()).limit(1))
        day = db.scalars(select(FinBankFlow).where(FinBankFlow.bank_account == acct, FinBankFlow.trade_date == d)
                         .order_by(FinBankFlow.trade_time.nulls_first(), FinBankFlow.id)).all()
        opening = prev.balance if prev and prev.balance is not None else None
        inc = sum((f.amount for f in day if f.direction == 1), ZERO)
        out = sum((f.amount for f in day if f.direction == 2), ZERO)
        closing = day[-1].balance if day and day[-1].balance is not None else opening
        diff = (opening + inc - out - closing) if opening is not None and closing is not None else None
        acc_rows.append({"account": acct, "opening": opening, "income": inc, "outflow": out, "net": inc - out,
                         "closing": closing, "count": len(day), "check": "一致" if diff == 0 else
                         ("无期初" if diff is None else f"勾稽差异 {diff}")})
        for f in day:
            rows.append({"id": f.id, "account": acct, "date": f.trade_date, "time": f.trade_time,
                         "direction": "收入" if f.direction == 1 else "支出",
                         "income": f.amount if f.direction == 1 else None,
                         "outflow": f.amount if f.direction == 2 else None, "amount": f.amount,
                         "counterparty": f.counterparty, "summary": f.summary, "balance": f.balance,
                         "match_status": f.match_status, "status": FLOW_MATCH_STATUS.get(f.match_status, ""),
                         "biz_ref": f.biz_ref, "source_file": f.source_file})
    by_status: dict[str, dict] = {}
    for r in rows:
        s = by_status.setdefault(r["status"], {"status": r["status"], "count": 0, "income": ZERO, "outflow": ZERO})
        s["count"] += 1
        s["income" if r["direction"] == "收入" else "outflow"] += r["amount"]
    pool = db.execute(select(func.count(), func.coalesce(func.sum(FinBankFlow.amount), 0)).where(
        FinBankFlow.match_status == 0, FinBankFlow.trade_date <= d)).one()
    unmatched = [r for r in rows if r["match_status"] == 0]
    tin = sum((a["income"] for a in acc_rows), ZERO)
    tout = sum((a["outflow"] for a in acc_rows), ZERO)
    closing_total = sum((a["closing"] or ZERO for a in acc_rows), ZERO)
    return {"stat_date": d, "accounts": acc_rows, "flows": rows, "by_status": list(by_status.values()),
            "unmatched": unmatched, "income_total": tin, "outflow_total": tout, "net_total": tin - tout,
            "closing_total": closing_total, "flow_count": len(rows), "unmatched_count": len(unmatched),
            "unmatched_amount": sum((r["amount"] for r in unmatched), ZERO),
            "pool_count": int(pool[0]), "pool_amount": _d(pool[1]),
            "summary": [{"label": "当日收入", "value": tin, "kind": "money"},
                        {"label": "当日支出", "value": tout, "kind": "money"},
                        {"label": "期末余额合计", "value": closing_total, "kind": "money"},
                        {"label": "流水笔数", "value": len(rows), "kind": "int"},
                        {"label": "当日未核对", "value": len(unmatched), "kind": "int"}]}


def render_daily_bankflow(db, m: dict, ctx: dict, lines: list[str]) -> list[tuple[str, bytes]]:
    b = XlsxBook()
    b.sheet("账户汇总", [("account", "账户", "text"), ("opening", "期初余额", "money"), ("income", "收入", "money"),
                     ("outflow", "支出", "money"), ("net", "净额", "money"), ("closing", "期末余额", "money"),
                     ("count", "笔数", "int"), ("check", "余额勾稽", "text")], m["accounts"],
            total={"account": "合计", "income": m["income_total"], "outflow": m["outflow_total"],
                   "net": m["net_total"], "closing": m["closing_total"], "count": m["flow_count"]},
            alert=lambda r: r["check"] not in ("一致",))
    b.sheet("核对状态", [("status", "核对状态", "text"), ("count", "笔数", "int"), ("income", "收入", "money"),
                     ("outflow", "支出", "money")], m["by_status"])
    flow_cols = [("account", "账户", "text"), ("time", "交易时间", "text"), ("direction", "收支", "text"),
                 ("income", "收入", "money"), ("outflow", "支出", "money"), ("balance", "余额", "money"),
                 ("counterparty", "对方户名", "text"), ("summary", "摘要", "text"), ("status", "核对状态", "text"),
                 ("biz_ref", "关联单号", "text"), ("source_file", "来源文件", "text")]
    fmt_rows = [{**r, "time": f"{r['time']:%Y-%m-%d %H:%M}" if r["time"] else str(r["date"])} for r in m["flows"]]
    b.sheet("流水明细", flow_cols, fmt_rows, total={"account": "合计", "income": m["income_total"],
                                                "outflow": m["outflow_total"]},
            alert=lambda r: r["match_status"] == 0)
    b.sheet("未核对清单", flow_cols, [r for r in fmt_rows if r["match_status"] == 0],
            total={"account": "合计", "income": sum((r["income"] or ZERO for r in m["unmatched"]), ZERO),
                   "outflow": sum((r["outflow"] or ZERO for r in m["unmatched"]), ZERO)},
            note=f"截至 {ctx['label']} 待核对池累计 {m['pool_count']} 笔，金额 {yuan(m['pool_amount'])}。")
    b.caliber_sheet("每日银行流水报表", lines, {"统计日": ctx["label"], "生成时间": f"{now():%Y-%m-%d %H:%M}"})
    return [(f"每日银行流水报表_{ctx['period']}.xlsx", b.to_bytes())]


# ================================================================ 3. 每周项目利润表
def alert_margin(db: Session) -> Decimal:
    rule = dict_service.get(db, "report_rule", "profit") or {}
    return Decimal(str(rule.get("alert_margin", "0.2")))


def public_expense_detail(db: Session, start: date, end: date) -> list[dict]:
    rows = db.execute(select(BizExpenseReport.category, func.count(), func.sum(BizExpenseReport.amount)).where(
        BizExpenseReport.status == 4, BizExpenseReport.project_code.is_(None),
        BizExpenseReport.expense_date >= start, BizExpenseReport.expense_date <= end)
        .group_by(BizExpenseReport.category).order_by(func.sum(BizExpenseReport.amount).desc())).all()
    return [{"category": c, "count": n, "amount": _d(a)} for c, n, a in rows]


def collect_weekly_profit(db: Session, ctx: dict) -> dict:
    pr = profit_service.project_profit(db, ctx["start"], ctx["end"], ctx["project_codes"])
    th = alert_margin(db)
    rows = []
    for r in pr["rows"]:
        rows.append({**r, "line_name": LINES.get(r["business_line"], r["business_line"]),
                     "status_name": PROJECT_STATUS.get(r["status"], ""),
                     "period_gross_profit": r["period_income"] - r["period_cost"],
                     "low_margin": r["gross_margin"] is not None and r["gross_margin"] < th})
    t = pr["totals"]
    return {"week": ctx["period"].split(":")[0], "start": ctx["start"], "end": ctx["end"], "caliber": pr["caliber"],
            "alert_margin": th, "rows": rows, "totals": t, "public_expense": pr["public_expense"],
            "public_detail": public_expense_detail(db, ctx["start"], ctx["end"]),
            "period_expense": sum((r["period_expense"] for r in rows), ZERO),
            "low_margin_count": sum(1 for r in rows if r["low_margin"]), "project_count": len(rows),
            "summary": [{"label": "累计收入", "value": t["income"], "kind": "money"},
                        {"label": "累计成本", "value": t["cost"], "kind": "money"},
                        {"label": "毛利", "value": t["gross_profit"], "kind": "money"},
                        {"label": "毛利率", "value": t["gross_margin"], "kind": "pct"},
                        {"label": "本周成本", "value": t["period_cost"], "kind": "money"},
                        {"label": "公共费用", "value": pr["public_expense"], "kind": "money"}]}


def render_weekly_profit(db, m: dict, ctx: dict, lines: list[str]) -> list[tuple[str, bytes]]:
    b = XlsxBook()
    t = m["totals"]
    cols = [("project_code", "项目编码", "text"), ("name", "项目名称", "text"), ("line_name", "业务线", "text"),
            ("status_name", "状态", "text"), ("customer_name", "客户", "text"),
            ("income", "累计收入", "money"), ("cost_expense", "累计报销费用", "money"),
            ("cost_project", "累计采购成本", "money"), ("cost_payable", "待支付应付", "money"),
            ("cost_lemon", "柠檬云辅助核算", "money"),
            ("cost", "累计成本", "money"), ("gross_profit", "累计毛利", "money"), ("gross_margin", "毛利率", "pct"),
            ("period_income", "本周收入", "money"), ("period_expense", "本周报销费用", "money"),
            ("period_project_cost", "本周采购成本", "money"), ("period_cost", "本周成本", "money"),
            ("period_gross_profit", "本周毛利", "money")]
    total = {"project_code": "合计", "income": t["income"], "cost": t["cost"], "gross_profit": t["gross_profit"],
             "gross_margin": t["gross_margin"], "period_income": t["period_income"], "period_cost": t["period_cost"],
             "cost_expense": sum((r["cost_expense"] for r in m["rows"]), ZERO),
             "cost_project": sum((r["cost_project"] for r in m["rows"]), ZERO),
             "cost_payable": sum((r["cost_payable"] for r in m["rows"]), ZERO),
             "cost_lemon": sum((r["cost_lemon"] for r in m["rows"]), ZERO),
             "period_expense": m["period_expense"],
             "period_project_cost": sum((r["period_project_cost"] for r in m["rows"]), ZERO),
             "period_gross_profit": t["period_income"] - t["period_cost"]}
    b.sheet("项目利润表", cols, m["rows"], total=total, alert=lambda r: r["low_margin"],
            note=f"标红：毛利率低于 {pct(m['alert_margin'])}。公共费用不摊入项目，见「公共费用」sheet。")
    b.kv_sheet("汇总", [("统计周", ctx["label"], "text"), ("项目数", m["project_count"], "int"),
                       ("累计收入", t["income"], "money"), ("累计成本", t["cost"], "money"),
                       ("其中：待支付应付", t.get("cost_payable", ZERO), "money"),
                       ("累计毛利", t["gross_profit"], "money"), ("毛利率", t["gross_margin"], "pct"),
                       ("本周收入", t["period_income"], "money"), ("本周成本", t["period_cost"], "money"),
                       ("本周归集报销费用", m["period_expense"], "money"),
                       ("管理费用-公共（本周，不摊入项目）", m["public_expense"], "money"),
                       ("低毛利项目数", m["low_margin_count"], "int")])
    b.sheet("公共费用", [("category", "费用类别", "text"), ("count", "笔数", "int"), ("amount", "金额", "money")],
            m["public_detail"], total={"category": "管理费用-公共 合计", "amount": m["public_expense"],
                                       "count": sum(r["count"] for r in m["public_detail"])})
    cal = m["caliber"]
    b.caliber_sheet("每周项目利润表", lines, {"统计周": ctx["label"], "收入口径": cal.get("income_basis"),
                                        "成本来源": "、".join(cal.get("cost_sources", [])),
                                        "生成时间": f"{now():%Y-%m-%d %H:%M}"})
    return [(f"每周项目利润表_{ctx['period'].replace(':', '_')}.xlsx", b.to_bytes())]


def snapshot_weekly_profit(db: Session, report: RptReport, m: dict) -> None:
    for r in m["rows"]:
        db.add(RptProjectProfit(week_no=m["week"], project_code=r["project_code"], income=r["income"], cost=r["cost"],
                                gross_profit=r["gross_profit"], gross_margin=r["gross_margin"], report_id=report.id))


# ================================================================ 6. 在建/运营项目
def _received_by_project(db: Session, end: date, start: date | None = None) -> dict[str, Decimal]:
    q = select(BizContract.project_code, func.sum(BizPayment.amount)).join(BizPayment.contract).where(
        BizPayment.payment_date <= end, BizContract.is_deleted.is_(False), BizContract.project_code.is_not(None))
    if start:
        q = q.where(BizPayment.payment_date >= start)
    return {k: _d(v) for k, v in db.execute(q.group_by(BizContract.project_code)).all()}


def collect_project_operation(db: Session, ctx: dict) -> dict:
    end, cut = ctx["end"], ctx["data_end"]
    q = select(BizProject).where(BizProject.is_deleted.is_(False), BizProject.status.in_([2, 3, 4]))
    if ctx["project_codes"]:
        q = q.where(BizProject.project_code.in_(ctx["project_codes"]))
    projects = db.scalars(q.order_by(BizProject.project_code)).all()
    codes = [p.project_code for p in projects]
    contract_amt = {k: _d(v) for k, v in db.execute(
        select(BizContract.project_code, func.sum(BizContract.amount)).where(
            BizContract.is_deleted.is_(False), BizContract.status != 3, BizContract.sign_date <= end,
            BizContract.project_code.in_(codes or [""])).group_by(BizContract.project_code)).all()}
    received = _received_by_project(db, end)
    received_week = _received_by_project(db, end, ctx["start"])
    pr = {r["project_code"]: r for r in profit_service.project_profit(db, ctx["start"], end, codes or None)["rows"]}
    group_anchor = {code: row["project_code"] for row in pr.values() for code in row.get("group_codes", [])}
    overdue = defaultdict(lambda: [ZERO, 0])
    for code, amt, rec in db.execute(
            select(BizContract.project_code, BizPaymentPlan.amount, BizPaymentPlan.received_amount)
            .join(BizPaymentPlan.contract).where(BizPaymentPlan.due_date < cut, BizContract.is_deleted.is_(False),
                                                 BizContract.status != 3, BizPaymentPlan.received_amount < BizPaymentPlan.amount)):
        overdue[code][0] += amt - rec
        overdue[code][1] += 1
    owners = _user_names(db, [p.owner_id for p in projects])
    rows, delays = [], []
    for p in projects:
        ms = sorted(p.milestones, key=lambda x: x.seq)
        cur = next((x for x in ms if x.status != 3), None)
        late = [x for x in ms if x.status == 4 or (x.status != 3 and x.plan_date and x.plan_date < cut)]
        for x in late:
            delays.append({"project_code": p.project_code, "project_name": p.name, "node": x.node_name,
                           "plan_date": x.plan_date, "delay_days": (cut - x.plan_date).days if x.plan_date else None,
                           "status": MILESTONE_STATUS.get(x.status, ""), "owner": x.owner.name if x.owner else "",
                           "note": x.progress_note or ""})
        ca = contract_amt.get(p.project_code, ZERO)
        rc = received.get(p.project_code, ZERO)
        prow = pr.get(p.project_code, {})
        combined_elsewhere = p.project_code in group_anchor and group_anchor[p.project_code] != p.project_code
        finance_note = (f"利润合并列于 {group_anchor[p.project_code]}，未向各合同分摊" if combined_elsewhere else prow.get("group_note", ""))
        rows.append({"project_code": p.project_code, "name": p.name,
                     "line_name": LINES.get(p.business_line, p.business_line), "customer_name": p.customer_name,
                     "status": p.status, "status_name": PROJECT_STATUS.get(p.status, ""), "is_key": p.is_key,
                     "owner": owners.get(p.owner_id, ""), "group": "在建" if p.status == 2 else "运营",
                     "contract_amount": ca, "received": rc, "received_week": received_week.get(p.project_code, ZERO),
                     "receive_rate": _ratio(rc, ca), "unreceived": ca - rc,
                     "income": None if combined_elsewhere else prow.get("income", ZERO),
                     "cost": None if combined_elsewhere else prow.get("cost", ZERO),
                     "gross_profit": None if combined_elsewhere else prow.get("gross_profit", ZERO), "gross_margin": prow.get("gross_margin"),
                     "period_cost": None if combined_elsewhere else prow.get("period_cost", ZERO), "finance_note": finance_note,
                     "current_node": cur.node_name if cur else "全部完成",
                     "current_plan": cur.plan_date if cur else None,
                     "progress": f"{sum(1 for x in ms if x.status == 3)}/{len(ms)}",
                     "delay_count": len(late), "delay_nodes": "、".join(x.node_name for x in late),
                     "overdue_amount": overdue[p.project_code][0] if p.project_code in overdue else ZERO})

    def agg(rs):
        ca, rc = sum((r["contract_amount"] for r in rs), ZERO), sum((r["received"] for r in rs), ZERO)
        gp, inc = sum((r["gross_profit"] or ZERO for r in rs), ZERO), sum((r["income"] or ZERO for r in rs), ZERO)
        return {"count": len(rs), "contract_amount": ca, "received": rc, "receive_rate": _ratio(rc, ca),
                "cost": sum((r["cost"] or ZERO for r in rs), ZERO), "gross_profit": gp, "gross_margin": _ratio(gp, inc),
                "received_week": sum((r["received_week"] for r in rs), ZERO),
                "delay_count": sum(r["delay_count"] for r in rs),
                "overdue_amount": sum((r["overdue_amount"] for r in rs), ZERO)}

    building = [r for r in rows if r["group"] == "在建"]
    running = [r for r in rows if r["group"] == "运营"]
    a_all = agg(rows)
    return {"week": ctx["period"].split(":")[0], "rows": rows, "delays": delays, "building": agg(building),
            "running": agg(running), "totals": a_all,
            "summary": [{"label": "在建项目", "value": len(building), "kind": "int"},
                        {"label": "运营项目", "value": len(running), "kind": "int"},
                        {"label": "合同额", "value": a_all["contract_amount"], "kind": "money"},
                        {"label": "累计回款", "value": a_all["received"], "kind": "money"},
                        {"label": "回款率", "value": a_all["receive_rate"], "kind": "pct"},
                        {"label": "延期节点", "value": len(delays), "kind": "int"}]}


def render_project_operation(db, m: dict, ctx: dict, lines: list[str]) -> list[tuple[str, bytes]]:
    b = XlsxBook()
    cols = [("project_code", "项目编码", "text"), ("name", "项目名称", "text"), ("line_name", "业务线", "text"),
            ("customer_name", "客户", "text"), ("owner", "项目经理", "text"), ("status_name", "状态", "text"),
            ("contract_amount", "合同额", "money"), ("received", "累计回款", "money"),
            ("receive_rate", "回款率", "pct"), ("unreceived", "未回款", "money"),
            ("received_week", "本周回款", "money"), ("overdue_amount", "逾期未回款", "money"),
            ("cost", "累计成本", "money"), ("gross_profit", "毛利", "money"), ("gross_margin", "毛利率", "pct"),
            ("finance_note", "利润核算说明", "text"),
            ("current_node", "当前节点", "text"), ("current_plan", "节点计划日", "date"),
            ("progress", "节点进度", "text"), ("delay_count", "延期节点数", "int"), ("delay_nodes", "延期节点", "text")]
    for title, key in (("在建项目", "在建"), ("运营项目", "运营")):
        rs = [r for r in m["rows"] if r["group"] == key]
        a = m["building" if key == "在建" else "running"]
        b.sheet(title, cols, rs, total={"project_code": "合计", **{k: a[k] for k in (
            "contract_amount", "received", "receive_rate", "cost", "gross_profit", "gross_margin", "received_week",
            "delay_count", "overdue_amount")}}, alert=lambda r: r["delay_count"] > 0)
    b.sheet("延期节点明细", [("project_code", "项目编码", "text"), ("project_name", "项目", "text"),
                         ("node", "节点", "text"), ("plan_date", "计划日", "date"), ("delay_days", "延期天数", "int"),
                         ("status", "状态", "text"), ("owner", "负责人", "text"), ("note", "进展说明", "text")],
            m["delays"])
    b.caliber_sheet("在建/运营项目报表", lines, {"统计周": ctx["label"], "生成时间": f"{now():%Y-%m-%d %H:%M}"})
    return [(f"在建运营项目报表_{ctx['period'].replace(':', '_')}.xlsx", b.to_bytes())]


# ================================================================ 公司周报 / 月报 公共取数
def signed_contracts(db: Session, start: date, end: date) -> list[dict]:
    from app.services import business_workbook, workbook_metrics
    wb = business_workbook.configured_source(db)
    if wb is not None:
        rows = workbook_metrics.between(business_workbook.performance_rows(wb), start, end)
        return [{"contract_no": f"{r['sheet_name']} · 第 {r['row']} 行", "name": r["name"],
                 "customer_name": r.get("customer") or "—", "amount": _d(r.get("amount")),
                 "sign_date": date.fromisoformat(r["date_value"]), "project_code": None,
                 "line_name": r.get("region") or "未注明"} for r in rows]
    cs = db.scalars(select(BizContract).where(BizContract.is_deleted.is_(False), BizContract.status != 3,
                                              BizContract.sign_date >= start, BizContract.sign_date <= end)
                    .order_by(BizContract.sign_date, BizContract.id)).all()
    return [{"contract_no": c.contract_no, "name": c.name, "customer_name": c.customer_name, "amount": c.amount,
             "sign_date": c.sign_date, "project_code": c.project_code,
             "line_name": LINES.get(c.business_line, c.business_line)} for c in cs]


def payments_between(db: Session, start: date, end: date) -> list[dict]:
    from app.services import business_workbook, workbook_metrics
    wb = business_workbook.configured_source(db)
    if wb is not None:
        periods = workbook_metrics.collection_periods(wb)
        out = []
        cur = date(start.year, start.month, 1)
        while cur <= end:
            per = f"{cur:%Y-%m}"
            d = periods.get(per)
            if d and d["available"]:
                for r in d["rows"]:
                    dt = date.fromisoformat(r["date_value"]) if r.get("date_value") else cur
                    if start <= dt <= end:
                        out.append({"payment_date": dt, "contract_no": f"{r['sheet_name']} · 第 {r['row']} 行",
                                    "customer_name": r.get("customer") or "—", "project_code": None,
                                    "business_line": r.get("region") or "", "line_name": r.get("region") or "未注明",
                                    "amount": _d(r.get("received"))})
            cur = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
        return out
    rows = db.execute(select(BizPayment, BizContract).join(BizPayment.contract).where(
        BizPayment.payment_date >= start, BizPayment.payment_date <= end, BizContract.is_deleted.is_(False))
        .order_by(BizPayment.payment_date, BizPayment.id)).all()
    return [{"payment_date": p.payment_date, "contract_no": c.contract_no, "customer_name": c.customer_name,
             "project_code": c.project_code, "business_line": c.business_line,
             "line_name": LINES.get(c.business_line, c.business_line), "amount": p.amount} for p, c in rows]


def payment_target(db: Session, month: str) -> Decimal:
    v = dict_service.get(db, "payment_target", month) or {}
    return sum((_d(x) for x in v.values()), ZERO) if isinstance(v, dict) else _d(v)


def opportunities_view(db: Session, start: date, end: date) -> dict:
    t0, t1 = _dt_bounds(start, end)
    new = db.scalars(select(BizOpportunity).where(BizOpportunity.is_deleted.is_(False),
                                                  BizOpportunity.created_at >= t0, BizOpportunity.created_at < t1)
                     .order_by(BizOpportunity.created_at)).all()
    funnel = db.execute(select(BizOpportunity.stage, func.count(), func.sum(BizOpportunity.amount)).where(
        BizOpportunity.is_deleted.is_(False), BizOpportunity.created_at < t1).group_by(BizOpportunity.stage)).all()
    fmap = {s: (n, _d(a)) for s, n, a in funnel}
    return {"new": [{"opp_no": o.opp_no, "name": o.name, "customer_name": o.customer_name, "amount": o.amount,
                     "stage": OPP_STAGES.get(o.stage, o.stage), "line_name": LINES.get(o.business_line, o.business_line),
                     "owner": o.owner.name if o.owner else ""} for o in new],
            "new_count": len(new), "new_amount": sum((o.amount for o in new), ZERO),
            "funnel": [{"stage": OPP_STAGES[s], "count": fmap.get(s, (0, ZERO))[0], "amount": fmap.get(s, (0, ZERO))[1]}
                       for s in OPP_STAGES],
            "open_amount": sum((fmap.get(s, (0, ZERO))[1] for s in ("lead", "contact", "quote", "negotiation")), ZERO)}


def submission_rate(db: Session, report_type: str, period: str) -> dict:
    """应提交名单由 sys_dict report_rule.submit 决定；已提交以 oa_submission 为准。按部门统计。"""
    rule = (dict_service.get(db, "report_rule", "submit") or {}).get(report_type, {})
    users = db.scalars(select(SysUser).where(SysUser.status == 1, SysUser.is_deleted.is_(False))).all()
    inc, exd, exr = set(rule.get("include_depts") or []), set(rule.get("exclude_depts") or []), set(rule.get("exclude_roles") or [])
    must = []
    for u in users:
        roles = {r.role_code for r in u.roles} or {"EMP"}
        if (inc and u.dept_code not in inc) or u.dept_code in exd or roles <= exr:
            continue
        must.append(u)
    sub = {uid: st for uid, st in db.execute(select(OaSubmission.user_id, OaSubmission.stat_status).where(
        OaSubmission.report_type == report_type, OaSubmission.period == period, OaSubmission.submit_time.is_not(None)))}
    depts = _dept_names(db)
    by: dict[str, dict] = {}
    for u in must:
        r = by.setdefault(u.dept_code or "-", {"dept": depts.get(u.dept_code or "", u.dept_code), "must": 0,
                                               "submitted": 0, "late": 0, "missing": []})
        r["must"] += 1
        if u.id in sub:
            r["submitted"] += 1
            r["late"] += 1 if sub[u.id] == 2 else 0
        else:
            r["missing"].append(u.name)
    rows = []
    for r in by.values():
        rows.append({**r, "rate": _ratio(r["submitted"], r["must"]), "missing": "、".join(r["missing"])})
    m, s = sum(r["must"] for r in rows), sum(r["submitted"] for r in rows)
    return {"period": period, "rows": sorted(rows, key=lambda r: r["rate"] or 0), "must": m, "submitted": s,
            "rate": _ratio(s, m), "late": sum(r["late"] for r in rows)}


def key_projects_progress(db: Session, start: date, end: date, codes=None) -> list[dict]:
    query = select(BizProject).where(BizProject.is_deleted.is_(False), BizProject.is_key.is_(True), BizProject.status.in_([1, 2, 3, 4]))
    if codes is not None:
        query = query.where(BizProject.project_code.in_(codes))
    ps = db.scalars(query.order_by(BizProject.project_code)).all()
    fb = defaultdict(list)
    for f in db.scalars(select(BizProjectFeedback).where(BizProjectFeedback.feedback_date >= start,
                                                         BizProjectFeedback.feedback_date <= end)
                        .order_by(BizProjectFeedback.feedback_date)):
        fb[f.project_id].append(f)
    out = []
    for p in ps:
        ms = sorted(p.milestones, key=lambda x: x.seq)
        cur = next((x for x in ms if x.status != 3), None)
        late = [x for x in ms if x.status == 4]
        notes = fb.get(p.id, [])
        out.append({"project_code": p.project_code, "name": p.name, "status_name": PROJECT_STATUS.get(p.status, ""),
                    "current_node": cur.node_name if cur else ("全部完成" if ms else "—"),
                    "delay_nodes": "、".join(x.node_name for x in late),
                    "risk": max([f.risk_level for f in notes], default=0),
                    "feedback": "；".join(f"{f.feedback_date:%m-%d} {f.content}" for f in notes[-3:]) or
                                (cur.progress_note if cur and cur.progress_note else "本周无进展反馈")})
    return out


def delayed_milestones(db: Session, codes=None) -> list[dict]:
    query = select(BizProjectMilestone, BizProject).join(BizProject).where(BizProject.is_deleted.is_(False), BizProjectMilestone.status == 4)
    if codes is not None:
        query = query.where(BizProject.project_code.in_(codes))
    rows = db.execute(query.order_by(BizProjectMilestone.plan_date)).all()
    T = today()
    return [{"project_code": p.project_code, "project_name": p.name, "node": m.node_name, "plan_date": m.plan_date,
             "delay_days": (T - m.plan_date).days if m.plan_date else None,
             "owner": m.owner.name if m.owner else "", "note": m.progress_note or ""} for m, p in rows]


# ================================================================ 4. 公司周报
def collect_company_weekly(db: Session, ctx: dict) -> dict:
    s, e = ctx["start"], ctx["end"]
    week = ctx["period"].split(":")[0]
    contracts = signed_contracts(db, s, e)
    pays = payments_between(db, s, e)
    month = f"{e:%Y-%m}"
    ms, _ = month_range(month)
    month_pay = sum((p["amount"] for p in payments_between(db, ms, e)), ZERO)
    target = payment_target(db, month)
    opps = opportunities_view(db, s, e)
    exp = expense_core(db, s, e)
    sub = submission_rate(db, "weekly", week)
    key = key_projects_progress(db, s, e, ctx["project_codes"])
    delays = delayed_milestones(db, ctx["project_codes"])
    prev_s, prev_e = s - timedelta(days=7), e - timedelta(days=7)
    prev_sign = sum((c["amount"] for c in signed_contracts(db, prev_s, prev_e)), ZERO)
    prev_pay = sum((p["amount"] for p in payments_between(db, prev_s, prev_e)), ZERO)
    sign_total = sum((c["amount"] for c in contracts), ZERO)
    pay_total = sum((p["amount"] for p in pays), ZERO)
    return {
        "week": week, "start": s, "end": e,
        "sign": {"count": len(contracts), "amount": sign_total, "prev_amount": prev_sign, "items": contracts},
        "payment": {"count": len(pays), "amount": pay_total, "prev_amount": prev_pay, "items": pays,
                    "month": month, "month_amount": month_pay, "month_target": target,
                    "month_rate": _ratio(month_pay, target)},
        "opportunity": opps, "expense": {k: exp[k] for k in ("total", "expense_total", "flow_total", "by_category",
                                                             "by_dept", "expense_count", "flow_count")},
        "submission": sub, "key_projects": key, "delays": delays,
        "summary": [{"label": "本周签约", "value": sign_total, "kind": "money"},
                    {"label": "本周回款", "value": pay_total, "kind": "money"},
                    {"label": "当月回款达成", "value": _ratio(month_pay, target), "kind": "pct"},
                    {"label": "新增商机", "value": opps["new_count"], "kind": "int"},
                    {"label": "周报提交率", "value": sub["rate"], "kind": "pct"},
                    {"label": "本周支出", "value": exp["total"], "kind": "money"}],
    }


def _chg(cur, prev) -> str:
    if not prev:
        return "上期无数据"
    r = (Decimal(cur) - Decimal(prev)) / Decimal(prev)
    return f"较上期{'增长' if r >= 0 else '下降'} {abs(r) * 100:.1f}%"


def render_company_weekly(db, m: dict, ctx: dict, lines: list[str]) -> list[tuple[str, bytes]]:
    sg, py, op, ex, sb = m["sign"], m["payment"], m["opportunity"], m["expense"], m["submission"]
    doc = DocxDoc(f"聚成数智 公司周报 {m['week']}", f"统计周期 {m['start']:%Y-%m-%d} 至 {m['end']:%Y-%m-%d} · "
                                                   f"系统自动生成初稿（{now():%Y-%m-%d %H:%M}），请总经办润色后发布")
    doc.h1("一、本周概览")
    doc.bullet(f"签约：{sg['count']} 份，金额 {yuan(sg['amount'])}，{_chg(sg['amount'], sg['prev_amount'])}。")
    doc.bullet(f"回款：{py['count']} 笔，金额 {yuan(py['amount'])}，{_chg(py['amount'], py['prev_amount'])}；"
               f"{py['month']} 月累计回款 {wan(py['month_amount'])}，目标 {wan(py['month_target'])}，达成 {pct(py['month_rate'])}。")
    doc.bullet(f"商机：新增 {op['new_count']} 个，预计金额 {wan(op['new_amount'])}；在途商机金额 {wan(op['open_amount'])}。")
    doc.bullet(f"重点项目 {len(m['key_projects'])} 个；当前延期节点 {len(m['delays'])} 个。")
    doc.bullet(f"周报提交率 {pct(sb['rate'])}（应交 {sb['must']} 人，已交 {sb['submitted']} 人，迟交 {sb['late']} 人）。")
    doc.bullet(f"本周支出 {yuan(ex['total'])}（报销 {yuan(ex['expense_total'])}，已核对流水 {yuan(ex['flow_total'])}）。")
    doc.h1("二、签约情况")
    if sg["items"]:
        doc.table(["合同编号", "合同名称", "客户", "业务线", "签订日", "金额"],
                  [[c["contract_no"], c["name"], c["customer_name"], c["line_name"], c["sign_date"], yuan(c["amount"])]
                   for c in sg["items"]])
    else:
        doc.p("本周无新签合同。")
    doc.h1("三、回款情况")
    if py["items"]:
        doc.table(["到账日", "合同编号", "客户", "业务线", "金额"],
                  [[p["payment_date"], p["contract_no"], p["customer_name"], p["line_name"], yuan(p["amount"])]
                   for p in py["items"]])
    else:
        doc.p("本周无回款到账。")
    doc.h1("四、商机进展")
    doc.table(["阶段", "商机数", "金额"], [[f["stage"], f["count"], wan(f["amount"])] for f in op["funnel"]])
    if op["new"]:
        doc.h2("本周新增商机")
        doc.table(["编号", "商机", "客户", "阶段", "金额", "负责人"],
                  [[o["opp_no"], o["name"], o["customer_name"], o["stage"], wan(o["amount"]), o["owner"]] for o in op["new"]])
    doc.h1("五、重点项目进展")
    if m["key_projects"]:
        doc.table(["项目", "状态", "当前节点", "延期节点", "本周进展"],
                  [[f"{k['project_code']} {k['name']}", k["status_name"], k["current_node"], k["delay_nodes"] or "—",
                    k["feedback"]] for k in m["key_projects"]])
    doc.h1("六、节点延期")
    if m["delays"]:
        doc.table(["项目", "节点", "计划日", "延期天数", "负责人", "说明"],
                  [[d["project_name"], d["node"], d["plan_date"], d["delay_days"], d["owner"], d["note"]] for d in m["delays"]])
    else:
        doc.p("当前无延期节点。")
    doc.h1("七、周报提交率")
    doc.table(["部门", "应交", "已交", "迟交", "提交率", "未交人员"],
              [[r["dept"], r["must"], r["submitted"], r["late"], pct(r["rate"]), r["missing"] or "—"] for r in sb["rows"]])
    doc.h1("八、本周支出")
    doc.table(["类别", "金额"], [[c["category"], yuan(c["total"])] for c in ex["by_category"]] +
              [["合计", yuan(ex["total"])]])
    doc.h1("附：口径说明")
    for line in lines:
        doc.p(line, muted=True)

    b = XlsxBook()
    b.kv_sheet("概览", [("统计周", ctx["label"], "text"), ("本周签约额", sg["amount"], "money"),
                       ("签约份数", sg["count"], "int"), ("本周回款", py["amount"], "money"),
                       ("当月累计回款", py["month_amount"], "money"), ("当月回款目标", py["month_target"], "money"),
                       ("当月回款达成率", py["month_rate"], "pct"), ("新增商机数", op["new_count"], "int"),
                       ("新增商机金额", op["new_amount"], "money"), ("在途商机金额", op["open_amount"], "money"),
                       ("周报提交率", sb["rate"], "pct"), ("本周支出", ex["total"], "money"),
                       ("延期节点数", len(m["delays"]), "int")])
    b.sheet("签约", [("contract_no", "合同编号", "text"), ("name", "合同名称", "text"), ("customer_name", "客户", "text"),
                   ("line_name", "业务线", "text"), ("sign_date", "签订日", "date"), ("amount", "金额", "money")],
            sg["items"], total={"contract_no": "合计", "amount": sg["amount"]})
    b.sheet("回款", [("payment_date", "到账日", "date"), ("contract_no", "合同编号", "text"),
                   ("customer_name", "客户", "text"), ("line_name", "业务线", "text"), ("amount", "金额", "money")],
            py["items"], total={"payment_date": "合计", "amount": py["amount"]})
    b.sheet("商机漏斗", [("stage", "阶段", "text"), ("count", "商机数", "int"), ("amount", "金额", "money")], op["funnel"])
    b.sheet("重点项目", [("project_code", "项目编码", "text"), ("name", "项目", "text"), ("status_name", "状态", "text"),
                     ("current_node", "当前节点", "text"), ("delay_nodes", "延期节点", "text"),
                     ("feedback", "本周进展", "text")], m["key_projects"], alert=lambda r: bool(r["delay_nodes"]))
    b.sheet("节点延期", [("project_code", "项目编码", "text"), ("project_name", "项目", "text"), ("node", "节点", "text"),
                     ("plan_date", "计划日", "date"), ("delay_days", "延期天数", "int"), ("owner", "负责人", "text"),
                     ("note", "说明", "text")], m["delays"])
    b.sheet("周报提交率", [("dept", "部门", "text"), ("must", "应交", "int"), ("submitted", "已交", "int"),
                      ("late", "迟交", "int"), ("rate", "提交率", "pct"), ("missing", "未交人员", "text")], sb["rows"],
            total={"dept": "合计", "must": sb["must"], "submitted": sb["submitted"], "late": sb["late"], "rate": sb["rate"]})
    b.sheet("本周支出", [("category", "类别", "text"), ("total", "金额", "money")], ex["by_category"],
            total={"category": "合计", "total": ex["total"]})
    b.caliber_sheet("公司周报", lines, {"统计周": ctx["label"], "生成时间": f"{now():%Y-%m-%d %H:%M}"})
    return [(f"公司周报_{m['week']}.docx", doc.to_bytes()), (f"公司周报数据附表_{m['week']}.xlsx", b.to_bytes())]


# ================================================================ 5. 公司月报
SUBJECT_CLASS = {"1": "资产类", "2": "负债类", "3": "共同类", "4": "所有者权益类", "5": "成本类", "6": "损益类"}
DEFAULT_SUBJECT_MAP = {"income": ["6001", "6051"], "cost": ["6401", "6402"], "tax_surcharge": ["6403"],
                       "expense": ["6601", "6602", "6603"]}
MISSING = "数据缺失"


def subject_map(db: Session) -> dict:
    v = dict_service.get(db, "report_rule", "subject_map")
    return {**DEFAULT_SUBJECT_MAP, **v} if isinstance(v, dict) else DEFAULT_SUBJECT_MAP


def _match(code: str, prefixes: list[str]) -> bool:
    return any(code.startswith(p) for p in prefixes)


def month_core(db: Session, period: str) -> dict:
    s, e = month_range(period)
    smap = subject_map(db)
    # --- 柠檬云凭证
    vs = db.scalars(select(FinVoucher).where(FinVoucher.voucher_date >= s, FinVoucher.voucher_date <= e)).all()
    lemon = {"available": bool(vs), "voucher_count": len({(v.voucher_no, v.voucher_date) for v in vs}),
             "line_count": len(vs), "by_class": [], "by_subject": [], "batches": sorted({v.sync_batch for v in vs})}
    if vs:
        cls, sub = {}, {}
        for v in vs:
            k = v.subject_code[:1]
            c = cls.setdefault(k, {"class": SUBJECT_CLASS.get(k, f"其他（{k}）"), "debit": ZERO, "credit": ZERO, "lines": 0})
            c["debit" if v.direction == 1 else "credit"] += v.amount
            c["lines"] += 1
            t = sub.setdefault(v.subject_code, {"subject_code": v.subject_code, "subject_name": v.subject_name,
                                                "class": SUBJECT_CLASS.get(k, k), "debit": ZERO, "credit": ZERO})
            t["debit" if v.direction == 1 else "credit"] += v.amount
        lemon["by_class"] = [cls[k] for k in sorted(cls)]
        lemon["by_subject"] = [sub[k] for k in sorted(sub)]
        lemon["debit_total"] = sum((c["debit"] for c in cls.values()), ZERO)
        lemon["credit_total"] = sum((c["credit"] for c in cls.values()), ZERO)
        ok_v = [v for v in vs if "结转" not in (v.summary or "")]

        def tot(key, direction):
            return sum((v.amount for v in ok_v if v.direction == direction and _match(v.subject_code, smap[key])), ZERO)
        inc, cost = tot("income", 2) - tot("income", 1), tot("cost", 1) - tot("cost", 2)
        exp, taxs = tot("expense", 1) - tot("expense", 2), tot("tax_surcharge", 1) - tot("tax_surcharge", 2)
        lemon["analysis"] = {"income": inc, "cost": cost, "expense": exp, "tax_surcharge": taxs,
                             "gross_profit": inc - cost, "gross_margin": _ratio(inc - cost, inc),
                             "operating_profit": inc - cost - exp - taxs}
    # --- 科目余额
    bal = db.scalars(select(FinSubjectBalance).where(FinSubjectBalance.period == period)
                     .order_by(FinSubjectBalance.subject_code)).all()
    balance = {"available": bool(bal), "rows": [{"subject_code": b.subject_code, "subject_name": b.subject_name,
                                                 "debit_amount": b.debit_amount, "credit_amount": b.credit_amount,
                                                 "debit_balance": b.debit_balance, "credit_balance": b.credit_balance}
                                                for b in bal]}
    # --- 银行流水
    fl = db.execute(select(FinBankFlow.bank_account, FinBankFlow.direction, FinBankFlow.match_status, func.count(),
                           func.sum(FinBankFlow.amount)).where(FinBankFlow.trade_date >= s, FinBankFlow.trade_date <= e)
                    .group_by(FinBankFlow.bank_account, FinBankFlow.direction, FinBankFlow.match_status)).all()
    acc, st = {}, {}
    for a, d, ms, n, amt in fl:
        r = acc.setdefault(a, {"account": a, "income": ZERO, "outflow": ZERO, "count": 0})
        r["income" if d == 1 else "outflow"] += _d(amt)
        r["count"] += n
        k = (FLOW_MATCH_STATUS.get(ms, ""), "收入" if d == 1 else "支出")
        x = st.setdefault(k, {"status": k[0], "direction": k[1], "count": 0, "amount": ZERO})
        x["count"] += n
        x["amount"] += _d(amt)
    bank = {"accounts": list(acc.values()), "by_status": list(st.values()),
            "income": sum((r["income"] for r in acc.values()), ZERO),
            "outflow": sum((r["outflow"] for r in acc.values()), ZERO)}
    bank["net"] = bank["income"] - bank["outflow"]
    # --- 税务申报
    tx = db.scalars(select(SyncTaxFiling).where(SyncTaxFiling.period == period).order_by(SyncTaxFiling.tax_type)).all()
    tax = {"available": bool(tx), "rows": [{"tax_type": t.tax_type, "taxable_amount": t.taxable_amount,
                                            "payable_amount": t.payable_amount, "paid_amount": t.paid_amount,
                                            "filing_date": t.filing_date} for t in tx],
           "payable_total": sum((t.payable_amount for t in tx), ZERO),
           "paid_total": sum((t.paid_amount or ZERO for t in tx), ZERO)}
    # --- 经营：回款 / 签约 / 商机 / 提交率 / 业务口径财务分析
    pays = payments_between(db, s, e)
    target = payment_target(db, period)
    by_line = defaultdict(lambda: ZERO)
    for p in pays:
        by_line[p["line_name"]] += p["amount"]
    contracts = signed_contracts(db, s, e)
    pr = profit_service.project_profit(db, s, e)
    t = pr["totals"]
    biz_gp = t["period_income"] - t["period_cost"]
    exp = expense_core(db, s, e)
    return {
        "period": period, "start": s, "end": e, "lemon": lemon, "balance": balance, "bank": bank, "tax": tax,
        "payment": {"amount": sum((p["amount"] for p in pays), ZERO), "count": len(pays), "target": target,
                    "rate": _ratio(sum((p["amount"] for p in pays), ZERO), target),
                    "by_line": [{"line_name": k, "amount": v} for k, v in by_line.items()], "items": pays},
        "sign": {"amount": sum((c["amount"] for c in contracts), ZERO), "count": len(contracts), "items": contracts},
        "opportunity": opportunities_view(db, s, e),
        "submission": submission_rate(db, "monthly", period),
        "expense": {k: exp[k] for k in ("total", "expense_total", "flow_total", "by_category", "by_dept")},
        "biz_analysis": {"income": t["period_income"], "project_cost": t["period_cost"],
                         "public_expense": pr["public_expense"], "gross_profit": biz_gp,
                         "gross_margin": _ratio(biz_gp, t["period_income"]),
                         "net_after_public": biz_gp - pr["public_expense"]},
    }


def _mom(cur, prev):
    if cur is None or prev is None or not prev:
        return None
    return ((Decimal(cur) - Decimal(prev)) / abs(Decimal(prev))).quantize(Decimal("0.0001"))


def collect_company_monthly(db: Session, ctx: dict) -> dict:
    period = ctx["period"]
    cur, prev = month_core(db, period), month_core(db, prev_month(period))
    la, pa = cur["lemon"].get("analysis"), prev["lemon"].get("analysis")
    pairs = [("回款", cur["payment"]["amount"], prev["payment"]["amount"]),
             ("签约额", cur["sign"]["amount"], prev["sign"]["amount"]),
             ("新增商机金额", cur["opportunity"]["new_amount"], prev["opportunity"]["new_amount"]),
             ("银行收入", cur["bank"]["income"], prev["bank"]["income"]),
             ("银行支出", cur["bank"]["outflow"], prev["bank"]["outflow"]),
             ("支出合计（报销+已核对流水）", cur["expense"]["total"], prev["expense"]["total"]),
             ("业务口径收入（签约）", cur["biz_analysis"]["income"], prev["biz_analysis"]["income"]),
             ("业务口径项目成本", cur["biz_analysis"]["project_cost"], prev["biz_analysis"]["project_cost"]),
             ("公共费用", cur["biz_analysis"]["public_expense"], prev["biz_analysis"]["public_expense"]),
             ("账务口径营业收入", la["income"] if la else None, pa["income"] if pa else None),
             ("账务口径营业成本", la["cost"] if la else None, pa["cost"] if pa else None),
             ("账务口径期间费用", la["expense"] if la else None, pa["expense"] if pa else None),
             ("应纳税额合计", cur["tax"]["payable_total"] if cur["tax"]["available"] else None,
              prev["tax"]["payable_total"] if prev["tax"]["available"] else None),
             ("月报提交率", cur["submission"]["rate"], prev["submission"]["rate"])]
    mom = [{"metric": k, "current": c, "previous": p, "mom": _mom(c, p)} for k, c, p in pairs]
    missing = [n for n, ok_ in (("柠檬云凭证", cur["lemon"]["available"]), ("科目余额", cur["balance"]["available"]),
                                ("税务申报", cur["tax"]["available"])) if not ok_]
    cur.update({"prev_period": prev["period"], "mom": mom, "missing": missing,
                "summary": [{"label": "当月回款", "value": cur["payment"]["amount"], "kind": "money"},
                            {"label": "回款达成", "value": cur["payment"]["rate"], "kind": "pct"},
                            {"label": "签约额", "value": cur["sign"]["amount"], "kind": "money"},
                            {"label": "营业收入(账务)", "value": la["income"] if la else MISSING,
                             "kind": "money" if la else "text"},
                            {"label": "毛利率(业务)", "value": cur["biz_analysis"]["gross_margin"], "kind": "pct"},
                            {"label": "月报提交率", "value": cur["submission"]["rate"], "kind": "pct"}]})
    return cur


def render_company_monthly(db, m: dict, ctx: dict, lines: list[str]) -> list[tuple[str, bytes]]:
    lm, bl, bk, tx, py, sg = m["lemon"], m["balance"], m["bank"], m["tax"], m["payment"], m["sign"]
    op, sb, ba, la = m["opportunity"], m["submission"], m["biz_analysis"], m["lemon"].get("analysis")
    period = m["period"]
    b = XlsxBook()
    ov = [("统计月份", ctx["label"], "text"), ("当月回款", py["amount"], "money"), ("回款目标", py["target"], "money"),
          ("回款达成率", py["rate"], "pct"), ("签约额", sg["amount"], "money"), ("签约份数", sg["count"], "int"),
          ("新增商机数", op["new_count"], "int"), ("新增商机金额", op["new_amount"], "money"),
          ("银行收入", bk["income"], "money"), ("银行支出", bk["outflow"], "money"), ("银行净流入", bk["net"], "money"),
          ("月报提交率", sb["rate"], "pct"),
          ("数据缺失项", "、".join(m["missing"]) or "无", "text")]
    b.kv_sheet("概览", ov)
    fa = [("【账务口径·柠檬云】", "" if la else MISSING, "text")]
    if la:
        fa += [("营业收入", la["income"], "money"), ("营业成本", la["cost"], "money"), ("毛利", la["gross_profit"], "money"),
               ("毛利率", la["gross_margin"], "pct"), ("期间费用", la["expense"], "money"),
               ("税金及附加", la["tax_surcharge"], "money"), ("营业利润（简化）", la["operating_profit"], "money")]
    fa += [("【业务口径·台账】", "", "text"), ("收入（当月签约额）", ba["income"], "money"),
           ("项目成本（归集报销+采购）", ba["project_cost"], "money"), ("项目毛利", ba["gross_profit"], "money"),
           ("毛利率", ba["gross_margin"], "pct"), ("公共费用（管理费用-公共）", ba["public_expense"], "money"),
           ("扣除公共费用后", ba["net_after_public"], "money")]
    b.kv_sheet("财务分析", fa)
    b.sheet("环比", [("metric", "指标", "text"), ("current", f"本月 {period}", "money"),
                   ("previous", f"上月 {m['prev_period']}", "money"), ("mom", "环比", "pct")],
            [{**r, "current": MISSING if r["current"] is None else (pct(r["current"]) if "率" in r["metric"] else r["current"]),
              "previous": MISSING if r["previous"] is None else (pct(r["previous"]) if "率" in r["metric"] else r["previous"])}
             for r in m["mom"]])
    if lm["available"]:
        b.sheet("凭证汇总-科目大类", [("class", "科目大类", "text"), ("lines", "分录数", "int"), ("debit", "借方发生", "money"),
                                 ("credit", "贷方发生", "money")], lm["by_class"],
                total={"class": "合计", "lines": lm["line_count"], "debit": lm["debit_total"], "credit": lm["credit_total"]},
                note=f"凭证 {lm['voucher_count']} 张，同步批次：{'、'.join(lm['batches'])}")
        b.sheet("凭证汇总-明细科目", [("subject_code", "科目编码", "text"), ("subject_name", "科目名称", "text"),
                                 ("class", "大类", "text"), ("debit", "借方发生", "money"), ("credit", "贷方发生", "money")],
                lm["by_subject"])
    else:
        b.sheet("凭证汇总-科目大类", [("class", "科目大类", "text"), ("note", "说明", "text")],
                [{"class": MISSING, "note": f"{period} 未同步到柠檬云凭证（lemoncloud_sync 未执行或无数据），本表不以其他数据替代。"}])
    if bl["available"]:
        b.sheet("科目余额", [("subject_code", "科目编码", "text"), ("subject_name", "科目名称", "text"),
                         ("debit_amount", "本期借方", "money"), ("credit_amount", "本期贷方", "money"),
                         ("debit_balance", "期末借方余额", "money"), ("credit_balance", "期末贷方余额", "money")], bl["rows"])
    else:
        b.sheet("科目余额", [("subject_code", "科目编码", "text"), ("note", "说明", "text")],
                [{"subject_code": MISSING, "note": f"{period} 科目余额未同步。"}])
    b.sheet("银行流水", [("account", "账户", "text"), ("count", "笔数", "int"), ("income", "收入", "money"),
                     ("outflow", "支出", "money")], bk["accounts"],
            total={"account": "合计", "count": sum(a["count"] for a in bk["accounts"]), "income": bk["income"],
                   "outflow": bk["outflow"]})
    b.sheet("流水核对分类", [("status", "核对状态", "text"), ("direction", "收支", "text"), ("count", "笔数", "int"),
                       ("amount", "金额", "money")], bk["by_status"])
    if tx["available"]:
        b.sheet("税务申报", [("tax_type", "税种", "text"), ("taxable_amount", "计税依据", "money"),
                         ("payable_amount", "应纳税额", "money"), ("paid_amount", "已缴税额", "money"),
                         ("filing_date", "申报日期", "date")], tx["rows"],
                total={"tax_type": "合计", "payable_amount": tx["payable_total"], "paid_amount": tx["paid_total"]})
    else:
        b.sheet("税务申报", [("tax_type", "税种", "text"), ("note", "说明", "text")],
                [{"tax_type": MISSING, "note": f"{period} 未上传税务申报数据。"}])
    b.sheet("回款", [("payment_date", "到账日", "date"), ("contract_no", "合同编号", "text"),
                   ("customer_name", "客户", "text"), ("line_name", "业务线", "text"), ("amount", "金额", "money")],
            py["items"], total={"payment_date": "合计", "amount": py["amount"]})
    b.sheet("签约", [("contract_no", "合同编号", "text"), ("name", "合同名称", "text"), ("customer_name", "客户", "text"),
                   ("line_name", "业务线", "text"), ("sign_date", "签订日", "date"), ("amount", "金额", "money")],
            sg["items"], total={"contract_no": "合计", "amount": sg["amount"]})
    b.sheet("商机漏斗", [("stage", "阶段", "text"), ("count", "商机数", "int"), ("amount", "金额", "money")], op["funnel"])
    b.sheet("月报提交率", [("dept", "部门", "text"), ("must", "应交", "int"), ("submitted", "已交", "int"),
                      ("rate", "提交率", "pct"), ("missing", "未交人员", "text")], sb["rows"],
            total={"dept": "合计", "must": sb["must"], "submitted": sb["submitted"], "rate": sb["rate"]})
    b.sheet("支出分类", [("category", "类别", "text"), ("total", "金额", "money")], m["expense"]["by_category"],
            total={"category": "合计", "total": m["expense"]["total"]})
    b.caliber_sheet("公司月报", lines, {"统计月份": ctx["label"], "生成时间": f"{now():%Y-%m-%d %H:%M}",
                                    "数据缺失项": "、".join(m["missing"]) or "无"})

    doc = DocxDoc(f"聚成数智 公司月报 {ctx['label']}",
                  f"统计周期 {m['start']:%Y-%m-%d} 至 {m['end']:%Y-%m-%d} · 系统自动生成（{now():%Y-%m-%d %H:%M}）")
    if m["missing"]:
        doc.p(f"提示：本月 {'、'.join(m['missing'])}{MISSING}，相关章节已注明，未以其他数据替代。")
    doc.h1("一、经营概况")
    doc.bullet(f"回款 {yuan(py['amount'])}，目标 {yuan(py['target'])}，达成 {pct(py['rate'])}。")
    doc.bullet(f"签约 {sg['count']} 份，金额 {yuan(sg['amount'])}；新增商机 {op['new_count']} 个，金额 {wan(op['new_amount'])}。")
    doc.bullet(f"银行收入 {yuan(bk['income'])}，支出 {yuan(bk['outflow'])}，净流入 {yuan(bk['net'])}。")
    doc.bullet(f"月报提交率 {pct(sb['rate'])}（应交 {sb['must']} 人，已交 {sb['submitted']} 人）。")
    doc.h1("二、财务分析")
    doc.h2("账务口径（柠檬云凭证）")
    if la:
        doc.table(["营业收入", "营业成本", "毛利", "毛利率", "期间费用", "营业利润（简化）"],
                  [[yuan(la["income"]), yuan(la["cost"]), yuan(la["gross_profit"]), pct(la["gross_margin"]),
                    yuan(la["expense"]), yuan(la["operating_profit"])]])
    else:
        doc.p(f"柠檬云凭证{MISSING}。")
    doc.h2("业务口径（台账）")
    doc.table(["收入（签约）", "项目成本", "项目毛利", "毛利率", "公共费用", "扣除公共费用后"],
              [[yuan(ba["income"]), yuan(ba["project_cost"]), yuan(ba["gross_profit"]), pct(ba["gross_margin"]),
                yuan(ba["public_expense"]), yuan(ba["net_after_public"])]])
    doc.h2("环比")
    doc.table(["指标", "本月", "上月", "环比"],
              [[r["metric"], "—" if r["current"] is None else (pct(r["current"]) if "率" in r["metric"] else yuan(r["current"])),
                MISSING if r["previous"] is None else (pct(r["previous"]) if "率" in r["metric"] else yuan(r["previous"])),
                pct(r["mom"])] for r in m["mom"]])
    doc.h1("三、柠檬云凭证汇总")
    if lm["available"]:
        doc.table(["科目大类", "分录数", "借方发生", "贷方发生"],
                  [[c["class"], c["lines"], yuan(c["debit"]), yuan(c["credit"])] for c in lm["by_class"]])
    else:
        doc.p(f"{MISSING}：{period} 未同步到柠檬云凭证。")
    doc.h1("四、银行流水")
    doc.table(["账户", "笔数", "收入", "支出"], [[a["account"], a["count"], yuan(a["income"]), yuan(a["outflow"])]
                                           for a in bk["accounts"]])
    doc.h1("五、税务申报")
    if tx["available"]:
        doc.table(["税种", "计税依据", "应纳税额", "已缴税额", "申报日期"],
                  [[t["tax_type"], yuan(t["taxable_amount"]), yuan(t["payable_amount"]), yuan(t["paid_amount"]),
                    t["filing_date"] or "—"] for t in tx["rows"]])
    else:
        doc.p(f"{MISSING}：{period} 未上传税务申报数据。")
    doc.h1("六、月报提交率")
    doc.table(["部门", "应交", "已交", "提交率", "未交人员"],
              [[r["dept"], r["must"], r["submitted"], pct(r["rate"]), r["missing"] or "—"] for r in sb["rows"]])
    doc.h1("附：口径说明")
    for line in lines:
        doc.p(line, muted=True)
    return [(f"公司月报_{period}.xlsx", b.to_bytes()), (f"公司月报_{period}.docx", doc.to_bytes())]


# ================================================================ 引擎
ENGINES = {
    "daily_expense": (collect_daily_expense, render_daily_expense, snapshot_daily_expense),
    "daily_bankflow": (collect_daily_bankflow, render_daily_bankflow, None),
    "weekly_profit": (collect_weekly_profit, render_weekly_profit, snapshot_weekly_profit),
    "company_weekly": (collect_company_weekly, render_company_weekly, None),
    "company_monthly": (collect_company_monthly, render_company_monthly, None),
    "project_operation": (collect_project_operation, render_project_operation, None),
}


def _keep_precision(v):
    """encode() 统一把 Decimal 格式化为 2 位小数；比率（4 位小数）需保留精度，先转字符串。"""
    if isinstance(v, Decimal):
        return str(v) if v.as_tuple().exponent < -2 else v
    if isinstance(v, dict):
        return {k: _keep_precision(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_keep_precision(x) for x in v]
    return v


def store_metrics(metrics: dict) -> dict:
    return encode(_keep_precision(metrics))


def report_title(report_type: str, ctx: dict) -> str:
    return f"{REPORT_TYPES[report_type]['name']} {ctx['label']}"


def generate(db: Session, report_type: str, period: str | None = None, trigger: str = "manual",
             user_id: int | None = None, project_codes: list[str] | None = None) -> RptReport:
    """生成一份报表（取数 → 渲染 → 归档 → 快照）。同 type+period 再生成 version+1。只 flush 不 commit。"""
    t0 = time.perf_counter()
    ctx = normalize_period(report_type, period, project_codes)
    collect, render, snap = ENGINES[report_type]
    lines = caliber_lines(db, report_type)
    metrics = collect(db, ctx)
    files = render(db, metrics, ctx, lines)
    version = (db.scalar(select(func.max(RptReport.version)).where(
        RptReport.report_type == report_type, RptReport.period == ctx["period"])
                        .execution_options(include_removed_records=True)) or 0) + 1
    month_dir = f"{ctx['start']:%Y-%m}"
    file_ids = []
    for filename, data in files:
        f = save_bytes(db, data=data, filename=filename, category="report", uploader_id=user_id,
                       key=f"report/{report_type}/{month_dir}/{filename}")
        file_ids.append(f.id)
    stored = store_metrics(metrics)
    stored["_meta"] = encode({"period_label": ctx["label"], "start": ctx["start"], "end": ctx["end"],
                              "project_codes": ctx["project_codes"], "caliber": lines,
                              "generated_at": now()})
    rpt = RptReport(report_type=report_type, period=ctx["period"], title=report_title(report_type, ctx),
                    version=version, metrics=stored, file_ids=file_ids, trigger=trigger, created_by=user_id)
    db.add(rpt)
    db.flush()
    if snap:
        snap(db, rpt, metrics)
    rpt.cost_ms = int((time.perf_counter() - t0) * 1000)
    db.flush()
    return rpt


def regenerate_plan() -> list[tuple[str, str]]:
    """一键更新范围：当日（昨日 + 今日截至当前）/ 本周（上周 + 本周至今）/ 本月（上月 + 本月至今）。"""
    T = today()
    y = (T - timedelta(days=1)).isoformat()
    tw, lw = week_no(T), last_week(T)
    tm, lm = f"{T:%Y-%m}", prev_month(f"{T:%Y-%m}")
    return [("daily_expense", y), ("daily_expense", T.isoformat()), ("daily_bankflow", y),
            ("daily_bankflow", T.isoformat()), ("weekly_profit", lw), ("weekly_profit", tw),
            ("project_operation", lw), ("project_operation", tw), ("company_weekly", lw), ("company_weekly", tw),
            ("company_monthly", lm), ("company_monthly", tm)]


def regenerate_all(db: Session, user_id: int | None = None, trigger: str = "manual",
                   types: list[str] | None = None, *, project_codes: list[str] | None = None) -> dict:
    t0 = time.perf_counter()
    results = []
    for rtype, period in regenerate_plan():
        if types and rtype not in types:
            continue
        scoped_codes = project_codes if rtype in {"weekly_profit", "project_operation", "company_weekly"} else None
        if scoped_codes == []:
            continue
        sp = db.begin_nested()
        try:
            r = generate(db, rtype, period, trigger, user_id, scoped_codes)
            sp.commit()
            results.append({"report_type": rtype, "period": r.period, "id": r.id, "version": r.version,
                            "title": r.title, "cost_ms": r.cost_ms, "ok": True,
                            "summary": r.metrics.get("summary", [])})
        except Exception as exc:  # noqa: BLE001
            sp.rollback()
            results.append({"report_type": rtype, "period": period, "ok": False, "error": str(exc)[:300]})
    return {"total": len(results), "success": sum(1 for r in results if r["ok"]),
            "failed": sum(1 for r in results if not r["ok"]), "cost_ms": int((time.perf_counter() - t0) * 1000),
            "results": results}


def latest(db: Session, report_type: str, period: str) -> RptReport | None:
    return db.scalar(select(RptReport).where(RptReport.report_type == report_type, RptReport.period == period)
                     .order_by(RptReport.version.desc()).limit(1))


def report_files(db: Session, r: RptReport) -> list[SysFile]:
    if not r.file_ids:
        return []
    fs = {f.id: f for f in db.scalars(select(SysFile).where(SysFile.id.in_(r.file_ids)))}
    return [fs[i] for i in r.file_ids if i in fs]


def catalog(db: Session) -> list[dict]:
    out = []
    for key, spec in REPORT_TYPES.items():
        out.append({"report_type": key, "name": spec["name"], "period_kind": spec["period_kind"],
                    "schedule": spec["schedule"], "job": spec["job"], "prd": spec["prd"], "formats": spec["formats"],
                    "caliber": caliber_lines(db, key), "supports_project": key in ("weekly_profit", "project_operation")})
    return out
