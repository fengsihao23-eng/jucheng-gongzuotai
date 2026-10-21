"""招聘：JD 生成、简历批量初筛（关键词/学历/年限打分 + 理由）、面试评估、阶段流转、招聘漏斗。"""
import io
import re
from collections import Counter

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import COMPANY
from app.core.response import BizError
from app.models import HrCandidate

EDU_LEVEL = {"高中": 1, "中专": 1, "大专": 2, "专科": 2, "本科": 3, "学士": 3, "硕士": 4, "研究生": 4, "博士": 5}
EDU_NAME = {1: "高中及以下", 2: "大专", 3: "本科", 4: "硕士", 5: "博士"}
STAGES = [("resume", "简历"), ("screen_pass", "初筛通过"), ("interview", "面试"), ("offer", "Offer"),
          ("hired", "已入职"), ("rejected", "淘汰")]
STAGE_NAME = dict(STAGES)
TRANSITIONS = {"resume": {"screen_pass", "rejected"}, "screen_pass": {"interview", "rejected"},
               "interview": {"offer", "rejected"}, "offer": {"hired", "rejected"}, "hired": set(),
               "rejected": {"resume"}}
EVAL_DIMS = [("professional", "专业能力", 0.35), ("communication", "沟通表达", 0.2), ("teamwork", "团队协作", 0.15),
             ("learning", "学习能力", 0.15), ("culture", "价值观匹配", 0.15)]

SKILL_LIB = ["Java", "Python", "Vue", "React", "SQL", "Linux", "网络", "交换机", "路由", "综合布线", "弱电", "安防", "海康",
             "大华", "视频监控", "门禁", "智慧校园", "数字校园", "项目管理", "PMP", "一建", "二建", "CAD", "Visio", "招投标",
             "标书", "售前", "方案", "客户沟通", "运维", "服务器", "虚拟化", "数据库", "Excel", "PPT", "财务", "人力资源",
             "招聘", "薪酬", "劳动法", "市场推广", "政府客户", "教育行业", "商务谈判", "施工管理", "系统集成"]

JD_TEMPLATES = {
    "项目经理": {"duty": ["负责数字校园/安防系统集成项目的计划、进度、质量、成本与安全管理",
                       "组织深化设计、采购、到货验收、实施与终验，按标准控制流程推进节点",
                       "对接甲方与监理，处理变更与签证，推动回款", "管理项目团队与分包，按周汇报项目进展"],
             "req": ["熟悉弱电智能化/系统集成项目全流程", "持有一建/二建（机电）或 PMP 证书优先", "具备良好的沟通协调与抗压能力"]},
    "售前工程师": {"duty": ["负责招标文件解读、技术方案与投标文件编制", "配合销售完成客户交流、方案讲解与演示",
                         "维护方案模板库与产品资料", "参与标书合规检查与澄清答疑"],
               "req": ["熟悉智慧校园/安防行业产品与解决方案", "文字功底扎实，熟练使用 Word/PPT/Visio", "有教育行业投标经验优先"]},
    "项目工程师": {"duty": ["负责现场设备安装调试、网络配置与系统联调", "编制施工记录与竣工资料", "处理质保期内的故障与巡检"],
               "req": ["熟悉网络、交换机、视频监控、门禁等设备调试", "能适应短期出差", "有弱电施工经验优先"]},
}


def jd_text(p: dict) -> str:
    pos = str(p.get("position") or "岗位")
    tpl = next((v for k, v in JD_TEMPLATES.items() if k in pos), None) or {
        "duty": [f"负责{pos}相关工作的计划与执行", "与相关部门协同推进业务目标达成", "完成上级交办的其他工作"],
        "req": ["具备良好的沟通协调能力与责任心", "能熟练使用常用办公软件"]}
    skills = [s for s in re.split(r"[,，、;；\s]+", str(p.get("skills") or "")) if s]
    edu = p.get("education") or "本科"
    years = p.get("years") or 3
    lines = [f"【岗位名称】", f"{pos}（{p.get('dept') or '相关部门'}）",
             "【公司简介】", COMPANY["intro"],
             "【岗位职责】", *[f"{i}. {d}" for i, d in enumerate(tpl["duty"], 1)],
             "【任职要求】", f"1. {edu}及以上学历，{years} 年以上相关工作经验",
             *[f"{i}. {r}" for i, r in enumerate(tpl["req"], 2)]]
    if skills:
        lines.append(f"{len(tpl['req']) + 2}. 关键技能：{'、'.join(skills)}")
    lines += ["【我们提供】", "五险一金、带薪年假、项目奖金、节日福利、年度体检、完善的培训与晋升通道"]
    return "\n".join(lines)


