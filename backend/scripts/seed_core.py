"""核心种子数据（与 PRD 3.6 / 8.4 演示基线一致）。全部相对“今天”生成，随机种子固定可复现。

基线：当月回款 ¥4,860,000 / 目标 ¥7,000,000（69.4%）；上月回款 ¥6,120,000 / 目标 ¥6,500,000。
"""
import json
import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.permissions import BUSINESS_LINES, DEPARTMENTS, ROLE_DEFS
from app.core.security import hash_password
from app.core.timeutil import TZ, month_range, today, week_no, week_range
from app.integration.wecom import mock as wecom_mock
from app.models import (BizContract, BizExpenseItem, BizExpenseReport, BizOpportunity, BizPayment, BizPaymentPlan,
                        BizProject, BizProjectCost, BizProjectMilestone, FinBankFlow, FinCashJournal, HrEmployee,
                        HrHeadcountPlan, OaSubmission, SyncWecomApproval, SysDepartment, SysDict, SysRole, SysUser,
                        SysUserRole)
from scripts.roster import DEPT_WECOM_IDS, ROSTER

D = Decimal
ACCOUNTS = {"cmb": "招商银行基本户 7559****0001", "icbc": "工商银行一般户 3602****0088"}
COMPANY = "广东聚成数智科技有限公司"

TPL_IDS = {"contract": "3WLJF6naF4PCb6XkGq7x2NsnHKh7Cm8rLH2Vb1Rt", "expense": "C4NyXUwrzEBLLsT3VhGPtBxCj7YhwwyqZ6VMoFbp",
           "opportunity": "3TkaX8fCQn2pVA6cHkZ6LV6gGHwPy7xQy9BMRp3e"}
APPROVAL_TEMPLATES = {
    TPL_IDS["contract"]: {"key": "contract", "name": "销售合同审批",
                          "fields": {"合同名称": "name", "客户名称": "customer_name", "合同金额": "amount",
                                     "所属项目": "project_code", "业务线": "business_line", "备注": "remark"}},
    TPL_IDS["expense"]: {"key": "expense", "name": "费用报销",
                         "fields": {"报销金额": "amount", "费用类别": "category", "费用日期": "expense_date",
                                    "所属项目": "project_code", "报销事由": "reason", "发票张数": "invoice_count"}},
    TPL_IDS["opportunity"]: {"key": "opportunity", "name": "商机工单",
                             "fields": {"商机名称": "name", "客户名称": "customer_name", "预计金额": "amount",
                                        "业务线": "business_line", "商机阶段": "stage",
                                        "预计签约日期": "expected_sign_date", "备注": "remark"}},
}
STANDARD_MILESTONES = [
    {"node": "深化设计", "duty": "项目经理牵头，设计工程师完成施工图深化、点位复核，报甲方确认", "days": 25},
    {"node": "进度管理", "duty": "项目经理编制总进度计划，按周更新进度并上报偏差", "days": 20},
    {"node": "采购", "duty": "项目经理提需求，采购按清单比价下单，跟踪到货周期", "days": 30},
    {"node": "到货验收", "duty": "项目工程师组织开箱验货，资料员归档合格证与清单", "days": 20},
    {"node": "终验", "duty": "项目经理组织甲方终验，整理竣工资料与验收报告", "days": 25},
]
EXPENSE_CATS = {"差旅费": (300, 3800), "交通费": (40, 460), "招待费": (300, 2600), "办公费": (60, 1200),
                "材料采购": (800, 9800), "通讯费": (50, 300), "劳务费": (1500, 8000)}

# (code, name, customer, status, key, pm, market, budget)
PROJECTS = [
    ("JC-XY-2026-001", "南沙区中小学数字校园建设", "广州市南沙区教育局", 2, True, "xuhao", "wujun", 9_800_000),
    ("JC-XY-2026-002", "东莞松山湖实验学校智慧校园", "东莞松山湖实验学校", 2, True, "pengbo", "zhengyu", 6_200_000),
    ("JC-XY-2026-003", "顺德职业技术学院数字基座一期", "顺德职业技术学院", 1, True, None, "wujun", 7_500_000),
    ("JC-XY-2026-004", "江门新会区智慧教室推广", "江门市新会区教育局", 2, False, "lufeng", "fengxue", 4_300_000),
    ("JC-AF-2026-001", "广东海洋大学校园综合安防", "广东海洋大学", 2, True, "jiangtao", "heyang", 8_600_000),
    ("JC-AF-2026-002", "宝安区中小学明厨亮灶及视频联网", "深圳市宝安区教育局", 2, False, "yuanhang", "zhengyu", 5_400_000),
    ("JC-AF-2026-003", "珠海高新区校园安全防控平台", "珠海高新区社会事业局", 1, False, None, "heyang", 12_000_000),
    ("JC-AF-2026-004", "中山一中校园安防改造", "中山市第一中学", 3, False, "pengbo", "fengxue", 3_600_000),
    ("JC-YW-2025-001", "天河区教育城域网运维", "广州市天河区教育局", 4, False, "lufeng", "wujun", 3_200_000),
    ("JC-YW-2025-002", "佛山市教育装备运维服务", "佛山市教育局", 4, True, "yuanhang", "heyang", 4_800_000),
    ("JC-YW-2026-001", "惠州市属学校安防运维", "惠州市教育局", 1, False, None, "luoqian", 2_600_000),
    ("JC-KP-2026-001", "广州市中小学素质教育综合评价平台", "广州市教育研究院", 2, True, "xuhao", "luoqian", 3_900_000),
    ("JC-KP-2026-002", "湛江市学生体质健康监测系统", "湛江市教育局", 3, False, "jiangtao", "fengxue", 2_800_000),
    ("JC-KP-2025-001", "肇庆端州区艺术素养考评", "肇庆市端州区教育局", 5, False, "xuhao", "luoqian", 1_600_000),
]

