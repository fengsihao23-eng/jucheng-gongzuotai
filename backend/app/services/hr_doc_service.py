"""人事文书生成（python-docx 模板化）+ 用工风险提示规则库 + 会议纪要行动项提炼。

每份文书生成时执行风险规则，返回风险点列表（仅提示，不替代法务意见）。
若 app.integration.llm.complete 可用，通知类正文可经大模型润色；否则使用模板正文。
"""
import io
import re
import zipfile
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import COMPANY
from app.core.response import BizError
from app.core.storage import file_brief, save_bytes
from app.core.timeutil import today
from app.models import HrDocument, HrEmployee, SysFile, SysUser

DISCLAIMER = "以上风险点由规则库自动识别，仅作提示，不替代法务意见；正式签署前请法务或律师审核。"
COMPANY_NAME = COMPANY["name"]

# ---------------- 文书类型目录 ----------------
DOC_TYPES: dict[str, dict] = {
    "labor_contract": {"name": "劳动合同", "group": "入离职", "fields": [
        "name", "dept", "position", "work_location", "contract_start", "contract_years", "probation_months",
        "salary", "probation_salary", "working_hours", "overtime_clause", "non_compete", "non_compete_compensation"]},
    "nda": {"name": "保密协议", "group": "入离职", "fields": ["name", "dept", "position", "non_compete",
                                                          "non_compete_compensation", "non_compete_months", "penalty"]},
    "onboard_notice": {"name": "入职须知", "group": "入离职", "fields": ["name", "dept", "position", "report_date",
                                                                   "work_location", "contact"]},
    "handover": {"name": "工作交接清单", "group": "入离职", "fields": ["name", "dept", "position", "receiver", "handover_date",
                                                                  "items"]},
    "leave_cert": {"name": "离职证明", "group": "入离职", "fields": ["name", "dept", "position", "hire_date", "leave_date",
                                                               "leave_reason", "remark"]},
    "termination": {"name": "解除劳动合同协议", "group": "入离职", "fields": [
        "name", "dept", "position", "leave_date", "termination_type", "termination_reason", "legal_basis",
        "compensation", "salary_settle_date"]},
    "offer": {"name": "录用通知书（Offer）", "group": "招聘", "fields": [
        "name", "dept", "position", "salary", "probation_months", "contract_years", "report_date", "work_location",
        "valid_until"]},
    "salary_adjust": {"name": "调薪通知", "group": "通知", "fields": ["name", "dept", "position", "old_salary",
                                                                  "new_salary", "effective_date", "reason", "agreed"]},
    "holiday_notice": {"name": "放假通知", "group": "通知", "fields": ["holiday", "start_date", "end_date", "makeup_days",
                                                                   "duty_arrangement"]},
    "care_notice": {"name": "员工关怀通知", "group": "通知", "fields": ["topic", "date", "content"]},
    "event_notice": {"name": "年会/团建通知", "group": "通知", "fields": ["topic", "date", "location", "content", "budget"]},
    "pip_notice": {"name": "绩效改进通知", "group": "绩效", "fields": ["name", "dept", "position", "issues", "goals",
                                                                  "start_date", "end_date", "coach"]},
    "perf_plan": {"name": "绩效方案 / KPI 指标表", "group": "绩效", "fields": ["dept", "position", "period", "kpis"]},
    "training_plan": {"name": "培训方案", "group": "培训", "fields": ["topic", "audience", "date", "trainer", "objectives",
                                                                 "modules"]},
    "course_outline": {"name": "课件大纲", "group": "培训", "fields": ["topic", "audience", "duration", "modules"]},
    "training_survey": {"name": "培训问卷", "group": "培训", "fields": ["topic", "date"]},
    "training_summary": {"name": "培训总结", "group": "培训", "fields": ["topic", "date", "participants", "score",
                                                                    "highlights", "improvements"]},
    "policy": {"name": "人事制度 / 员工手册章节", "group": "制度", "fields": ["topic", "content"]},
    "minutes": {"name": "会议纪要", "group": "会议", "fields": ["topic", "date", "attendees", "raw_text"]},
    "jd": {"name": "岗位说明书（JD）", "group": "招聘", "fields": ["position", "dept", "skills", "education", "years"]},
}

ONBOARD_PACK = ["labor_contract", "nda", "onboard_notice", "handover"]

FIELD_LABELS = {
    "name": "员工姓名", "dept": "部门", "position": "岗位", "work_location": "工作地点", "contract_start": "合同起始日",
    "contract_years": "合同期限（年，0=无固定期限）", "probation_months": "试用期（月）", "salary": "转正月薪（元）",
    "probation_salary": "试用期月薪（元）", "working_hours": "工时制度", "overtime_clause": "加班约定",
    "non_compete": "是否约定竞业限制", "non_compete_compensation": "竞业补偿（元/月）", "non_compete_months": "竞业期限（月）",
    "penalty": "违约金约定", "report_date": "报到日期", "contact": "入职联系人", "receiver": "交接接收人",
    "handover_date": "交接日期", "items": "交接事项（每行一项）", "hire_date": "入职日期", "leave_date": "离职/解除日期",
    "leave_reason": "离职原因", "remark": "备注", "termination_type": "解除类型", "termination_reason": "解除理由",
    "legal_basis": "法律依据", "compensation": "经济补偿（元）", "salary_settle_date": "工资结算日",
    "valid_until": "Offer 有效期至", "old_salary": "原月薪", "new_salary": "调整后月薪", "effective_date": "生效日期",
    "reason": "调整原因", "agreed": "是否已与员工协商一致", "holiday": "节日", "start_date": "开始日期", "end_date": "结束日期",
    "makeup_days": "调休上班日", "duty_arrangement": "值班安排", "topic": "主题", "date": "日期", "content": "正文要点",
    "location": "地点", "budget": "预算", "issues": "待改进问题", "goals": "改进目标（每行一项）", "coach": "辅导人",
    "period": "考核周期", "kpis": "KPI（每行：指标|权重|目标值）", "audience": "培训对象", "trainer": "讲师",
    "objectives": "培训目标", "modules": "课程模块（每行一项）", "duration": "时长", "participants": "参训人数",
    "score": "满意度评分", "highlights": "亮点", "improvements": "改进项", "attendees": "参会人",
    "raw_text": "纪要原文", "skills": "关键技能", "education": "学历要求", "years": "工作年限",
}


