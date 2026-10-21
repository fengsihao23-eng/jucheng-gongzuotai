"""总经办领域种子：门户卡片方案、已归档合同原件（docx，含付款条款）、重点项目近 10 天进展反馈。"""
import io
import random
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

from app.core.storage import save_bytes
from app.core.timeutil import utcnow
from app.models import BizContract, BizProject, BizProjectFeedback, SysDict

COMPANY = "广东聚成数智科技有限公司"


def contract_docx(c: BizContract, project_name: str | None = None, terms: list[tuple[str, int]] | None = None) -> bytes:
    """生成合同原件 docx。terms: [(阶段, 百分比)]，默认 预付 30 / 进度 40 / 验收 25 / 质保 5。"""
    from docx import Document
    from docx.shared import Pt
    terms = terms or [("预付款", 30), ("进度款", 40), ("验收款", 25), ("质保金", 5)]
    amt = Decimal(c.amount)
    doc = Document()
    st = doc.styles["Normal"]
    st.font.size = Pt(11)
    doc.add_heading(f"{c.name or project_name or '项目'}", level=1)
    doc.add_paragraph(f"合同编号：{c.contract_no}")
    doc.add_paragraph(f"甲方（采购人）：{c.customer_name}")
    doc.add_paragraph(f"乙方（供应商）：{COMPANY}")
    doc.add_paragraph(f"签订日期：{c.sign_date:%Y年%m月%d日}")
    doc.add_heading("第一条 项目内容", level=2)
    doc.add_paragraph(f"乙方按照甲方要求完成「{project_name or c.name}」的设计、供货、安装调试、培训及售后服务。")
    doc.add_heading("第二条 合同金额", level=2)
    doc.add_paragraph(f"本合同总价为人民币 {amt:,.2f} 元（含税），该价格为固定总价。")
    doc.add_heading("第三条 付款方式", level=2)
    clause = {
        "预付款": lambda p: f"预付款：合同签订后10个工作日内，甲方向乙方支付合同总价的{p}%作为预付款，"
                         f"计人民币 {amt * p / 100:,.2f} 元",
        "进度款": lambda p: f"进度款：项目主要设备安装调试完成并经甲方确认后15日内，甲方支付合同金额的{p}%",
        "到货款": lambda p: f"到货款：设备全部到货并经甲方清点签收后15日内，甲方支付合同金额的{p}%",
        "验收款": lambda p: f"验收款：项目终验合格后30日内，甲方支付合同金额的{p}%",
        "质保金": lambda p: f"质保金：合同金额的{p}%作为质量保证金，在验收合格满一年后无息支付",
    }
    for i, (stage, pct) in enumerate(terms, start=1):
        doc.add_paragraph(f"{i}. {clause.get(stage, clause['验收款'])(pct)}。")
    doc.add_paragraph("乙方在每次收款前向甲方开具等额合法有效的增值税专用发票，税率按国家规定执行。")
    doc.add_heading("第四条 违约责任", level=2)
    doc.add_paragraph("任何一方逾期履行，每逾期一日按未履行部分金额的万分之五向对方支付违约金。")
    doc.add_heading("第五条 其他", level=2)
    doc.add_paragraph("本合同一式肆份，甲乙双方各执贰份，自双方签字盖章之日起生效。")
    t = doc.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "甲方（盖章）", "乙方（盖章）"
    t.cell(1, 0).text, t.cell(1, 1).text = c.customer_name, COMPANY
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