# 合同：(项目, 金额, 签订日相对今天的天数/或固定日期, 来源 1 审批/2 人工)
CONTRACTS = [
    ("JC-YW-2025-001", 2_760_000, date(2025, 3, 18), 2), ("JC-YW-2025-002", 4_350_000, date(2025, 5, 9), 2),
    ("JC-KP-2025-001", 1_480_000, date(2025, 6, 26), 2), ("JC-AF-2026-004", 3_380_000, date(2025, 9, 2), 2),
    ("JC-KP-2026-002", 2_650_000, date(2025, 10, 11), 2), ("JC-XY-2026-001", 6_200_000, date(2025, 11, 20), 2),
    ("JC-AF-2026-001", 5_100_000, date(2025, 12, 15), 2), ("JC-YW-2025-001", 1_200_000, date(2025, 8, 7), 2),
    ("JC-XY-2026-002", 5_680_000, -230, 1), ("JC-XY-2026-001", 2_120_000, -205, 1), ("JC-AF-2026-002", 4_960_000, -190, 1),
    ("JC-KP-2026-001", 2_300_000, -176, 1), ("JC-AF-2026-001", 3_200_000, -150, 1), ("JC-XY-2026-004", 3_960_000, -135, 1),
    ("JC-YW-2025-002", 1_850_000, -120, 1), ("JC-YW-2025-001", 980_000, -101, 1), ("JC-KP-2026-001", 1_350_000, -88, 1),
    ("JC-XY-2026-002", 1_460_000, -70, 1), ("JC-AF-2026-002", 1_280_000, -58, 1), ("JC-YW-2025-002", 760_000, -45, 1),
    ("JC-XY-2026-001", 1_580_000, -36, 1), ("JC-AF-2026-004", 640_000, -30, 1), ("JC-KP-2026-002", 420_000, -26, 1),
    ("JC-XY-2026-004", 880_000, -12, 1), ("JC-AF-2026-001", 1_960_000, -9, 1), ("JC-YW-2025-001", 520_000, -6, 1),
    ("JC-KP-2026-001", 760_000, -3, 1), ("JC-XY-2026-002", 2_040_000, 0, 1),
    # 去年同期（年度同比）
    ("JC-YW-2025-001", 1_900_000, date(2025, 1, 16), 2), ("JC-KP-2025-001", 980_000, date(2025, 2, 21), 2),
    ("JC-YW-2025-002", 2_200_000, date(2025, 4, 2), 2), ("JC-AF-2026-004", 1_100_000, date(2025, 7, 15), 2),
]

CUR_MONTH_PAY = {"XY": 1_960_000, "AF": 1_580_000, "YW": 920_000, "KP": 400_000}  # = 4,860,000
PREV_MONTH_PAY = {"XY": 2_450_000, "AF": 1_980_000, "YW": 1_150_000, "KP": 540_000}  # = 6,120,000
CUR_TARGET = {"XY": 2_800_000, "AF": 2_200_000, "YW": 1_200_000, "KP": 800_000}  # = 7,000,000
PREV_TARGET = {"XY": 2_600_000, "AF": 2_000_000, "YW": 1_200_000, "KP": 700_000}  # = 6,500,000