def catalog() -> list[dict]:
    return [{"type": k, "name": v["name"], "group": v["group"],
             "fields": [{"key": f, "label": FIELD_LABELS.get(f, f)} for f in v["fields"]]} for k, v in DOC_TYPES.items()]


# ---------------- 参数 ----------------
def _num(v) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v).replace(",", "").replace("元", "").strip())
    except InvalidOperation:
        return None


def _truthy(v) -> bool:
    return v in (True, 1, "1", "true", "是", "yes", "Y", "y")


def _d(v) -> date | None:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def resolve_params(db: Session, doc_type: str, params: dict, user_id: int | None) -> dict:
    p = {k: v for k, v in (params or {}).items() if v not in (None, "")}
    if user_id:
        u = db.get(SysUser, user_id)
        e = db.scalar(select(HrEmployee).where(HrEmployee.user_id == user_id))
        if u:
            p.setdefault("name", u.name)
            p.setdefault("dept", u.department.name if u.department else "")
            p.setdefault("position", u.position or "")
        if e:
            p.setdefault("employee_no", e.employee_no)
            p.setdefault("gender", e.gender)
            p.setdefault("hire_date", e.hire_date.isoformat())
            if e.contract_start_date:
                p.setdefault("contract_start", e.contract_start_date.isoformat())
            if e.leave_date:
                p.setdefault("leave_date", e.leave_date.isoformat())
            if e.leave_reason:
                p.setdefault("leave_reason", e.leave_reason)
    if doc_type in ("labor_contract", "offer"):
        p.setdefault("contract_years", 3)
        p.setdefault("working_hours", "标准工时制")
    p.setdefault("date", today().isoformat())
    return p


# ---------------- 风险规则库 ----------------
def _risk(level: str, rule: str, message: str, basis: str) -> dict:
    return {"level": level, "rule": rule, "message": message, "basis": basis}


def probation_limit(contract_years: Decimal | None) -> int | None:
    """《劳动合同法》第十九条：<3 月 不得约定；3 月~<1 年 ≤1 月；1~<3 年 ≤2 月；≥3 年或无固定期限 ≤6 月。"""
    if contract_years is None:
        return None
    if contract_years == 0:
        return 6
    months = contract_years * 12
    if months < 3:
        return 0
    if months < 12:
        return 1
    if months < 36:
        return 2
    return 6