def generate_jd(p: dict) -> dict:
    text = jd_text(p)
    engine = "template"
    try:
        from app.integration.llm import complete  # type: ignore
        out = complete("你是资深 HR，请润色以下招聘 JD，保留结构与事实，输出纯文本。", text)
        if out:
            text, engine = out.strip(), "llm"
    except ImportError:
        pass
    except Exception:  # noqa: BLE001
        pass
    skills = [s for s in re.split(r"[,，、;；\s]+", str(p.get("skills") or "")) if s]
    return {"text": text, "engine": engine,
            "requirements": {"position": p.get("position"), "keywords": skills, "min_education": p.get("education") or "本科",
                             "min_years": int(p.get("years") or 3)}}


# ---------------- 简历解析 ----------------
def _edu_of(text: str) -> str | None:
    best = 0
    for k, v in EDU_LEVEL.items():
        if k in text:
            best = max(best, v)
    return EDU_NAME.get(best) if best else None


def _years_of(text: str) -> int | None:
    m = re.search(r"(\d{1,2})\s*年(?:以上)?(?:工作|相关|行业|项目)?经验", text) or re.search(r"工作(?:年限|经验)[:：]?\s*(\d{1,2})", text)
    return int(m.group(1)) if m else None


def _skills_of(text: str, extra: list[str]) -> list[str]:
    low = text.lower()
    return [s for s in dict.fromkeys([*SKILL_LIB, *extra]) if s and s.lower() in low]


def parse_resume_text(text: str, filename: str, keywords: list[str]) -> dict:
    m = re.search(r"姓\s*名[:：]\s*([一-龥A-Za-z·]{2,10})", text)
    name = m.group(1) if m else re.sub(r"\.(txt|docx)$", "", filename).split("_")[0].split("-")[0][:10]
    ch = re.search(r"(?:来源|渠道)[:：]\s*(\S+)", text)
    return {"name": name, "education": _edu_of(text), "years": _years_of(text), "skills": _skills_of(text, keywords),
            "channel": ch.group(1)[:32] if ch else None, "text": text}


def parse_upload(filename: str, content: bytes, keywords: list[str]) -> list[dict]:
    fn = filename.lower()
    if fn.endswith(".txt"):
        for enc in ("utf-8", "gbk"):
            try:
                return [parse_resume_text(content.decode(enc), filename, keywords)]
            except UnicodeDecodeError:
                continue
        raise BizError(42201, f"{filename} 编码无法识别")
    if fn.endswith(".docx"):
        from docx import Document
        d = Document(io.BytesIO(content))
        text = "\n".join(p.text for p in d.paragraphs) + "\n" + "\n".join(
            c.text for t in d.tables for r in t.rows for c in r.cells)
        return [parse_resume_text(text, filename, keywords)]
    if fn.endswith((".xlsx", ".xlsm")):
        wb = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
        ws = wb.worksheets[0]
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []
        hdr = [str(c or "").strip() for c in rows[0]]

        def col(*names):
            return next((i for i, h in enumerate(hdr) if any(n in h for n in names)), None)
        ci = {"name": col("姓名"), "edu": col("学历"), "years": col("年限", "经验", "工龄"), "skills": col("技能", "关键词"),
              "channel": col("渠道", "来源"), "summary": col("简介", "经历", "摘要", "备注")}
        out = []
        for r in rows[1:]:
            if not r or ci["name"] is None or not r[ci["name"]]:
                continue
            get = lambda k: (r[ci[k]] if ci[k] is not None and ci[k] < len(r) else None)  # noqa: E731
            text = " ".join(str(x) for x in r if x is not None)
            yrs = get("years")
            try:
                yrs = int(float(re.sub(r"[^\d.]", "", str(yrs)))) if yrs not in (None, "") else _years_of(text)
            except ValueError:
                yrs = _years_of(text)
            out.append({"name": str(get("name")).strip()[:32], "education": _edu_of(str(get("edu") or "")) or _edu_of(text),
                        "years": yrs, "skills": _skills_of(text, keywords), "channel": str(get("channel") or "")[:32] or None,
                        "text": text})
        wb.close()
        return out
    raise BizError(42201, f"{filename}：仅支持 txt / docx 简历或 xlsx 候选人清单（PDF 请先另存为 docx）")


# ---------------- 打分 ----------------
def score(c: dict, req: dict) -> tuple[int, str]:
    kws = [k for k in req.get("keywords") or [] if k]
    reasons = []
    text_low = (c.get("text") or " ".join(c.get("skills") or [])).lower()
    hit = [k for k in kws if k.lower() in text_low or k in (c.get("skills") or [])]
    s_skill = round(50 * len(hit) / len(kws)) if kws else 30
    reasons.append(f"技能匹配 {len(hit)}/{len(kws)}（{'、'.join(hit) or '无'}）" if kws else "未设置技能关键词，按基础分计")
    need = EDU_LEVEL.get(str(req.get("min_education") or "本科").replace("及以上", ""), 3)
    have = max([v for k, v in EDU_LEVEL.items() if k in str(c.get("education") or "")] or [0])
    s_edu = 25 if have >= need else 12 if have == need - 1 else 0
    reasons.append(f"学历 {c.get('education') or '未知'}（要求{EDU_NAME.get(need)}及以上）{'达标' if have >= need else '不达标'}")
    ny = int(req.get("min_years") or 0)
    y = c.get("years")
    s_year = 25 if (y or 0) >= ny else round(25 * (y or 0) / ny) if ny else 25
    reasons.append(f"经验 {y if y is not None else '未知'} 年（要求 {ny} 年）{'达标' if (y or 0) >= ny else '不足'}")
    bonus = [k for k in req.get("bonus") or [] if k.lower() in text_low]
    total = min(100, s_skill + s_edu + s_year + 5 * len(bonus))
    if bonus:
        reasons.append(f"加分项：{'、'.join(bonus)}")
    return total, "；".join(reasons)