OPPS = [  # (名称, 客户, 业务线, 阶段, 金额, 创建距今天数, 负责人)
    ("增城区智慧教育云平台", "广州市增城区教育局", "XY", "lead", 5_600_000, -2, "wujun"),
    ("南海区校园视频监控联网", "佛山市南海区教育局", "AF", "lead", 2_300_000, -1, "zhengyu"),
    ("清远职业技术学院智慧校园", "清远职业技术学院", "XY", "contact", 3_100_000, -8, "fengxue"),
    ("珠海市属中学一键报警及周界防范", "珠海市教育局", "AF", "contact", 6_800_000, -11, "heyang"),
    ("黄埔区学生综合素质评价", "广州市黄埔区教育局", "KP", "quote", 1_800_000, -16, "luoqian"),
    ("前海国际学校数字校园总包", "深圳前海国际学校", "XY", "quote", 9_200_000, -19, "wujun"),
    ("番禺区中小学安防提升", "广州市番禺区教育局", "AF", "negotiation", 4_400_000, -24, "heyang"),
    ("天河区教育城域网运维续签", "广州市天河区教育局", "YW", "negotiation", 2_900_000, -28, "wujun"),
    ("东莞市中考体育考评系统", "东莞市教育局", "KP", "contact", 1_500_000, -33, "luoqian"),
    ("韶关学院数据中心二期", "韶关学院", "XY", "lead", 7_600_000, -37, "zhengyu"),
    ("湛江一中智慧校园", "湛江第一中学", "XY", "quote", 3_800_000, -41, "fengxue"),
    ("广东海洋大学安防运维", "广东海洋大学", "YW", "contact", 1_200_000, -45, "heyang"),
    ("阳江市校园食品安全监管平台", "阳江市市场监督管理局", "AF", "negotiation", 5_200_000, -52, "zhengyu"),
    ("汕头大学智慧教室", "汕头大学", "XY", "lead", 2_100_000, -58, "fengxue"),
    ("惠州大亚湾学校运维", "惠州大亚湾区教育局", "YW", "quote", 1_700_000, -61, "luoqian"),
    ("江门市艺术素养测评", "江门市教育局", "KP", "lead", 900_000, -64, "luoqian"),
    ("中山翠亨新区智慧学校", "中山翠亨新区社会事务局", "XY", "contact", 4_600_000, -70, "wujun"),
    ("肇庆高新区学生体质监测", "肇庆高新区社会事务局", "KP", "quote", 1_300_000, -76, "luoqian"),
    ("松山湖实验学校二期", "东莞松山湖实验学校", "XY", "won", 2_040_000, -82, "zhengyu"),
    ("宝安区明厨亮灶扩容", "深圳市宝安区教育局", "AF", "won", 1_280_000, -88, "zhengyu"),
    ("佛山禅城教育网升级", "佛山市禅城区教育局", "XY", "lost", 2_600_000, -95, "fengxue"),
    ("珠海金湾区校园安防运维", "珠海市金湾区教育局", "YW", "lost", 1_900_000, -101, "heyang"),
    ("广州大学城高校安防联网", "广州大学城管委会", "AF", "negotiation", 6_300_000, -5, "wujun"),
    ("越秀区中小学劳动教育评价", "广州市越秀区教育局", "KP", "contact", 1_100_000, -3, "luoqian"),
]


class Ctx:
    """跨领域种子共享的查找表。"""

    def __init__(self):
        self.T = today()
        self.users: dict[str, SysUser] = {}
        self.depts: dict[str, SysDepartment] = {}
        self.projects: dict[str, BizProject] = {}
        self.contracts: list[BizContract] = []


def _dt(d: date, h: int = 10, m: int = 0) -> datetime:
    return datetime.combine(d, time(h, m), TZ)