def check_risks(doc_type: str, p: dict) -> list[dict]:
    out: list[dict] = []
    years, prob = _num(p.get("contract_years")), _num(p.get("probation_months"))
    salary, psal = _num(p.get("salary")), _num(p.get("probation_salary"))
    if doc_type in ("labor_contract", "offer"):
        lim = probation_limit(years)
        if lim is not None and prob is not None and prob > lim:
            out.append(_risk("high", "probation_over_limit",
                             f"合同期限 {years:g} 年，试用期约定 {prob:g} 个月，超过法定上限 {lim} 个月" +
                             ("（合同不满 3 个月不得约定试用期）" if lim == 0 else ""),
                             "《劳动合同法》第十九条；违法约定的试用期已履行的，按转正工资标准支付赔偿金（第八十三条）"))
        if doc_type == "labor_contract" and salary and psal is not None and psal < salary * Decimal("0.8"):
            out.append(_risk("high", "probation_salary_low",
                             f"试用期工资 {psal:g} 元低于转正工资 {salary:g} 元的 80%（{salary * Decimal('0.8'):g} 元）",
                             "《劳动合同法》第二十条：试用期工资不得低于约定工资的 80%，且不低于当地最低工资"))
        if not p.get("work_location"):
            out.append(_risk("medium", "missing_location", "未写明工作地点",
                             "《劳动合同法》第十七条：工作内容和工作地点为劳动合同必备条款"))
        if not p.get("position"):
            out.append(_risk("medium", "missing_position", "未写明工作岗位/工作内容", "《劳动合同法》第十七条"))
    if doc_type == "labor_contract":
        if not p.get("contract_start"):
            out.append(_risk("medium", "missing_term", "未写明合同起始日期", "《劳动合同法》第十七条：劳动合同期限为必备条款"))
        if not p.get("overtime_clause"):
            out.append(_risk("low", "overtime_unspecified", "未约定加班审批与加班费/调休规则，易引发加班费争议",
                             "《劳动法》第四十四条；《工资支付暂行规定》第十三条"))
        if not salary:
            out.append(_risk("medium", "missing_salary", "未约定劳动报酬", "《劳动合同法》第十七条：劳动报酬为必备条款"))
    if doc_type in ("labor_contract", "nda") and _truthy(p.get("non_compete")):
        comp = _num(p.get("non_compete_compensation"))
        if not comp:
            out.append(_risk("high", "non_compete_no_comp", "约定了竞业限制但未约定离职后按月支付经济补偿",
                             "《劳动合同法》第二十三条；最高法劳动争议司法解释（一）第三十六条（未约定补偿可按前 12 月平均工资 30% 主张）"))
        months = _num(p.get("non_compete_months"))
        if months and months > 24:
            out.append(_risk("high", "non_compete_too_long", f"竞业限制期限 {months:g} 个月超过 2 年上限",
                             "《劳动合同法》第二十四条"))
        if p.get("position") and not re.search(r"经理|总监|主管|工程师|技术|研发|高管|方案|售前", str(p["position"])):
            out.append(_risk("low", "non_compete_scope", f"岗位「{p['position']}」可能不属于竞业限制适用人员",
                             "《劳动合同法》第二十四条：限于高级管理人员、高级技术人员和其他负有保密义务的人员"))
    if doc_type == "nda" and p.get("penalty") and not _truthy(p.get("non_compete")):
        out.append(_risk("medium", "penalty_scope", "保密协议约定了违约金，但未约定竞业限制；单纯违反保密义务约定违约金存在无效风险",
                          "《劳动合同法》第二十五条：仅服务期、竞业限制可约定由劳动者承担违约金"))
    if doc_type == "termination":
        ttype = str(p.get("termination_type") or "")
        if not p.get("termination_reason") or (ttype != "协商一致" and not p.get("legal_basis")):
            out.append(_risk("high", "termination_no_basis", "解除理由或法律依据缺失，单方解除可能被认定为违法解除（2N 赔偿金）",
                             "《劳动合同法》第三十六、三十九、四十、四十一、八十七条"))
        if ttype in ("协商一致", "无过失性辞退", "经济性裁员", "") and not _num(p.get("compensation")):
            out.append(_risk("medium", "termination_no_compensation", "未写明经济补偿金额（协商一致/无过失性辞退应按 N 支付）",
                             "《劳动合同法》第四十六、四十七条"))
        if ttype == "无过失性辞退":
            out.append(_risk("low", "notice_period", "无过失性辞退需提前 30 日书面通知或额外支付一个月工资（N+1）",
                             "《劳动合同法》第四十条"))
        if not p.get("salary_settle_date"):
            out.append(_risk("low", "salary_settle", "未写明工资结算日期，应在解除时一次付清工资", "《工资支付暂行规定》第九条"))
    if doc_type == "leave_cert":
        bad = [w for w in ("违纪", "开除", "旷工", "不胜任", "处分", "能力差", "表现不佳") if w in
               (str(p.get("leave_reason") or "") + str(p.get("remark") or ""))]
        if bad:
            out.append(_risk("high", "leave_cert_negative", f"离职证明含负面评价（{'、'.join(bad)}），可能影响再就业引发争议",
                             "《劳动合同法实施条例》第二十四条：离职证明应写明合同期限、解除日期、工作岗位、本单位工作年限"))
        if not p.get("leave_date"):
            out.append(_risk("medium", "leave_cert_date", "未写明劳动合同解除/终止日期", "《劳动合同法实施条例》第二十四条"))
    if doc_type == "salary_adjust":
        old, new = _num(p.get("old_salary")), _num(p.get("new_salary"))
        if old and new and new < old and not _truthy(p.get("agreed")):
            out.append(_risk("high", "salary_cut_unilateral", "降薪未与员工协商一致，单方降薪属变更劳动合同，员工可主张差额或解除并要求补偿",
                             "《劳动合同法》第三十五条、第三十八条"))
    if doc_type == "pip_notice":
        if not p.get("goals") or not p.get("end_date"):
            out.append(_risk("medium", "pip_unclear", "改进目标或改进期限不明确，后续以“不胜任”解除时举证不足",
                             "《劳动合同法》第四十条第（二）项：须经培训或调岗后仍不能胜任方可解除"))
        if any(w in str(p.get("issues") or "") for w in ("辞退", "解除", "开除")):
            out.append(_risk("medium", "pip_threat", "绩效改进通知中直接出现解除表述，建议改为客观描述绩效差距与支持措施",
                             "《劳动合同法》第四十条"))
    if doc_type == "offer" and not p.get("valid_until"):
        out.append(_risk("low", "offer_validity", "未写明 Offer 有效期，录用意向长期有效可能产生缔约过失责任",
                         "《民法典》第四百七十六条、第五百条"))
    if doc_type == "holiday_notice" and p.get("duty_arrangement"):
        out.append(_risk("low", "holiday_duty_pay", "法定节假日安排值班加班的，应支付不低于 300% 工资（不能以调休替代）",
                         "《劳动法》第四十四条第（三）项"))
    if doc_type == "event_notice" and re.search(r"必须参加|强制|扣.*工资|罚款", str(p.get("content") or "")):
        out.append(_risk("medium", "event_forced", "团建/年会通知含强制参与或扣款表述；非工作时间活动强制参加可能被认定为加班",
                         "《劳动法》第四十四条；《工资支付暂行规定》第十五条"))
    return out