def screen(db: Session, files: list[tuple[str, bytes]], req: dict) -> dict:
    if not req.get("position"):
        raise BizError(40001, "请填写岗位名称")
    pass_score = int(req.get("pass_score") or 60)
    parsed, errors = [], []
    for fn, content in files:
        try:
            parsed += parse_upload(fn, content, req.get("keywords") or [])
        except BizError as e:
            errors.append({"file": fn, "message": e.message})
    out = []
    for c in parsed:
        sc, reason = score(c, req)
        row = db.scalar(select(HrCandidate).where(HrCandidate.name == c["name"], HrCandidate.position == req["position"]))
        if not row:
            row = HrCandidate(name=c["name"], position=req["position"], stage="resume", skills=[])
            db.add(row)
        row.channel = c.get("channel") or row.channel or "简历上传"
        row.education, row.years, row.skills = c.get("education"), c.get("years"), c.get("skills") or []
        row.screen_score, row.screen_reason = sc, reason
        if row.stage in ("resume", "screen_pass", "rejected"):
            row.stage = "screen_pass" if sc >= pass_score else "rejected"
        db.flush()
        out.append(cand_row(row))
    out.sort(key=lambda x: -(x["screen_score"] or 0))
    return {"position": req["position"], "pass_score": pass_score, "total": len(out),
            "passed": sum(1 for x in out if (x["screen_score"] or 0) >= pass_score), "candidates": out, "errors": errors}


def cand_row(c: HrCandidate) -> dict:
    return {"id": c.id, "name": c.name, "position": c.position, "channel": c.channel, "education": c.education,
            "years": c.years, "skills": c.skills or [], "stage": c.stage, "stage_name": STAGE_NAME.get(c.stage, c.stage),
            "screen_score": c.screen_score, "screen_reason": c.screen_reason, "interview_eval": c.interview_eval,
            "updated_at": c.updated_at}


def move_stage(c: HrCandidate, stage: str) -> None:
    if stage not in STAGE_NAME:
        raise BizError(40001, f"未知阶段 {stage}")
    if stage != c.stage and stage not in TRANSITIONS.get(c.stage, set()):
        raise BizError(40901, f"不能从「{STAGE_NAME[c.stage]}」流转到「{STAGE_NAME[stage]}」")
    c.stage = stage


def evaluate(scores: dict, interviewer: str | None, comment: str | None) -> dict:
    dims, total = [], 0.0
    for key, label, w in EVAL_DIMS:
        v = scores.get(key)
        if v is None:
            raise BizError(40001, f"请为「{label}」评分（1-5）")
        v = float(v)
        if not 1 <= v <= 5:
            raise BizError(40001, f"「{label}」评分须在 1-5 之间")
        dims.append({"key": key, "label": label, "weight": w, "score": v})
        total += v * w
    total = round(total / 5 * 100, 1)
    low = [d["label"] for d in dims if d["score"] <= 2]
    conclusion = "强烈推荐" if total >= 85 and not low else "推荐录用" if total >= 70 and not low else \
        "待定（建议复试）" if total >= 60 else "不推荐"
    return {"dims": dims, "total": total, "conclusion": conclusion, "weak": low, "interviewer": interviewer,
            "comment": comment}


def funnel(db: Session, position: str | None = None) -> dict:
    q = select(HrCandidate)
    if position:
        q = q.where(HrCandidate.position == position)
    cands = db.scalars(q).all()
    cnt = Counter(c.stage for c in cands)
    order = ["resume", "screen_pass", "interview", "offer", "hired"]
    reached = {s: sum(cnt[x] for x in order[i:]) for i, s in enumerate(order)}
    reached["resume"] = len(cands)
    steps = [{"stage": s, "name": STAGE_NAME[s], "count": reached[s], "current": cnt[s]} for s in order]
    for i in range(1, len(steps)):
        prev = steps[i - 1]["count"]
        steps[i]["conv"] = round(steps[i]["count"] / prev, 4) if prev else None
    positions = Counter(c.position for c in cands)
    return {"steps": steps, "rejected": cnt["rejected"], "total": len(cands),
            "positions": [{"position": k, "count": v} for k, v in positions.most_common()]}