def seed(db: Session) -> Ctx:
    ctx = Ctx()
    rnd = random.Random(20261020)
    T = ctx.T

    # ---------- 部门 / 角色 / 用户 ----------
    for i, (code, name) in enumerate(DEPARTMENTS):
        d = SysDepartment(dept_code=code, name=name, wecom_dept_id=DEPT_WECOM_IDS[code][0], sort=i)
        db.add(d)
        ctx.depts[code] = d
    roles = {}
    for code, d in ROLE_DEFS.items():
        roles[code] = SysRole(role_code=code, name=d["name"], dept_code=d["dept"], permissions={})
        db.add(roles[code])
    db.flush()
    pwd = hash_password(__import__("app.core.config", fromlist=["settings"]).settings.demo_password)
    for r in ROSTER:
        u = SysUser(wecom_userid=r["userid"], name=r["name"], department_id=ctx.depts[r["dept"]].id,
                    position=r["position"], mobile=r["mobile"], email=f"{r['userid']}@jc-digital.cn",
                    status=2 if r["left"] else 1, password_hash=pwd, preferences={})
        db.add(u)
        db.flush()
        db.add(SysUserRole(user_id=u.id, role_id=roles[r["role"]].id))
        ctx.users[r["userid"]] = u
    db.flush()
    U = ctx.users

    # ---------- 字典口径 ----------
    def put(t, k, v, remark=None):
        db.add(SysDict(dict_type=t, dict_key=k, dict_value=v, remark=remark))

    for k, v in BUSINESS_LINES.items():
        put("business_line", k, v)
    cur, prev = f"{T:%Y-%m}", f"{(T.replace(day=1) - timedelta(days=1)):%Y-%m}"
    nxt = f"{(T.replace(day=28) + timedelta(days=5)):%Y-%m}"
    put("payment_target", cur, CUR_TARGET, "当月回款目标（元），按业务线")
    put("payment_target", prev, PREV_TARGET)
    put("payment_target", nxt, {"XY": 3_000_000, "AF": 2_400_000, "YW": 1_200_000, "KP": 900_000})
    for i in range(2, 10):
        m = (T.replace(day=1) - timedelta(days=28 * i))
        put("payment_target", f"{m:%Y-%m}", {"XY": 2_400_000, "AF": 1_900_000, "YW": 1_100_000, "KP": 600_000})
    put("opportunity_stage", "stages", [{"key": "lead", "name": "线索"}, {"key": "contact", "name": "初步接触"},
                                        {"key": "quote", "name": "方案报价"}, {"key": "negotiation", "name": "商务谈判"},
                                        {"key": "won", "name": "赢单"}, {"key": "lost", "name": "丢单"}])
    for tid, tpl in APPROVAL_TEMPLATES.items():
        put("approval_template", tid, tpl, f"企微审批模板映射：{tpl['name']}")
    put("expense_category", "list", list(EXPENSE_CATS))
    put("caliber", "profit", {"income_basis": "contract", "public_expense": "exclude",
                              "cost_sources": ["expense", "project_cost", "lemon_aux"]},
        "项目毛利口径（待财务评审）：收入=合同额；公共费用不摊入项目")
    put("caliber", "payment", {"date_basis": "bank_trade_date"}, "回款以银行流水到账日为准")
    put("report_rule", "submit", {"weekly": {"exclude_depts": ["ADMIN"], "exclude_roles": ["EMP"]},
                                  "monthly": {"exclude_depts": ["ADMIN"], "exclude_roles": ["EMP"]},
                                  "daily": {"include_depts": ["XM", "SC"], "exclude_roles": ["EMP"]},
                                  "weekly_deadline": "周五 18:00", "monthly_deadline": "次月 3 日 18:00"},
        "应提交范围与截止时间")
    put("notify_rule", "morning_report", {"roles": ["ZB-01", "ZB-02"], "time": "08:00"})
    put("notify_rule", "daily_expense", {"roles": ["CW-01", "ZB-01"], "time": "08:00"})
    put("alert_rule", "hr", {"contract_lead_days": 30, "probation_lead_days": 15})
    put("milestone_template", "standard", STANDARD_MILESTONES, "系统集成项目标准控制流程（数字校园/安防/运维）")
    put("milestone_template", "software", [
        {"node": "需求调研", "duty": "产品经理调研并输出需求规格说明书", "days": 20},
        {"node": "设计开发", "duty": "研发负责人按迭代交付", "days": 45},
        {"node": "测试", "duty": "测试工程师完成功能与性能测试", "days": 20},
        {"node": "上线试运行", "duty": "项目经理组织上线与培训", "days": 15},
        {"node": "终验", "duty": "项目经理组织甲方终验", "days": 15}], "平台软件交付流程（素质教育考评）")

    # ---------- 项目 + 流程节点 ----------
    for code, name, cust, status, key, pm, mk, budget in PROJECTS:
        line = code.split("-")[1]
        start = T - timedelta(days=rnd.randint(120, 260)) if status != 1 else None
        p = BizProject(project_code=code, name=name, business_line=line, customer_name=cust,
                       owner_id=U[pm].id if pm else None, market_owner_id=U[mk].id, status=status, is_key=key,
                       budget_amount=D(budget), start_date=start,
                       bid_deadline=T + timedelta(days=rnd.randint(6, 28)) if status == 1 else None,
                       milestone_template="software" if line == "KP" else "standard")
        db.add(p)
        ctx.projects[code] = p
        if status == 1:
            continue
        tpl = STANDARD_MILESTONES if line != "KP" else None
        nodes = tpl or [{"node": n, "duty": d, "days": x} for n, d, x in [
            ("需求调研", "产品经理调研并输出需求规格说明书", 20), ("设计开发", "研发负责人按迭代交付", 45),
            ("测试", "测试工程师完成功能与性能测试", 20), ("上线试运行", "项目经理组织上线与培训", 15),
            ("终验", "项目经理组织甲方终验", 15)]]
        plan = start
        engineers = ["dingrui", "weichen", "shenkang", "guojian", "qinlang"]
        for i, n in enumerate(nodes, start=1):
            plan = plan + timedelta(days=n["days"] + (25 if status == 2 else 0))
            ms = BizProjectMilestone(seq=i, node_name=n["node"], duty=n["duty"], plan_date=plan,
                                     owner_id=U[pm].id if i in (1, 2, 5) else U[rnd.choice(engineers)].id)
            prev_done = i == 1 or p.milestones[-1].status in (3, 4)
            if status in (3, 4, 5) or plan < T - timedelta(days=45):
                late = rnd.random() < 0.25
                ms.finish_date = plan + timedelta(days=rnd.randint(2, 9) if late else -rnd.randint(0, 4))
                ms.status, ms.alert_flag = 3, ms.finish_date > plan
                ms.progress_note = "已完成" + ("（较计划延期）" if late else "")
            elif plan < T and prev_done:
                if rnd.random() < 0.55:  # 计划日已过未完成 → 延期标红
                    ms.status, ms.alert_flag = 4, True
                    ms.progress_note = rnd.choice(["甲方图纸确认滞后", "设备到货延迟，已催办供应商", "现场条件不具备，等待土建移交",
                                                   "学校教学时段限制施工窗口"])
                else:
                    ms.finish_date = plan + timedelta(days=rnd.randint(0, 6))
                    ms.status, ms.alert_flag = 3, ms.finish_date > plan
                    ms.progress_note = "已完成"
            elif plan < T + timedelta(days=30) and prev_done:
                ms.status, ms.progress_note = 2, "进行中"
            p.milestones.append(ms)
        if status in (3, 4, 5):
            p.plan_end_date = p.milestones[-1].plan_date
    db.flush()

    # ---------- 合同 ----------
    seq_by_year: dict[int, int] = {}
    approvals: list[dict] = []
    for proj_code, amount, when, src in CONTRACTS:
        sd = when if isinstance(when, date) else T + timedelta(days=when)
        seq_by_year[sd.year] = seq_by_year.get(sd.year, 0) + 1
        p = ctx.projects[proj_code]
        no = f"HT-{sd.year}-{seq_by_year[sd.year]:04d}"
        c = BizContract(contract_no=no, name=f"{p.name}{'（补充协议）' if any(x.project_code == proj_code for x in ctx.contracts) else ''}合同",
                        project_code=proj_code, customer_name=p.customer_name, amount=D(amount), sign_date=sd,
                        business_line=p.business_line, owner_id=p.market_owner_id, source_type=src,
                        status=2 if (T - sd).days > 30 else 1)
        if src == 1:
            sp_no = f"{sd:%Y%m%d}{rnd.randint(1000, 9999)}01"
            c.source_approval_no = sp_no
            mk = db.get(SysUser, p.market_owner_id)
            fields = {"合同名称": c.name, "客户名称": c.customer_name, "合同金额": f"{amount}", "所属项目": proj_code,
                      "业务线": p.business_line, "备注": ""}
            approvals.append({"sp_no": sp_no, "template_id": TPL_IDS["contract"], "sp_name": "销售合同审批",
                              "sp_status": 2, "apply_time": _dt(sd - timedelta(days=2)).isoformat(),
                              "applicant_userid": mk.wecom_userid, "fields": fields,
                              "approved_time": _dt(sd, 16).isoformat(), "_target": no, "_key": "contract"})
        db.add(c)
        ctx.contracts.append(c)
        if p.contract_amount is None:
            p.contract_amount = D(0)
        p.contract_amount += D(amount)
    db.flush()

    # 回款计划（已归档合同：预付 30% / 进度 40% / 验收 25% / 质保 5%）
    STAGES = [("预付款", D("0.30"), 10), ("进度款", D("0.40"), 120), ("验收款", D("0.25"), 240), ("质保金", D("0.05"), 600)]
    for c in ctx.contracts:
        if c.status != 2:
            continue
        for i, (stage, ratio, days) in enumerate(STAGES, start=1):
            c.plans.append(BizPaymentPlan(seq=i, stage_name=stage, ratio=ratio, amount=(c.amount * ratio).quantize(D("1")),
                                          due_date=c.sign_date + timedelta(days=days),
                                          condition_text=f"{stage}：合同金额的 {int(ratio * 100)}%"))
    db.flush()

    # ---------- 回款 + 银行流水（收） ----------
    flows: list[FinBankFlow] = []

    def add_flow(acct, d, direction, amount, cp, summary, status, ref=None, hour=None):
        f = FinBankFlow(bank_account=ACCOUNTS[acct], trade_date=d,
                        trade_time=datetime.combine(d, time(hour or rnd.randint(9, 17), rnd.randint(0, 59))),
                        direction=direction, amount=D(amount).quantize(D("0.01")), counterparty=cp, summary=summary,
                        fingerprint="", match_status=status, biz_ref=ref, source_file="seed")
        flows.append(f)
        return f

    remaining = {c.id: c.amount for c in ctx.contracts}
    all_payments: list[BizPayment] = []

    def month_pay(month_start: date, month_end: date, line: str, total: int):
        cands = [c for c in ctx.contracts if c.business_line == line and c.sign_date <= month_end]
        rnd.shuffle(cands)
        left = D(total)
        chunks = []
        while left > 0 and cands:
            c = max(cands, key=lambda x: remaining[x.id])
            if remaining[c.id] <= 0:
                break
            amt = min(left, remaining[c.id], D(rnd.randrange(180_000, 900_000, 10_000)))
            if left - amt < 50_000:
                amt = min(left, remaining[c.id])
            chunks.append((c, amt))
            remaining[c.id] -= amt
            left -= amt
            cands = [x for x in cands if x is not c] + [c]
        for c, amt in chunks:
            lo = max(month_start, c.sign_date)
            d = lo + timedelta(days=rnd.randint(0, max(0, (month_end - lo).days)))
            pay = BizPayment(contract_id=c.id, amount=amt, payment_date=d, source_type=1, register_by=U["zhaoli"].id)
            db.add(pay)
            all_payments.append(pay)
            f = add_flow("cmb", d, 1, amt, c.customer_name, f"{ctx.projects[c.project_code].name} 回款", 2, c.contract_no)
            pay._flow = f

    cur_start = T.replace(day=1)
    prev_end = cur_start - timedelta(days=1)
    for i in range(10, 1, -1):  # 前 9 个月
        ms, me = month_range(ref=cur_start - timedelta(days=28 * i + 3))
        for line, (lo, hi) in {"XY": (600_000, 1_800_000), "AF": (500_000, 1_500_000), "YW": (300_000, 900_000),
                               "KP": (80_000, 360_000)}.items():
            month_pay(ms, me, line, rnd.randrange(lo, hi, 10_000))
    for line, amt in PREV_MONTH_PAY.items():
        month_pay(prev_end.replace(day=1), prev_end, line, amt)
    for line, amt in CUR_MONTH_PAY.items():
        month_pay(cur_start, T, line, amt)
    db.flush()

    # 回款 → 计划阶段消化
    for c in ctx.contracts:
        pays = sorted([p for p in all_payments if p.contract_id == c.id], key=lambda x: x.payment_date)
        for pay in pays:
            left = pay.amount
            for plan in c.plans:
                due = plan.amount - plan.received_amount
                if due <= 0 or left <= 0:
                    continue
                take = min(due, left)
                plan.received_amount += take
                pay.plan_id = pay.plan_id or plan.id
                left -= take
        for plan in c.plans:
            plan.status = 4 if plan.received_amount >= plan.amount else 3 if plan.received_amount > 0 else \
                (5 if plan.due_date and plan.due_date < T else 1)

    # 待核对的到账（未计入回款，用于出纳核对演示）
    pending_in = [c for c in ctx.contracts if c.status == 2 and remaining[c.id] > 300_000][:3]
    for i, c in enumerate(pending_in):
        nxt_plan = next((pl for pl in c.plans if pl.received_amount < pl.amount), None)
        amt = (nxt_plan.amount - nxt_plan.received_amount) if nxt_plan else D(300_000)
        add_flow("cmb", T - timedelta(days=i % 2), 1, amt, c.customer_name,
                 f"{ctx.projects[c.project_code].name} {nxt_plan.stage_name if nxt_plan else '款项'}", 0)
    add_flow("icbc", T - timedelta(days=1), 1, 1286.43, "工商银行", "结息", 0)

    # ---------- 报销（近 90 天） ----------
    staff = [u for k, u in U.items() if k not in ("admin", "tianye")]
    active_codes = [c for c, p in ctx.projects.items() if p.status in (2, 3, 4)]
    exp_seq = 0
    for back in range(90, -1, -1):
        d = T - timedelta(days=back)
        n = rnd.randint(1, 3) if d.weekday() >= 5 else rnd.randint(3, 10)
        for _ in range(n):
            exp_seq += 1
            u = rnd.choice(staff)
            cat = rnd.choice(list(EXPENSE_CATS))
            lo, hi = EXPENSE_CATS[cat]
            amt = D(rnd.randint(lo, hi)) + D(rnd.choice([0, 0, 50, 80])) / 100
            r = rnd.random()
            if back <= 6 and r < 0.12:
                status, pcode, raw = 2, None, rnd.choice(["", "南沙项目", "JC-XY-2026-099"])  # 待归集
            elif r < 0.28 or u.dept_code in ("CW", "RS", "ZB") and r < 0.7:
                status, pcode, raw = 4, None, "公共费用"
            else:
                status = 4
                pcode = rnd.choice(active_codes)
                raw = f"{pcode} {ctx.projects[pcode].name}"
            approved = _dt(d + timedelta(days=rnd.choice([0, 0, 1])), rnd.randint(9, 18), rnd.randint(0, 59))
            if approved.date() > T:
                approved = _dt(T, 9, rnd.randint(0, 59))
            sp_no = f"{approved:%Y%m%d}{exp_seq:04d}{rnd.randint(10, 99)}"
            reason = {"差旅费": "项目现场出差", "交通费": "市内交通", "招待费": "客户接待", "办公费": "办公用品",
                      "材料采购": "现场辅材采购", "通讯费": "通讯补贴", "劳务费": "临时劳务"}[cat]
            e = BizExpenseReport(approval_no=sp_no, applicant_id=u.id, dept_code=u.dept_code, project_code=pcode,
                                 amount=amt, expense_date=d, approved_at=approved, category=cat, reason=reason,
                                 status=status, detail={"raw_project": raw})
            e.items = [BizExpenseItem(item_name=cat, amount=amt, invoice_count=rnd.randint(1, 4))]
            db.add(e)
            fields = {"报销金额": str(amt), "费用类别": cat, "费用日期": d.isoformat(), "所属项目": raw,
                      "报销事由": reason, "发票张数": "1"}
            approvals.append({"sp_no": sp_no, "template_id": TPL_IDS["expense"], "sp_name": "费用报销", "sp_status": 2,
                              "apply_time": _dt(d, 9).isoformat(), "applicant_userid": u.wecom_userid,
                              "fields": fields, "approved_time": approved.isoformat(), "_target": sp_no, "_key": "expense",
                              "_dispatch": 1})
            # 付款：审批后 2 天内付（最近 2 天的已出账但未核对）
            pay_day = approved.date() + timedelta(days=rnd.choice([1, 2]))
            if pay_day <= T - timedelta(days=2):
                e.pay_status = 1
                e._flow = add_flow("cmb", pay_day, 2, amt, u.name, f"报销款 {u.name} {sp_no}", 1, sp_no)
            elif pay_day <= T:
                add_flow("cmb", pay_day, 2, amt, u.name, f"报销 {u.name}", 0)
    db.flush()

    # ---------- 公户支出：工资 / 社保 / 税费 / 房租；项目采购成本 ----------
    for i in range(10, -1, -1):
        ms, me = month_range(ref=cur_start - timedelta(days=28 * i + 3)) if i else (cur_start, T)
        for day, cp, summary, amt, acct in [(10, "代发工资", "代发工资", rnd.randint(560_000, 640_000), "cmb"),
                                            (12, "广州市社会保险基金管理中心", "社保公积金", rnd.randint(150_000, 170_000), "cmb"),
                                            (15, "国家税务总局广州市税务局", "增值税及附加", rnd.randint(120_000, 320_000), "icbc"),
                                            (5, "广州天河科技园物业管理有限公司", "办公室租金", 86_000, "icbc")]:
            d = ms.replace(day=min(day, me.day))
            if d <= T:
                add_flow(acct, d, 2, amt, cp, summary, 3, None)
    suppliers = ["杭州海康威视数字技术股份有限公司", "华为技术有限公司", "希沃（广州视源电子科技）", "锐捷网络股份有限公司",
                 "中国移动通信集团广东有限公司", "广州盛通线缆有限公司"]
    for code in [c for c, p in ctx.projects.items() if p.status in (2, 3, 4, 5)]:
        p = ctx.projects[code]
        base = float(p.contract_amount or 0)
        for _ in range(rnd.randint(3, 5)):
            d = (p.start_date or T - timedelta(days=100)) + timedelta(days=rnd.randint(20, 160))
            if d > T - timedelta(days=2):
                d = T - timedelta(days=rnd.randint(3, 20))
            amt = D(round(base * rnd.uniform(0.11, 0.17), -2))
            sup = rnd.choice(suppliers)
            f = add_flow("cmb", d, 2, amt, sup, f"{p.name} 设备材料采购", 4, code)
            cost = BizProjectCost(project_code=code, cost_date=d, cost_type=rnd.choice(["采购", "采购", "分包"]),
                                  amount=amt, summary=f"{sup} 设备材料", created_by=U["sunyan"].id)
            cost._flow = f
            db.add(cost)

    # ---------- 流水入库：指纹 + 余额 ----------
    import hashlib
    opening = {ACCOUNTS["cmb"]: D("16000000"), ACCOUNTS["icbc"]: D("3500000")}
    flows.sort(key=lambda f: (f.trade_date, f.trade_time))
    for i, f in enumerate(flows):
        opening[f.bank_account] += f.amount if f.direction == 1 else -f.amount
        f.balance = opening[f.bank_account]
        f.source_row = i + 1
        f.fingerprint = hashlib.sha256(f"{f.bank_account}|{f.trade_date}|{f.direction}|{f.amount}|{f.summary}|{i}".encode()).hexdigest()
        if f.match_status:
            f.matched_by, f.matched_at = U["zhaoli"].id, _dt(f.trade_date, 18)
    db.add_all(flows)
    db.flush()
    for obj in list(db.identity_map.values()):
        fl = getattr(obj, "_flow", None)
        if fl is not None:
            if isinstance(obj, BizPayment):
                obj.bank_flow_id = fl.id
            elif isinstance(obj, BizExpenseReport):
                obj.bank_flow_id = fl.id
            elif isinstance(obj, BizProjectCost):
                obj.bank_flow_id = fl.id

    # 出纳日记账：已核对流水自动生成（source=1）+ 少量现金手工账
    cat_map = {1: "报销", 2: "回款", 3: "公户支出", 4: "采购"}
    for f in flows:
        if f.match_status in (1, 2, 3, 4):
            db.add(FinCashJournal(journal_date=f.trade_date, account_type="bank", bank_account=f.bank_account,
                                  direction=f.direction, amount=f.amount, summary=f.summary,
                                  category=cat_map[f.match_status], project_code=f.biz_ref if f.match_status == 4 else None,
                                  biz_ref=f.biz_ref, bank_flow_id=f.id, register_by=U["zhaoli"].id, source_type=1))
    for back in range(60, 0, -4):
        d = T - timedelta(days=back)
        db.add(FinCashJournal(journal_date=d, account_type="cash", direction=2, amount=D(rnd.randint(80, 900)),
                              summary=rnd.choice(["备用金-快递费", "备用金-茶水", "备用金-停车费", "备用金-打印装订"]),
                              category="其他", register_by=U["zhaoli"].id, source_type=2))
    db.add(FinCashJournal(journal_date=T - timedelta(days=61), account_type="cash", direction=1, amount=D(10000),
                          summary="提取备用金", category="其他", register_by=U["zhaoli"].id, source_type=2))

    # ---------- 商机 ----------
    for i, (name, cust, line, stage, amt, back, owner) in enumerate(OPPS, start=1):
        created = T + timedelta(days=back)
        o = BizOpportunity(opp_no=f"SJ-{created.year}-{i:04d}", name=name, customer_name=cust, business_line=line,
                           stage=stage, amount=D(amt), expected_sign_date=created + timedelta(days=rnd.randint(45, 150)),
                           owner_id=U[owner].id, source_type=1, status={"won": "won", "lost": "lost"}.get(stage, "open"))
        sp_no = f"{created:%Y%m%d}{8000 + i}03"
        o.source_approval_no = sp_no
        db.add(o)
        db.flush()
        o.created_at = _dt(created, 11)
        approvals.append({"sp_no": sp_no, "template_id": TPL_IDS["opportunity"], "sp_name": "商机工单", "sp_status": 2,
                          "apply_time": _dt(created, 9).isoformat(), "applicant_userid": owner,
                          "fields": {"商机名称": name, "客户名称": cust, "预计金额": str(amt), "业务线": line,
                                     "商机阶段": stage, "预计签约日期": "", "备注": ""},
                          "approved_time": _dt(created, 11).isoformat(), "_target": o.opp_no, "_key": "opportunity"})

    # ---------- 审批同步记录 + Mock 企微侧审批单 ----------
    for a in approvals:
        db.add(SyncWecomApproval(sp_no=a["sp_no"], template_id=a["template_id"], template_key=a["_key"],
                                 sp_name=a["sp_name"], applicant_userid=a["applicant_userid"], status=2,
                                 apply_time=datetime.fromisoformat(a["apply_time"]), detail={"fields": a["fields"]},
                                 received_via="callback", dispatch_status=1, dispatch_target=a["_target"],
                                 dispatched_at=datetime.fromisoformat(a["approved_time"])))
    mdir = wecom_mock.mock_dir()
    mdir.mkdir(parents=True, exist_ok=True)
    (mdir / "approvals.json").write_text(json.dumps([{k: v for k, v in a.items() if not k.startswith("_")}
                                                     for a in approvals], ensure_ascii=False), "utf-8")
    (mdir / "outbox.jsonl").write_text("", "utf-8")

    # ---------- 周报 / 月报 / 日报 提交记录（近 8 周） ----------
    must = [u for u in staff if u.status == 1 and u.dept_code != "ADMIN" and u.wecom_userid not in ("zengli", "panhui", "jinbo")]
    journals = []
    lazy = {U["heyang"].id, U["weichen"].id, U["linna"].id}  # 经常迟交/漏交
    this_week = week_no(T)
    for wk_back in range(8, -1, -1):
        ref = T - timedelta(weeks=wk_back)
        wk = week_no(ref)
        ws, we = week_range(ref=ref)
        for u in must:
            p_submit = 0.45 if u.id in lazy else 0.94
            if wk == this_week:
                p_submit = 0.0 if T.weekday() < 4 else 0.6
                if T.weekday() >= 1 and u.dept_code in ("ZB", "CW") and rnd.random() < 0.5:
                    p_submit = 1.0  # 少数人提前提交
            if rnd.random() < p_submit:
                st = _dt(min(we - timedelta(days=2), T), rnd.randint(15, 21), rnd.randint(0, 59))
                late = st.date() > ws + timedelta(days=4)
                db.add(OaSubmission(user_id=u.id, report_type="weekly", period=wk, submit_time=st,
                                    wecom_report_id=f"wk-{wk}-{u.wecom_userid}", stat_status=2 if late else 1))
                journals.append({"report_id": f"wk-{wk}-{u.wecom_userid}", "userid": u.wecom_userid,
                                 "template_name": "周报", "report_time": st.isoformat()})
    for m_back in (2, 1):
        ms, me = month_range(ref=cur_start - timedelta(days=28 * m_back - 20))
        period = f"{ms:%Y-%m}"
        for u in must:
            if rnd.random() < (0.5 if u.id in lazy else 0.93):
                st = _dt(me + timedelta(days=rnd.randint(0, 2)), 17)
                db.add(OaSubmission(user_id=u.id, report_type="monthly", period=period, submit_time=st,
                                    wecom_report_id=f"mo-{period}-{u.wecom_userid}"))
                journals.append({"report_id": f"mo-{period}-{u.wecom_userid}", "userid": u.wecom_userid,
                                 "template_name": "月报", "report_time": st.isoformat()})
    daily_users = [u for u in must if u.dept_code in ("XM", "SC")]
    for back in range(14, -1, -1):
        d = T - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        for u in daily_users:
            if back == 0:
                continue  # 今日日报尚未到截止
            if rnd.random() < (0.6 if u.id in lazy else 0.9):
                st = _dt(d, 18, rnd.randint(0, 59))
                db.add(OaSubmission(user_id=u.id, report_type="daily", period=d.isoformat(), submit_time=st,
                                    wecom_report_id=f"dy-{d}-{u.wecom_userid}"))
                journals.append({"report_id": f"dy-{d}-{u.wecom_userid}", "userid": u.wecom_userid,
                                 "template_name": "日报", "report_time": st.isoformat()})
    wecom_mock.put_journals(journals)

    # ---------- 人事档案 / 编制 ----------
    for i, u in enumerate(sorted(U.values(), key=lambda x: x.id), start=1):
        hire = T - timedelta(days=rnd.randint(200, 2600))
        cstart = hire
        while cstart + timedelta(days=365 * 3) < T:
            cstart += timedelta(days=365 * 3)
        emp = HrEmployee(user_id=u.id, employee_no=f"JC{i:04d}", gender=rnd.choice(["男", "女"]), hire_date=hire,
                         probation_end_date=hire + timedelta(days=90), contract_start_date=cstart,
                         contract_end_date=cstart + timedelta(days=365 * 3 - 1),
                         education=rnd.choice(["本科", "本科", "本科", "大专", "硕士"]),
                         employ_type="正式", annual_leave_days=D(5 + min(10, (T - hire).days // 365)))
        if u.wecom_userid == "tianye":
            emp.leave_date, emp.leave_reason = T - timedelta(days=18), "个人发展"
        db.add(emp)
    db.flush()
    # 指定到期样本：合同 30 天内到期 2 人、试用期 15 天内到期 2 人（新员工）
    for uid, days in (("huangqi", 12), ("guojian", 26)):
        e = db.query(HrEmployee).filter_by(user_id=U[uid].id).one()
        e.contract_end_date = T + timedelta(days=days)
    for uid, days in (("zhuyi", 9), ("yaoyao", 14)):
        e = db.query(HrEmployee).filter_by(user_id=U[uid].id).one()
        e.hire_date = T - timedelta(days=90 - days)
        e.probation_end_date, e.employ_type = T + timedelta(days=days), "试用"
        e.contract_start_date = e.hire_date
        e.contract_end_date = e.hire_date + timedelta(days=365 * 3 - 1)
    for code, n in {"ZB": 4, "CW": 6, "SC": 8, "YX": 4, "CP": 5, "RS": 3, "XM": 14, "KF": 5, "ADMIN": 1}.items():
        db.add(HrHeadcountPlan(year=T.year, dept_code=code, planned=n))

    db.flush()
    return ctx