# ---------------- docx 构建 ----------------
def _docx_base(title: str) -> Document:
    doc = Document()
    st = doc.styles["Normal"]
    st.font.name, st.font.size = "宋体", Pt(11)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    h = doc.add_paragraph()
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = h.add_run(title)
    r.bold, r.font.size, r.font.color.rgb = True, Pt(18), RGBColor(0x1D, 0x1F, 0x24)
    r.font.name = "黑体"
    r._element.rPr.rFonts.set(qn("w:eastAsia"), "黑体")
    return doc


def _h(doc, text):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.bold, r.font.size, r.font.color.rgb = True, Pt(12), RGBColor(0x1B, 0x32, 0x75)


def _p(doc, text, indent=True):
    for line in str(text).split("\n"):
        para = doc.add_paragraph(("　　" if indent else "") + line)
        para.paragraph_format.space_after = Pt(4)


def _table(doc, header: list[str], rows: list[list]):
    t = doc.add_table(rows=1, cols=len(header))
    t.style = "Table Grid"
    for i, h in enumerate(header):
        t.rows[0].cells[i].text = h
    for r in rows:
        cells = t.add_row().cells
        for i, v in enumerate(r):
            cells[i].text = "" if v is None else str(v)
    doc.add_paragraph()


def _sign(doc, left=f"甲方（盖章）：{COMPANY_NAME}", right="乙方（签字）："):
    doc.add_paragraph()
    _p(doc, f"{left}\n法定代表人或授权代表：\n日期：　　年　　月　　日", indent=False)
    doc.add_paragraph()
    _p(doc, f"{right}\n日期：　　年　　月　　日", indent=False)