FEEDBACK = {
    "JC-XY-2026-001": (["完成第三批 12 所学校点位复核，设计变更单已报教育局", "多媒体教室设备到货 85%，剩余交换机本周到货",
                        "与区教育局召开周例会，确认终验资料清单", "综合布线完成 9 所学校，进入设备安装阶段",
                        "部分学校机房改造与暑期施工计划冲突，需重排工期"],
                       ["终验时间存在延后风险：甲方要求增加平台对接，需评估工作量与变更费用"]),
    "JC-XY-2026-002": (["智慧教室 26 间完成安装调试", "校方信息中心确认平台账号体系对接方案",
                        "到货验收资料已提交监理，待签字"],
                       ["二期扩容合同已签订，需提前锁定 AP 设备货期（供应商反馈缺货 3 周）"]),
    "JC-XY-2026-003": (["招标文件已领取，技术参数逐条比对完成 70%", "售前完成方案初稿，报价测算中",
                        "现场踏勘完成，校方关注数据治理与基座对接"],
                       ["竞品已提前与校方技术部门沟通，评分细则对我方资质项不利，需补充案例证明"]),
    "JC-AF-2026-001": (["周界防范一期摄像机安装完成 160 路", "采购清单二批次已下单，海康到货周期 10 天",
                        "与校保卫处确认指挥中心大屏布局", "消防联动接口联调完成"],
                       []),
    "JC-YW-2025-002": (["9 月运维月报已提交，故障响应及时率 98.6%", "完成 3 所学校交换机巡检与固件升级",
                        "续签意向沟通：甲方希望增加教育装备资产盘点服务"],
                       ["上季度运维费回款尚未到账，财政拨付流程延迟，需商务跟进"]),
    "JC-KP-2026-001": (["素质评价平台 2.3 版本上线试运行", "完成 6 个区教研员培训，累计 420 人",
                        "数据中台对接市教育局学籍库，接口联调通过", "试运行问题清单 18 项，已关闭 14 项"],
                       ["试运行期间并发峰值超预期，需扩容服务器并调整验收时间"]),
}


def seed(db, ctx):
    rnd = random.Random(20261021)
    T = ctx.T
    U = ctx.users

    # ---------- 门户卡片方案（管理员可在系统管理-字典调整） ----------
    from app.api.portal import DEFAULT_SCHEMES
    for scheme, cards in DEFAULT_SCHEMES.items():
        if not db.scalar(select(SysDict.id).where(SysDict.dict_type == "portal_cards", SysDict.dict_key == scheme)):
            db.add(SysDict(dict_type="portal_cards", dict_key=scheme, dict_value=cards,
                           remark=f"门户卡片方案：{scheme}（卡片 key 见 GET /portal/schemes）"))
    db.flush()

    # ---------- 已归档合同：生成合同原件 docx 并挂 archive_file_id ----------
    projects = {p.project_code: p for p in db.scalars(select(BizProject)).unique().all()}
    for c in db.scalars(select(BizContract).where(BizContract.status == 2, BizContract.archive_file_id.is_(None))):
        pj = projects.get(c.project_code)
        data = contract_docx(c, pj.name if pj else None)
        f = save_bytes(db, data=data, filename=f"{c.contract_no}.docx", category="contract",
                       key=f"contract/{c.contract_no}", uploader_id=U["songjia"].id,
                       content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        c.archive_file_id = f.id
        c.archived_at = utcnow() - timedelta(days=rnd.randint(3, 25))
    db.flush()

    # ---------- 重点项目近 10 天进展反馈（3~6 条，含 1~2 条风险） ----------
    for code, (progress, risks) in FEEDBACK.items():
        p = projects.get(code)
        if not p or db.scalar(select(BizProjectFeedback.id).where(BizProjectFeedback.project_id == p.id)):
            continue
        reporter = p.market_owner_id or p.owner_id
        items = [(t, "progress", rnd.choice([0, 0, 1])) for t in progress]
        if not risks:
            risks = ["采购到货节点已延期，正在协调供应商分批发货"]
        items += [(t, "risk", 2) for t in risks[:2]]
        rnd.shuffle(items)
        items = items[:6]
        if not any(k == "risk" for _, k, _ in items):
            items[-1] = (risks[0], "risk", 2)
        days = sorted(rnd.sample(range(0, 10), len(items)), reverse=True)
        for (text, kind, lvl), back in zip(items, days):
            db.add(BizProjectFeedback(project_id=p.id, feedback_date=T - timedelta(days=back), kind=kind, content=text,
                                      risk_level=lvl,
                                      reporter_id=reporter if kind != "risk" or not p.owner_id else p.owner_id))
    db.flush()