def _lines(v) -> list[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [x.strip(" -·•\t") for x in str(v or "").splitlines() if x.strip(" -·•\t")]


def _end_date(start: date | None, years) -> str:
    y = _num(years)
    if not start or y is None:
        return "____年__月__日"
    if y == 0:
        return "无固定期限"
    return (start.replace(year=start.year + int(y)) - timedelta(days=1)).isoformat() if y == int(y) else \
        (start + timedelta(days=int(y * 365)) - timedelta(days=1)).isoformat()


def _polish(kind: str, text: str) -> tuple[str, str]:
    """大模型润色（可选）。返回 (正文, engine)。"""
    try:
        from app.integration.llm import complete  # type: ignore
    except ImportError:
        return text, "template"
    try:
        out = complete(f"你是{COMPANY_NAME}人事部文书助手。请在不改变事实、日期、金额的前提下润色{kind}正文，语气正式简洁，直接输出正文。",
                       text)
    except Exception:  # noqa: BLE001
        out = None
    return (out.strip(), "llm") if out else (text, "template")


def build(doc_type: str, p: dict) -> tuple[bytes, str, dict]:
    """返回 (docx bytes, 标题, extra)。"""
    meta = DOC_TYPES[doc_type]
    name = p.get("name") or ""
    title = meta["name"] if doc_type not in ("event_notice", "care_notice", "policy", "training_plan", "minutes") \
        else f"{p.get('topic') or meta['name']}"
    extra: dict = {"engine": "template"}
    doc = _docx_base(title if doc_type != "event_notice" else f"关于举办{p.get('topic') or '年会'}的通知")
    cs = _d(p.get("contract_start"))
    if doc_type == "labor_contract":
        _p(doc, f"甲方（用人单位）：{COMPANY_NAME}\n乙方（劳动者）：{name}　　工号：{p.get('employee_no', '')}", indent=False)
        _p(doc, "根据《中华人民共和国劳动法》《中华人民共和国劳动合同法》等法律法规，甲乙双方遵循合法、公平、平等自愿、协商一致、诚实信用的原则，订立本合同。")
        _h(doc, "第一条 合同期限")
        _p(doc, f"本合同为{'无固定期限' if _num(p.get('contract_years')) == 0 else '固定期限'}劳动合同，自 {p.get('contract_start') or '____年__月__日'} 起至 "
                f"{_end_date(cs, p.get('contract_years'))} 止。其中试用期 {p.get('probation_months', '__')} 个月。")
        _h(doc, "第二条 工作内容和工作地点")
        _p(doc, f"乙方在 {p.get('dept', '')} 担任 {p.get('position') or '________'} 岗位；工作地点：{p.get('work_location') or '________'}。"
                "甲方因经营需要调整岗位或地点的，应与乙方协商一致。")
        _h(doc, "第三条 工作时间和休息休假")
        _p(doc, f"实行{p.get('working_hours', '标准工时制')}，每日工作 8 小时、每周 40 小时。"
                f"{p.get('overtime_clause') or '加班须经甲方事先审批，甲方依法安排调休或支付加班费。'}")
        _h(doc, "第四条 劳动报酬")
        _p(doc, f"乙方试用期月工资 {p.get('probation_salary') or '______'} 元，转正后月工资 {p.get('salary') or '______'} 元，"
                "甲方于每月 10 日前以货币形式足额支付上月工资。")
        _h(doc, "第五条 社会保险和福利")
        _p(doc, "甲方依法为乙方缴纳社会保险和住房公积金，乙方个人缴纳部分由甲方代扣代缴。")
        _h(doc, "第六条 保密与竞业限制")
        _p(doc, "乙方应遵守甲方保密制度，具体以双方另行签订的《保密协议》为准。" +
                (f"双方约定竞业限制，离职后甲方按月支付竞业补偿 {p.get('non_compete_compensation') or '______'} 元。"
                 if _truthy(p.get("non_compete")) else ""))
        _h(doc, "第七条 劳动合同的变更、解除和终止")
        _p(doc, "按《劳动合同法》相关规定执行。")
        _h(doc, "第八条 其他")
        _p(doc, "本合同一式两份，甲乙双方各执一份，具有同等法律效力。")
        _sign(doc)
    elif doc_type == "nda":
        _p(doc, f"甲方：{COMPANY_NAME}\n乙方：{name}（{p.get('dept', '')} {p.get('position', '')}）", indent=False)
        _h(doc, "一、保密信息范围")
        _p(doc, "包括但不限于客户资料、投标方案与报价、项目技术文档、源代码、财务数据、人事薪酬信息及甲方标明为保密的其他信息。")
        _h(doc, "二、保密义务")
        _p(doc, "乙方在职期间及离职后均不得披露、使用或允许他人使用保密信息；离职时应交还全部载有保密信息的资料与设备。")
        _h(doc, "三、竞业限制")
        if _truthy(p.get("non_compete")):
            _p(doc, f"乙方离职后 {p.get('non_compete_months', 12)} 个月内不得到与甲方有竞争关系的单位任职，甲方按月支付竞业补偿 "
                    f"{p.get('non_compete_compensation') or '______'} 元。")
        else:
            _p(doc, "本协议不约定竞业限制。")
        _h(doc, "四、违约责任")
        _p(doc, p.get("penalty") or "乙方违反保密义务给甲方造成损失的，应依法承担赔偿责任。")
        _sign(doc)
    elif doc_type == "onboard_notice":
        _p(doc, f"{name}：\n欢迎加入{COMPANY_NAME}！请于 {p.get('report_date') or '入职当日'} 9:00 前到 "
                f"{p.get('work_location') or '公司前台'} 报到，入职部门 {p.get('dept', '')}，岗位 {p.get('position', '')}。", indent=False)
        _h(doc, "一、报到需携带材料")
        for x in ["身份证原件及复印件 2 份", "学历/学位证书原件及复印件", "上家单位离职证明", "近期一寸免冠照片 2 张",
                  "本人名下银行卡（工资卡）", "入职体检报告（三个月内）"]:
            _p(doc, "· " + x, indent=False)
        _h(doc, "二、入职流程")
        _p(doc, "签订劳动合同与保密协议 → 加入企业微信、开通工作台账号 → 领取办公设备 → 部门负责人介绍岗位职责 → 参加新员工培训。")
        _h(doc, "三、考勤与制度")
        _p(doc, "工作时间 9:00-18:00，使用企业微信打卡；请假、加班、报销均通过企业微信审批。请在入职一周内阅读《员工手册》。")
        _p(doc, f"入职联系人：{p.get('contact') or '人事部 邓欣'}", indent=False)
    elif doc_type == "handover":
        _p(doc, f"移交人：{name}（{p.get('dept', '')} {p.get('position', '')}）　接收人：{p.get('receiver') or '______'}　"
                f"交接日期：{p.get('handover_date') or '______'}", indent=False)
        items = _lines(p.get("items")) or ["在办项目/任务清单及进度", "客户/供应商联系人与往来记录", "文档资料（电子/纸质）",
                                           "账号权限（企微、工作台、各系统）", "办公设备与固定资产", "借款/备用金/报销结清",
                                           "印章、钥匙、门禁卡"]
        _table(doc, ["序号", "交接事项", "交接说明", "接收人确认", "备注"], [[i, x, "", "", ""] for i, x in enumerate(items, 1)])
        _p(doc, "移交人签字：__________　接收人签字：__________　部门负责人签字：__________　人事部签字：__________", indent=False)
    elif doc_type == "leave_cert":
        _p(doc, f"兹证明 {name}（身份证号：____________）自 {p.get('hire_date') or '______'} 至 {p.get('leave_date') or '______'} "
                f"在我公司 {p.get('dept', '')} 担任 {p.get('position', '')} 职务，现已于 {p.get('leave_date') or '______'} 与我公司"
                f"解除（终止）劳动合同，并已办理完离职手续。" + (f"离职原因：{p.get('leave_reason')}。" if p.get("leave_reason") else ""))
        if p.get("remark"):
            _p(doc, p["remark"])
        _p(doc, "特此证明。")
        _p(doc, f"\n{COMPANY_NAME}（盖章）\n{today():%Y年%m月%d日}", indent=False)
    elif doc_type == "termination":
        _p(doc, f"甲方：{COMPANY_NAME}\n乙方：{name}（{p.get('dept', '')} {p.get('position', '')}）", indent=False)
        _p(doc, f"经双方确认，甲乙双方劳动合同于 {p.get('leave_date') or '______'} 解除，解除类型：{p.get('termination_type') or '______'}。")
        _p(doc, f"解除理由：{p.get('termination_reason') or '______'}；依据：{p.get('legal_basis') or '______'}。")
        _p(doc, f"甲方于 {p.get('salary_settle_date') or '______'} 前结清乙方工资，并支付经济补偿 {p.get('compensation') or '______'} 元。"
                "乙方应于解除日前完成工作交接。双方确认就劳动关系再无其他争议。")
        _sign(doc)
    elif doc_type == "offer":
        _p(doc, f"尊敬的 {name}：", indent=False)
        _p(doc, f"感谢您对{COMPANY_NAME}的关注。经综合评估，我们很高兴邀请您加入 {p.get('dept', '')}，担任 {p.get('position', '')}。")
        _table(doc, ["项目", "内容"], [["月薪（税前）", f"{p.get('salary') or '______'} 元"],
                                     ["合同期限", f"{p.get('contract_years', 3)} 年"],
                                     ["试用期", f"{p.get('probation_months', '__')} 个月（试用期工资不低于转正工资 80%）"],
                                     ["报到日期", p.get("report_date") or "______"],
                                     ["工作地点", p.get("work_location") or "______"],
                                     ["福利", "五险一金、带薪年假、节日福利、年度体检"]])
        _p(doc, f"请于 {p.get('valid_until') or '______'} 前回复确认，逾期未确认本通知自动失效。")
        _p(doc, f"\n{COMPANY_NAME} 人事部\n{today():%Y年%m月%d日}", indent=False)
    elif doc_type == "salary_adjust":
        body = (f"{name}（{p.get('dept', '')} {p.get('position', '')}）：\n经公司研究{('并与您协商一致' if _truthy(p.get('agreed')) else '')}，"
                f"自 {p.get('effective_date') or '______'} 起，您的月薪由 {p.get('old_salary') or '______'} 元调整为 "
                f"{p.get('new_salary') or '______'} 元。调整原因：{p.get('reason') or '年度调薪'}。\n请严格保守薪酬信息。")
        body, extra["engine"] = _polish("调薪通知", body)
        _p(doc, body, indent=False)
        _p(doc, f"\n{COMPANY_NAME} 人事部\n{today():%Y年%m月%d日}\n\n员工确认签字：__________", indent=False)
    elif doc_type == "holiday_notice":
        body = (f"全体员工：\n根据国务院办公厅节假日安排，结合公司实际，{p.get('holiday') or '节日'}放假安排如下："
                f"{p.get('start_date') or '__'} 至 {p.get('end_date') or '__'} 放假" +
                (f"，{p.get('makeup_days')} 正常上班（调休）" if p.get("makeup_days") else "") + "。\n" +
                (f"值班安排：{p['duty_arrangement']}。\n" if p.get("duty_arrangement") else "") +
                "放假前请各部门做好安全检查，关闭电源门窗；项目现场人员按甲方要求做好保障。祝大家节日愉快！")
        body, extra["engine"] = _polish("放假通知", body)
        _p(doc, body, indent=False)
        _p(doc, f"\n{COMPANY_NAME} 人事部\n{today():%Y年%m月%d日}", indent=False)
    elif doc_type in ("care_notice", "event_notice"):
        body = ("全体员工：\n" + (f"为丰富员工生活、增强团队凝聚力，公司定于 {p.get('date') or '__'} 在 {p.get('location') or '__'} 举办"
                                 f"{p.get('topic') or '年会'}。" if doc_type == "event_notice" else
                                 f"{p.get('topic') or '员工关怀'}：") +
                f"\n{p.get('content') or '具体安排另行通知。'}" + (f"\n活动预算：{p['budget']}。" if p.get("budget") else "") +
                "\n请各部门提前安排好工作，如有疑问请联系人事部。")
        body, extra["engine"] = _polish(DOC_TYPES[doc_type]["name"], body)
        _p(doc, body, indent=False)
        _p(doc, f"\n{COMPANY_NAME} 人事部\n{today():%Y年%m月%d日}", indent=False)
    elif doc_type == "pip_notice":
        _p(doc, f"{name}（{p.get('dept', '')} {p.get('position', '')}）：", indent=False)
        _p(doc, f"经绩效评估，您在以下方面与岗位要求存在差距：{p.get('issues') or '______'}。为帮助您提升绩效，公司制定本改进计划，"
                f"改进期自 {p.get('start_date') or '______'} 至 {p.get('end_date') or '______'}，辅导人：{p.get('coach') or '部门负责人'}。")
        _table(doc, ["序号", "改进目标", "衡量标准", "完成时间", "评估结果"],
               [[i, g, "", p.get("end_date") or "", ""] for i, g in enumerate(_lines(p.get("goals")) or ["______"], 1)])
        _p(doc, "改进期内公司将提供必要的培训与辅导，期满后进行评估。员工签收：__________　日期：__________", indent=False)
    elif doc_type == "perf_plan":
        _p(doc, f"适用部门/岗位：{p.get('dept', '')} {p.get('position', '')}；考核周期：{p.get('period') or '季度'}", indent=False)
        _h(doc, "一、考核原则")
        _p(doc, "目标导向、量化可衡量、过程沟通与结果反馈并重；考核结果应用于绩效奖金、调薪、晋升与培训。")
        _h(doc, "二、KPI 指标表")
        kpis = [x.split("|") for x in _lines(p.get("kpis"))] or [["回款完成率", "30%", "≥100%"], ["项目按期交付率", "25%", "≥95%"],
                                                                ["客户满意度", "20%", "≥90 分"], ["过程管理与协作", "15%", "无重大投诉"],
                                                                ["学习成长", "10%", "完成 2 门课程"]]
        _table(doc, ["序号", "指标", "权重", "目标值", "评分标准", "得分"],
               [[i, *(k + ["", ""])[:3], "达成 100% 得满分，按比例折算", ""] for i, k in enumerate(kpis, 1)])
        _h(doc, "三、等级与应用")
        _p(doc, "S（≥95）、A（85-94）、B（70-84）、C（60-69）、D（<60）；连续两期 C 及以下启动绩效改进计划。")
        _h(doc, "四、绩效沟通话术参考")
        for x in ["开场：先肯定本期具体贡献，再说明面谈目的。", "差距：用数据描述结果与目标差距，避免评价人格。",
                  "原因：请员工先自我分析，管理者补充资源与流程因素。", "计划：共同确定 2-3 个可衡量的改进目标与支持措施。"]:
            _p(doc, "· " + x, indent=False)
    elif doc_type in ("training_plan", "course_outline"):
        _p(doc, f"培训主题：{p.get('topic', '')}　培训对象：{p.get('audience') or '全体员工'}　"
                f"时间/时长：{p.get('date') or p.get('duration') or '______'}　讲师：{p.get('trainer') or '______'}", indent=False)
        if doc_type == "training_plan":
            _h(doc, "一、培训目标")
            _p(doc, p.get("objectives") or "提升岗位专业能力与协作效率。")
        _h(doc, "二、课程模块" if doc_type == "training_plan" else "课件大纲")
        mods = _lines(p.get("modules")) or ["背景与目标", "核心知识讲解", "案例分析", "实操演练", "总结与考核"]
        _table(doc, ["序号", "模块", "要点", "时长", "形式"], [[i, m, "", "", "讲授/研讨"] for i, m in enumerate(mods, 1)])
        if doc_type == "training_plan":
            _h(doc, "三、组织与评估")
            _p(doc, "人事部负责组织与签到，培训后发放满意度问卷，一周内完成培训总结；考核结果纳入个人学习档案。")
    elif doc_type == "training_survey":
        _p(doc, f"培训主题：{p.get('topic', '')}　日期：{p.get('date', '')}（匿名填写）", indent=False)
        qs = ["课程内容与工作的相关度", "讲师表达与互动", "课程时长安排", "案例与实操的实用性", "培训组织与场地", "整体满意度"]
        _table(doc, ["序号", "评价项", "非常满意", "满意", "一般", "不满意"], [[i, q, "□", "□", "□", "□"] for i, q in enumerate(qs, 1)])
        _p(doc, "您最大的收获是：____________________\n希望改进的地方：____________________\n后续希望参加的培训主题：____________________",
           indent=False)
    elif doc_type == "training_summary":
        _p(doc, f"培训主题：{p.get('topic', '')}　日期：{p.get('date', '')}　参训人数：{p.get('participants') or '__'}　"
                f"满意度：{p.get('score') or '__'}", indent=False)
        _h(doc, "一、培训概况")
        _p(doc, f"本次培训围绕「{p.get('topic', '')}」展开，按计划完成全部课程模块。")
        _h(doc, "二、亮点")
        _p(doc, p.get("highlights") or "课程案例贴近项目实际，学员参与度高。")
        _h(doc, "三、改进项")
        _p(doc, p.get("improvements") or "实操时间偏少，下期增加分组演练。")
        _h(doc, "四、后续计划")
        _p(doc, "跟踪学员一个月内在岗应用情况，纳入季度培训复盘。")
    elif doc_type == "policy":
        body = p.get("content") or "第一条 目的\n第二条 适用范围\n第三条 职责\n第四条 管理规定\n第五条 附则"
        body, extra["engine"] = _polish("人事制度", body)
        _p(doc, body, indent=False)
        _p(doc, f"\n本制度由人事部负责解释，自发布之日起施行。\n{COMPANY_NAME}　{today():%Y年%m月%d日}", indent=False)
    elif doc_type == "minutes":
        items = extract_actions(p.get("raw_text") or "", p.get("_names") or [], _d(p.get("date")) or today())
        extra["actions"] = items
        _p(doc, f"会议主题：{p.get('topic') or '______'}\n会议时间：{p.get('date') or '______'}\n参会人：{p.get('attendees') or '______'}",
           indent=False)
        _h(doc, "一、会议要点")
        for s in _sentences(p.get("raw_text") or "")[:12]:
            _p(doc, "· " + s, indent=False)
        _h(doc, "二、行动项")
        _table(doc, ["序号", "事项", "责任人", "期限"], [[i, a["task"], a["owner"] or "待定", a["due"] or "待定"]
                                                     for i, a in enumerate(items, 1)] or [["—", "未识别到行动项", "", ""]])
    elif doc_type == "jd":
        from app.services import recruit_service
        jd = recruit_service.jd_text(p)
        extra["jd"] = jd
        for line in jd.split("\n"):
            if line.startswith("【"):
                _h(doc, line.strip("【】"))
            elif line.strip():
                _p(doc, line, indent=False)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue(), (f"{title}-{name}" if name and doc_type not in ("holiday_notice", "event_notice", "care_notice",
                                                                           "policy", "training_survey") else title), extra


# ---------------- 生成入口 ----------------
def generate(db: Session, doc_type: str, params: dict, user_id: int | None = None, created_by: int | None = None) -> dict:
    if doc_type not in DOC_TYPES:
        raise BizError(40001, f"不支持的文书类型 {doc_type}")
    p = resolve_params(db, doc_type, params, user_id)
    if doc_type == "minutes":
        p["_names"] = [u.name for u in db.scalars(select(SysUser).where(SysUser.status == 1)).all()]
    risks = check_risks(doc_type, p)
    data, title, extra = build(doc_type, p)
    p.pop("_names", None)
    fname = f"{title}_{today():%Y%m%d}.docx".replace("/", "／")
    f = save_bytes(db, data=data, filename=fname, category="hr_doc", key=f"hr_doc/{today():%Y-%m}/{doc_type}/{fname}",
                   uploader_id=created_by)
    row = HrDocument(doc_type=doc_type, title=title, target_user_id=user_id, params={**p, **{k: v for k, v in extra.items()
                                                                                              if k == "engine"}},
                     risk_hints=risks, file_id=f.id, created_by=created_by)
    db.add(row)
    db.flush()
    return {"document": doc_row(db, row, f), "risks": risks, "disclaimer": DISCLAIMER, **extra}


def onboarding_pack(db: Session, params: dict, user_id: int | None = None, created_by: int | None = None) -> dict:
    docs, risks = [], []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for t in ONBOARD_PACK:
            r = generate(db, t, params, user_id, created_by)
            docs.append(r["document"])
            f = db.get(SysFile, r["document"]["file"]["id"])
            from app.core.storage import read_bytes
            z.writestr(f.filename, read_bytes(f))
            for x in r["risks"]:
                risks.append({**x, "doc": DOC_TYPES[t]["name"]})
    p = resolve_params(db, "labor_contract", params, user_id)
    name = p.get("name") or "新员工"
    fname = f"入职资料包-{name}_{today():%Y%m%d}.zip"
    zf = save_bytes(db, data=buf.getvalue(), filename=fname, category="hr_doc",
                    key=f"hr_doc/{today():%Y-%m}/onboarding_pack/{fname}", uploader_id=created_by,
                    content_type="application/zip")
    row = HrDocument(doc_type="onboarding_pack", title=f"入职资料包-{name}", target_user_id=user_id,
                     params={**p, "documents": [d["id"] for d in docs]}, risk_hints=risks, file_id=zf.id,
                     created_by=created_by)
    db.add(row)
    db.flush()
    return {"package": doc_row(db, row, zf), "documents": docs, "risks": risks, "disclaimer": DISCLAIMER}


def doc_row(db: Session, d: HrDocument, f: SysFile | None = None) -> dict:
    f = f or (db.get(SysFile, d.file_id) if d.file_id else None)
    u = db.get(SysUser, d.target_user_id) if d.target_user_id else None
    c = db.get(SysUser, d.created_by) if d.created_by else None
    return {"id": d.id, "doc_type": d.doc_type, "type_name": DOC_TYPES.get(d.doc_type, {}).get("name", "入职资料包"),
            "title": d.title, "target_name": u.name if u else d.params.get("name"), "risk_count": len(d.risk_hints or []),
            "risk_hints": d.risk_hints, "file": file_brief(f), "created_by": c.name if c else None, "created_at": d.created_at}


# ---------------- 会议纪要行动项 ----------------
_ACTION_KW = ("负责", "完成", "跟进", "截止", "落实", "提交", "推进", "安排", "整理", "输出", "对接", "前")
_DUE_RE = re.compile(r"(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?|\d{1,2}月\d{1,2}[日号]|\d{1,2}[/-]\d{1,2}(?!\d)|"
                     r"(?:本|下)周[一二三四五六日天]|(?:本|下)?月底|月末|明天|后天|今天|周[一二三四五六日天])(?:之?前|截止)?")
_WD = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}


def _sentences(text: str) -> list[str]:
    parts = re.split(r"[。；;！!\n]+", text or "")
    return [re.sub(r"^\s*(\d+[.、)]|[-·•])\s*", "", s).strip() for s in parts if s.strip()]


def _norm_due(s: str, base: date) -> str:
    s = re.sub(r"(之?前|截止)$", "", s)
    m = re.match(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", s)
    try:
        if m:
            return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
        m = re.match(r"(\d{1,2})月(\d{1,2})", s) or re.match(r"(\d{1,2})[/-](\d{1,2})$", s)
        if m:
            d = date(base.year, int(m[1]), int(m[2]))
            return (d if d >= base - timedelta(days=30) else d.replace(year=base.year + 1)).isoformat()
    except ValueError:
        return s
    if s in ("今天",):
        return base.isoformat()
    if s == "明天":
        return (base + timedelta(days=1)).isoformat()
    if s == "后天":
        return (base + timedelta(days=2)).isoformat()
    if "月底" in s or "月末" in s:
        from app.core.timeutil import month_range
        y, mth = base.year, base.month + (1 if s.startswith("下") else 0)
        y, mth = y + (mth - 1) // 12, (mth - 1) % 12 + 1
        return month_range(f"{y}-{mth:02d}")[1].isoformat()
    m = re.search(r"(本|下)?周([一二三四五六日天])", s)
    if m:
        monday = base - timedelta(days=base.weekday())
        d = monday + timedelta(days=_WD[m[2]] + (7 if m[1] == "下" else 0))
        if not m[1] and d < base:
            d += timedelta(days=7)
        return d.isoformat()
    return s


def extract_actions(text: str, names: list[str], base: date) -> list[dict]:
    out = []
    names = sorted(set(names), key=len, reverse=True)
    for s in _sentences(text):
        if not any(k in s for k in _ACTION_KW):
            continue
        due_m = _DUE_RE.search(s)
        owner = None
        m = re.search(r"由\s*([一-龥]{2,3})\s*(?:负责|牵头|跟进|完成)", s) or \
            re.search(r"([一-龥]{2,3})\s*(?:负责|牵头|跟进)", s) or \
            re.search(r"责任人[:：]\s*([一-龥]{2,4})", s)
        hit = next((n for n in names if n in s), None)
        if hit:
            owner = hit
        elif m:
            owner = m.group(1)
        if not owner and not due_m and "负责" not in s and "跟进" not in s:
            continue
        out.append({"task": s, "owner": owner, "due": _norm_due(due_m.group(0), base) if due_m else None})
    return out
