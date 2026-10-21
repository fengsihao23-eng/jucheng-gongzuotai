"""客服部（PRD 7.8）：问题登记自动化（分类/去重/关联）、辅助排查与建议回复、问题归因分析与报告。

- 分类：关键词规则库（sys_dict cs_category 可配，缺省用 DEFAULT_CATEGORY_RULES），按命中关键词长度加权取最高
- 去重：dedupe_hash = sha1(规范化客户 | 分类 | 规范化症状)；24h 内同指纹判定重复，合并到首单（duplicate_of）
- 相似工单：同分类候选，按 设备一致 + 症状重合 + 描述 bigram Jaccard 打分 top5（排除自身及自身的重复单）
- 排查建议：知识库（乐享客服手册）检索 + 相似已解决工单的解决方案，全部带出处
- 辅助回复：知识库“回复话术/排查步骤”规则拼装；LLM 可用时润色（未配置走规则）
"""
import hashlib
import io
import re
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.response import bad_request, not_found
from app.core.timeutil import TZ, now, today
from app.models import CsTicket, KbChunk, KbDocument, RptReport, SysUser

STATUS = {1: "登记", 2: "排查中", 3: "已解决", 4: "已归因"}
CHANNELS = ["电话", "微信", "邮件", "企微"]
ROOT_CAUSE_TYPES = ["配置", "硬件", "网络", "软件缺陷", "操作", "第三方"]

DEFAULT_CATEGORY_RULES: dict[str, list[str]] = {
    "显示设备": ["交互大屏", "大屏", "交互", "平板", "一体机", "投影", "班班通", "触控", "ops", "黑屏", "花屏", "无信号",
             "电子班牌", "班牌", "白板"],
    "音视频": ["广播", "音箱", "功放", "话筒", "麦克风", "录播", "无声", "打铃", "音响", "扩音", "杂音"],
    "安防监控": ["监控", "摄像", "录像", "nvr", "视频", "明厨亮灶", "周界", "报警", "画面", "后厨"],
    "门禁": ["门禁", "人脸", "闸机", "访客", "电锁", "刷脸", "开门"],
    "一卡通": ["一卡通", "消费机", "饭卡", "充值", "圈存", "扣款", "刷卡"],
    "网络": ["网络", "wifi", "无线", "ap", "交换机", "网速", "断网", "光纤", "上不了网", "掉线"],
    "平台软件": ["平台", "系统", "评价", "同步", "报表", "页面", "小程序", "数据", "体测", "成绩"],
    "账号权限": ["账号", "密码", "登录", "权限", "未授权", "验证码", "登不上", "登录不了", "无法登录", "登录失败", "密码错误"],
}
# 设备/系统识别（关键词 → 规范设备名，按顺序优先）
DEVICE_RULES: list[tuple[str, str]] = [
    ("明厨亮灶", "明厨亮灶"), ("交互大屏", "交互大屏"), ("班班通", "交互大屏"), ("智能平板", "交互大屏"), ("大屏", "交互大屏"),
    ("一体机", "交互大屏"), ("电子班牌", "电子班牌"), ("ops", "OPS电脑"), ("广播", "IP广播"), ("功放", "功放"),
    ("录播", "录播系统"), ("nvr", "NVR"), ("硬盘录像机", "NVR"), ("摄像", "摄像机"), ("监控", "摄像机"), ("闸机", "闸机"),
    ("门禁", "人脸门禁"), ("人脸", "人脸门禁"), ("消费机", "消费机"), ("圈存", "圈存机"), ("饭卡", "消费机"),
    ("一卡通", "一卡通"), ("wifi", "无线AP"), ("无线", "无线AP"), ("ap", "无线AP"), ("交换机", "交换机"),
    ("评价平台", "素质评价平台"), ("素质", "素质评价平台"), ("体测", "体测设备"), ("统一身份", "统一身份认证"),
]
SYMPTOMS: dict[str, list[str]] = {
    "无信号": ["无信号", "没信号", "no signal", "没画面", "不显示画面"],
    "黑屏": ["黑屏"], "花屏": ["花屏"], "触控失灵": ["触控", "触摸", "不能写", "点不动"],
    "无法开机": ["开不了机", "无法开机", "不开机", "开不开"],
    "无声": ["无声", "没声", "没有声音", "不响", "听不到"], "杂音": ["杂音", "噪音", "电流声"],
    "录像丢失": ["录像丢失", "没有录像", "无录像", "回放不了", "录像不全", "录像缺失"],
    "离线": ["离线", "掉线", "断线", "连不上", "无法连接", "断网", "上不了网"],
    "卡顿": ["卡顿", "很卡", "卡住", "延迟", "不流畅"],
    "识别失败": ["识别失败", "识别不了", "刷脸失败", "无法识别", "不识别", "识别不出"],
    "不开门": ["不开门", "打不开门", "门打不开"],
    "无法登录": ["无法登录", "登录不了", "登不上", "登录失败", "密码错误", "未授权"],
    "数据未同步": ["未同步", "不同步", "没同步", "同步失败", "数据缺失", "没有数据", "数据不全"],
    "扣款异常": ["扣款", "余额", "不到账"], "无法刷卡": ["刷不了", "刷卡失败", "无法刷卡", "刷卡没反应"],
    "网速慢": ["网速慢", "网很慢"], "误报": ["误报", "漏报"],
}
_ASCII_WORD = re.compile(r"[a-z0-9]+")
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")


# ---------------------------------------------------------------- 文本解析
def _contains(text: str, kw: str) -> bool:
    if kw.isascii():  # 英文关键词按词边界匹配（避免 "app" 命中 "ap"）
        return kw in _ASCII_WORD.findall(text)
    return kw in text


def category_rules(db: Session) -> dict[str, list[str]]:
    from app.services import dict_service
    custom = dict_service.items(db, "cs_category")
    if custom:
        return {k: list(v.get("keywords", v) if isinstance(v, dict) else v) for k, v in custom.items()}
    return DEFAULT_CATEGORY_RULES


def classify(text: str, rules: dict[str, list[str]] | None = None) -> str:
    t = (text or "").lower()
    best, best_score = "其他", 0
    for cat, kws in (rules or DEFAULT_CATEGORY_RULES).items():
        score = sum(len(k) for k in kws if _contains(t, k.lower()))
        if score > best_score:
            best, best_score = cat, score
    return best


def extract_device(text: str) -> str | None:
    t = (text or "").lower()
    for kw, name in DEVICE_RULES:
        if _contains(t, kw):
            return name
    return None


def extract_symptoms(text: str) -> list[str]:
    t = (text or "").lower()
    return [s for s, kws in SYMPTOMS.items() if any(k in t for k in kws)]


def _norm(s: str) -> str:
    return re.sub(r"[\s，,。.、：:；;！!？?“”\"'（）()【】\[\]-]", "", (s or "").lower())


def dedupe_hash(customer: str, category: str, desc: str) -> str:
    syms = extract_symptoms(desc)
    key = ",".join(sorted(syms)) if syms else _norm(desc)[:30]
    dev = extract_device(desc) or ""
    return hashlib.sha1(f"{_norm(customer)}|{category}|{dev}|{key}".encode()).hexdigest()


_PREFIX = re.compile(r"^(请|帮我|麻烦|帮忙)?(登记|记录|新建|录入)(一下)?(一个|一条|一张)?(客户|工单|问题)?[：:\s]*")
_SENTENCE = re.compile(r"^(?P<cust>.{2,40}?)(?:的)?(?:老师|信息中心|总务处|电教处|保卫处)?\s*"
                       r"(?:反馈|反映|报修|来电|致电|投诉|咨询|说)[：:，,\s]*(?P<prob>.+)$")
_LABELED = re.compile(r"客户[：:\s]*(?P<cust>[^，,；;。\n]+)[，,；;\s]+(?:问题|描述|现象)[：:\s]*(?P<prob>.+)")


def parse_text(db: Session, text: str) -> dict:
    """一句话登记解析：“登记客户 XX 学校反馈：教室大屏无信号”→ {customer_name, problem_desc, channel, contact}"""
    raw = (text or "").strip()
    out: dict = {}
    for ch in CHANNELS:
        if ch in raw:
            out["channel"] = ch
            break
    phone = _PHONE.search(raw)
    if phone:
        out["contact"] = phone.group()
    body = _PREFIX.sub("", raw)
    m = _LABELED.search(body) or _SENTENCE.match(body)
    if m:
        cust = re.sub(r"^(客户|单位|学校)[：:\s]*", "", m.group("cust")).strip(" ：:，,")
        cust = re.sub(r"(通过)?(电话|微信|邮件|企微)(来电|反馈)?$", "", cust).strip()
        known = db.scalars(select(CsTicket.customer_name).distinct()).all()
        full = sorted((k for k in known if k and cust and cust in k and k != cust), key=len)
        out["customer_name"] = full[0] if full else cust
        out["problem_desc"] = re.sub(r"^(说|称|表示)[：:，,\s]*", "", m.group("prob").strip())
    else:
        out["problem_desc"] = body
        known = db.scalars(select(CsTicket.customer_name).distinct()).all()
        hit = sorted((c for c in known if c and c in body), key=len, reverse=True)
        if hit:
            out["customer_name"] = hit[0]
    return out


# ---------------------------------------------------------------- 相似 / 去重 / 建议
def _bigrams(s: str) -> set[str]:
    s = _norm(s)
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}


def similarity(a: CsTicket | dict, b: CsTicket) -> float:
    desc_a = a["problem_desc"] if isinstance(a, dict) else a.problem_desc
    dev_a = a.get("device") if isinstance(a, dict) else a.device
    ba, bb = _bigrams(desc_a), _bigrams(b.problem_desc)
    jac = len(ba & bb) / len(ba | bb) if ba and bb else 0
    sa, sb = set(extract_symptoms(desc_a)), set(extract_symptoms(b.problem_desc))
    sym = len(sa & sb) / len(sa | sb) if sa and sb else 0
    dev = 1.0 if dev_a and dev_a == b.device else 0.0
    return round(0.4 * dev + 0.35 * sym + 0.25 * jac, 4)


def similar_tickets(db: Session, t: CsTicket, limit: int = 5) -> list[dict]:
    excl = {t.id}
    q = (select(CsTicket).where(CsTicket.category == t.category, CsTicket.id != t.id,
                                or_(CsTicket.duplicate_of.is_(None), CsTicket.duplicate_of != t.id))
         .order_by(CsTicket.created_at.desc()).limit(800))
    scored = []
    for c in db.scalars(q):
        if c.id in excl or (t.duplicate_of and c.id == t.duplicate_of):
            continue  # 排除自身与重复合并的首单（首单在 duplicate 字段单独展示）
        s = similarity(t, c)
        if s >= 0.2:
            scored.append((s + (0.05 if c.status >= 3 and c.solution else 0), s, c))
    scored.sort(key=lambda x: (-x[0], -x[2].id))
    return [{**brief(c), "similarity": s} for _, s, c in scored[:limit]]


def _kb_steps(db: Session, doc_id: int) -> tuple[list[str], str | None]:
    chunks = db.scalars(select(KbChunk).where(KbChunk.doc_id == doc_id).order_by(KbChunk.seq)).all()
    steps, script = [], None
    for c in chunks:
        h = c.heading or ""
        if "排查步骤" in h or ("排查" in h and not steps):
            for line in c.chunk_text.splitlines():
                m = re.match(r"^\s*\d+[\.、．]\s*(.+)$", line)
                if m:
                    steps.append(m.group(1).strip().rstrip("；;"))
        if "话术" in h and not script:
            script = c.chunk_text.strip()
    return steps, script


def build_suggestion(db: Session, t: CsTicket, similar: list[dict] | None = None) -> dict:
    from app.services import kb_service
    syms = extract_symptoms(t.problem_desc)
    query = " ".join(filter(None, [t.device, *syms, t.problem_desc[:60]]))
    kb = kb_service.search(db, query, category="客服", limit=3)
    if not kb:
        kb = kb_service.search(db, query, limit=3)
    kb_hit = bool(kb) and kb[0]["score"] >= 8
    steps, script, steps_src = [], None, None
    if kb:
        steps, script = _kb_steps(db, kb[0]["doc_id"])
        steps_src = {"title": kb[0]["title"], "url": kb[0]["url"], "doc_id": kb[0]["doc_id"]}
    similar = similar if similar is not None else similar_tickets(db, t)
    history = [{"ticket_no": s["ticket_no"], "customer_name": s["customer_name"], "solution": s["solution"],
                "root_cause": s["root_cause"], "root_cause_type": s["root_cause_type"], "similarity": s["similarity"],
                "id": s["id"]} for s in similar if s.get("solution")][:3]
    reply, engine = build_reply(t, steps, script, history, kb[0] if kb else None)
    return {"kb": [{k: h[k] for k in ("doc_id", "title", "category", "url", "heading", "snippet", "score")} for h in kb],
            "steps": steps[:6], "steps_source": steps_src, "history": history, "reply": reply,
            "reply_engine": engine, "kb_hit": kb_hit, "adopted": False,
            "generated_at": now().isoformat()}


def build_reply(t: CsTicket, steps: list[str], script: str | None, history: list[dict], kb_top: dict | None):
    """建议回复话术：知识库话术/步骤规则拼装 → LLM 润色（可选）。客服确认后复制发送。"""
    from app.integration import llm
    who = f"{t.customer_name}老师" if t.customer_name and "未注明" not in t.customer_name else "老师"
    subject = t.device or t.category
    lines = [f"{who}您好，关于您反馈的{subject}问题（{t.problem_desc[:40]}），我们已登记工单 {t.ticket_no}。"]
    if steps:
        lines.append("建议您先按以下步骤排查：")
        lines += [f"{i}. {s}" for i, s in enumerate(steps[:4], 1)]
    elif history:
        lines.append(f"同类问题以往的处理方式为：{history[0]['solution']}")
    if script:
        tip = re.sub(r"^(您好|你好)[，,]?", "", script.split("\n", 1)[-1].strip())
        lines.append("补充说明：" + tip[:120])
    lines.append("如按上述步骤仍未恢复，我们将安排工程师尽快到场处理，处理进展会及时通知您。")
    rule_text = "\n".join(lines)
    if not llm.enabled():
        return rule_text, "rule"
    src = (kb_top or {}).get("title", "")
    polished = llm.complete(
        "你是教育信息化公司的客服专员。请把给定的回复草稿润色为礼貌、简洁、口语化的中文回复，"
        "保留全部排查步骤与工单号，不得新增草稿中没有的技术结论或承诺。只输出回复正文。",
        f"参考手册：{src}\n回复草稿：\n{rule_text}")
    return (polished, "llm") if polished and t.ticket_no in polished else (rule_text, "rule")


# ---------------------------------------------------------------- 登记
def brief(t: CsTicket) -> dict:
    return {"id": t.id, "ticket_no": t.ticket_no, "customer_name": t.customer_name, "contact": t.contact,
            "channel": t.channel, "device": t.device, "problem_desc": t.problem_desc, "category": t.category,
            "symptoms": extract_symptoms(t.problem_desc), "status": t.status, "status_text": STATUS.get(t.status),
            "duplicate_of": t.duplicate_of, "root_cause": t.root_cause, "root_cause_type": t.root_cause_type,
            "solution": t.solution, "handler_id": t.handler_id, "created_at": t.created_at,
            "resolved_at": t.resolved_at, "related_ticket_ids": t.related_ticket_ids or []}


def find_duplicate(db: Session, t: CsTicket) -> CsTicket | None:
    since = (t.created_at or now()) - timedelta(hours=24)
    dup = db.scalar(select(CsTicket).where(CsTicket.dedupe_hash == t.dedupe_hash, CsTicket.id != t.id,
                                           CsTicket.created_at >= since, CsTicket.created_at <= (t.created_at or now()))
                    .order_by(CsTicket.created_at).limit(1))
    if dup and dup.duplicate_of:
        dup = db.get(CsTicket, dup.duplicate_of) or dup
    return dup


def register_ticket(db: Session, text: str | dict | None = None, fields: dict | SysUser | None = None,
                    user: SysUser | None = None, created_at: datetime | None = None, with_suggestion: bool = True) -> dict:
    """登记工单（工作台快速登记 / MCP cs.ticket_register 复用）。

    兼容调用：register_ticket(db, "一句话", user=u) / register_ticket(db, fields={...}) / register_ticket(db, {...}, u)
    返回 {ticket, ticket_no, summary, duplicate, similar, suggestion, parsed, elapsed_ms}。
    """
    from app.services import codes
    t0 = time.perf_counter()
    if isinstance(fields, SysUser):
        user, fields = fields, None
    if isinstance(text, dict):
        text, fields = text.get("text"), {**text, **(fields or {})}
        fields.pop("text", None)
    f = {k: v for k, v in (fields or {}).items() if v not in (None, "")}
    parsed = parse_text(db, text) if text else {}
    data = {**parsed, **f}
    desc = (data.get("problem_desc") or "").strip()
    if not desc:
        raise bad_request("请填写问题描述（或一句话：登记客户 XX 学校反馈：教室大屏无信号）")
    customer = (data.get("customer_name") or "未注明客户").strip()[:128]
    channel = data.get("channel") if data.get("channel") in CHANNELS else "电话"
    full_text = f"{data.get('device') or ''} {desc}"
    category = data.get("category") or classify(full_text, category_rules(db))
    device = (data.get("device") or extract_device(full_text) or None)
    created = created_at or now()
    tno = codes._next(db, CsTicket.ticket_no, f"KF-{created:%Y%m}-", 4) if created_at else codes.ticket_no(db)
    t = CsTicket(ticket_no=tno, customer_name=customer, contact=(data.get("contact") or None),
                 channel=channel, device=device[:64] if device else None, problem_desc=desc, category=category,
                 dedupe_hash=dedupe_hash(customer, category, full_text), related_ticket_ids=[], status=1,
                 handler_id=user.id if user else None, created_at=created)
    t.updated_at = t.created_at
    db.add(t)
    db.flush()
    dup = find_duplicate(db, t)
    if dup:
        t.duplicate_of = dup.id
        dup.related_ticket_ids = sorted({*(dup.related_ticket_ids or []), t.id})
    similar = similar_tickets(db, t)
    t.related_ticket_ids = sorted({*(t.related_ticket_ids or []), *(s["id"] for s in similar)})
    if with_suggestion:
        t.suggestion = build_suggestion(db, t, similar)
    db.flush()
    kb_top = (t.suggestion or {}).get("kb") or []
    summary = (f"已登记工单 {t.ticket_no}（{t.customer_name} · {t.category}{' · ' + t.device if t.device else ''}）"
               + (f"；判定为 24h 内重复报修，已合并到 {dup.ticket_no}" if dup else "")
               + (f"；相似历史工单 {len(similar)} 张" if similar else "")
               + (f"；排查建议参考《{kb_top[0]['title']}》{kb_top[0]['url']}" if kb_top else ""))
    return {"ticket": brief(t), "ticket_no": t.ticket_no, "summary": summary, "parsed": parsed,
            "duplicate": {"is_duplicate": bool(dup), "of": brief(dup) if dup else None},
            "similar": similar, "suggestion": t.suggestion,
            "elapsed_ms": int((time.perf_counter() - t0) * 1000)}


def refresh_suggestion(db: Session, t: CsTicket) -> dict:
    t.suggestion = build_suggestion(db, t)
    db.flush()
    return t.suggestion


def mark_reply_adopted(db: Session, t: CsTicket, adopted: bool = True) -> dict:
    sug = dict(t.suggestion or {})
    sug["adopted"] = adopted
    t.suggestion = sug
    db.flush()
    return sug


def update_status(db: Session, t: CsTicket, status: int, root_cause_type: str | None = None,
                  root_cause: str | None = None, solution: str | None = None, handler: SysUser | None = None) -> dict:
    if status not in STATUS:
        raise bad_request("状态不合法")
    if status == 3:
        if not (root_cause_type and solution):
            raise bad_request("标记已解决须填写根因类型与解决方案")
        if root_cause_type not in ROOT_CAUSE_TYPES:
            raise bad_request(f"根因类型须为：{'/'.join(ROOT_CAUSE_TYPES)}")
    if status == 4 and t.status < 3:
        raise bad_request("工单须先解决后再归因")
    t.status = status
    if root_cause_type:
        t.root_cause_type = root_cause_type
    if root_cause is not None:
        t.root_cause = root_cause[:255]
    if solution is not None:
        t.solution = solution
    if handler:
        t.handler_id = handler.id
    if status == 3 and not t.resolved_at:
        t.resolved_at = now()
    # 重复单随首单一起关闭
    if status >= 3:
        for child in db.scalars(select(CsTicket).where(CsTicket.duplicate_of == t.id, CsTicket.status < 3)):
            child.status, child.root_cause_type, child.root_cause = 3, t.root_cause_type, t.root_cause
            child.solution, child.resolved_at = f"随首单 {t.ticket_no} 一并解决：{t.solution}", t.resolved_at
    db.flush()
    return brief(t)


def detail(db: Session, t: CsTicket) -> dict:
    dups = db.scalars(select(CsTicket).where(CsTicket.duplicate_of == t.id).order_by(CsTicket.created_at)).all()
    related = db.scalars(select(CsTicket).where(CsTicket.id.in_(t.related_ticket_ids or [0]))).all()
    handler = db.get(SysUser, t.handler_id) if t.handler_id else None
    return {**brief(t), "handler_name": handler.name if handler else None, "suggestion": t.suggestion,
            "duplicate_parent": brief(db.get(CsTicket, t.duplicate_of)) if t.duplicate_of else None,
            "duplicates": [brief(d) for d in dups],
            "related": [{**brief(r), "similarity": similarity(t, r)} for r in related if r.id != t.id]}


def list_tickets(db: Session, status: int | None = None, category: str | None = None, keyword: str | None = None,
                 start: date | None = None, end: date | None = None, page: int = 1, page_size: int = 20,
                 only_open: bool = False) -> dict:
    q = select(CsTicket)
    if status:
        q = q.where(CsTicket.status == status)
    if only_open:
        q = q.where(CsTicket.status < 3)
    if category:
        q = q.where(CsTicket.category == category)
    if keyword:
        kw = f"%{keyword}%"
        q = q.where(or_(CsTicket.customer_name.ilike(kw), CsTicket.problem_desc.ilike(kw), CsTicket.ticket_no.ilike(kw)))
    if start:
        q = q.where(CsTicket.created_at >= datetime.combine(start, datetime.min.time(), TZ))
    if end:
        q = q.where(CsTicket.created_at < datetime.combine(end + timedelta(days=1), datetime.min.time(), TZ))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(CsTicket.created_at.desc(), CsTicket.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [brief(r) for r in rows], "total": total, "page": page, "page_size": page_size}


# ---------------------------------------------------------------- 统计 / 归因
def _range_rows(db: Session, start: date, end: date) -> list[CsTicket]:
    a = datetime.combine(start, datetime.min.time(), TZ)
    b = datetime.combine(end + timedelta(days=1), datetime.min.time(), TZ)
    return db.scalars(select(CsTicket).where(CsTicket.created_at >= a, CsTicket.created_at < b)
                      .order_by(CsTicket.created_at)).all()


def _hours(t: CsTicket) -> float | None:
    if t.resolved_at and t.created_at:
        return max((t.resolved_at - t.created_at).total_seconds() / 3600, 0)
    return None


def kpi(db: Session) -> dict:
    T = today()
    start = T.replace(day=1)
    rows = _range_rows(db, start, T)
    hours = [h for h in (_hours(t) for t in rows) if h is not None]
    with_reply = [t for t in rows if (t.suggestion or {}).get("reply")]
    return {"period": f"{T:%Y-%m}", "month_total": len(rows),
            "pending": db.scalar(select(func.count(CsTicket.id)).where(CsTicket.status < 3)) or 0,
            "avg_resolve_hours": round(sum(hours) / len(hours), 1) if hours else None,
            "duplicate_rate": round(sum(1 for t in rows if t.duplicate_of) / len(rows), 4) if rows else 0,
            "kb_hit_rate": round(sum(1 for t in rows if (t.suggestion or {}).get("kb_hit")) / len(rows), 4) if rows else 0,
            "reply_adopt_rate": round(sum(1 for t in with_reply if t.suggestion.get("adopted")) / len(with_reply), 4)
            if with_reply else 0}


SUGGESTION_RULES = [
    ("硬件", 0.3, "硬件类根因占比 {pct}，建议对高发设备（{top_dev}）开展批量巡检与预防性更换，评估是否纳入原厂质保换新，并补充现场备件。"),
    ("操作", 0.25, "操作类问题占比 {pct}，建议面向学校老师开展针对性培训，在教室张贴设备使用指引，并将排查步骤沉淀到乐享知识库供自动回复引用。"),
    ("网络", 0.25, "网络类根因占比 {pct}，建议复核 PoE 供电预算与交换机端口，台风/雨季前对室外链路和防雷做专项检查。"),
    ("配置", 0.2, "配置类问题占比 {pct}，建议建立学期初/节假日配置核对清单（作息表、人脸库下发、权限同步），变更后双人复核。"),
    ("软件缺陷", 0.15, "软件缺陷占比 {pct}，建议汇总缺陷清单反馈研发/厂商排期修复，并在修复前提供临时规避方案。"),
    ("第三方", 0.15, "第三方原因占比 {pct}，建议与运营商/厂家建立联合响应机制，明确责任边界与升级时限。"),
]


def attribution(db: Session, start: date, end: date) -> dict:
    rows = _range_rows(db, start, end)
    total = len(rows)
    clusters: dict[tuple, list[CsTicket]] = defaultdict(list)
    for t in rows:
        syms = extract_symptoms(t.problem_desc)
        clusters[(t.category, syms[0] if syms else (t.device or "其他"))].append(t)
    top = []
    for (cat, sym), items in sorted(clusters.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        devs = Counter(t.device for t in items if t.device)
        causes = Counter(t.root_cause_type for t in items if t.root_cause_type)
        top.append({"category": cat, "symptom": sym, "label": f"{(devs.most_common(1)[0][0] if devs else cat)}·{sym}",
                    "count": len(items), "share": round(len(items) / total, 4) if total else 0,
                    "resolved": sum(1 for t in items if t.status >= 3), "duplicates": sum(1 for t in items if t.duplicate_of),
                    "customers": len({t.customer_name for t in items}),
                    "top_device": devs.most_common(1)[0][0] if devs else None,
                    "top_root_cause": causes.most_common(1)[0][0] if causes else None,
                    "sample_solution": next((t.solution for t in reversed(items) if t.solution), None),
                    "ticket_nos": [t.ticket_no for t in items[-5:]]})
    top = top[:10]
    resolved = [t for t in rows if t.status >= 3]
    causes = Counter(t.root_cause_type or "未填写" for t in resolved)
    hours = [h for h in (_hours(t) for t in resolved) if h is not None]
    by_customer = Counter(t.customer_name for t in rows).most_common(10)
    by_device = Counter(t.device or "未识别" for t in rows).most_common(10)
    by_category = Counter(t.category for t in rows).most_common()
    dup_rate = round(sum(1 for t in rows if t.duplicate_of) / total, 4) if total else 0
    advice = []
    n_res = len(resolved) or 1
    top_dev = by_device[0][0] if by_device else "—"
    for cause, threshold, tpl in SUGGESTION_RULES:
        share = causes.get(cause, 0) / n_res
        if share >= threshold:
            advice.append({"rule": f"{cause}≥{int(threshold * 100)}%", "text": tpl.format(pct=f"{share:.0%}", top_dev=top_dev)})
    if dup_rate >= 0.08:
        advice.append({"rule": "重复率≥8%", "text": f"重复报修率 {dup_rate:.0%}，建议首次处理时同步告知客户预计恢复时间，并在问题未闭环前主动回访，减少重复来电。"})
    if hours and sum(hours) / len(hours) > 24:
        advice.append({"rule": "平均解决时长>24h", "text": "平均解决时长超过 24 小时，建议对需上门的工单按片区就近派单，并提高远程处理比例。"})
    for c in top[:3]:
        if c["count"] >= 3:
            advice.append({"rule": "高频问题", "text": f"高频问题「{c['label']}」{c['count']} 次，建议形成标准排查手册并纳入开学前专项检查；"
                                                    f"典型处理：{(c['sample_solution'] or '待补充')[:60]}"})
    if not advice:
        advice.append({"rule": "整体平稳", "text": "本期问题分布较分散，建议持续沉淀已解决工单经验到知识库。"})
    return {"start": start, "end": end, "total": total, "resolved": len(resolved),
            "avg_resolve_hours": round(sum(hours) / len(hours), 1) if hours else None,
            "duplicate_rate": dup_rate, "top10": top,
            "root_causes": [{"name": k, "count": v} for k, v in causes.most_common()],
            "by_customer": [{"name": k, "count": v} for k, v in by_customer],
            "by_device": [{"name": k, "count": v} for k, v in by_device],
            "by_category": [{"name": k, "count": v} for k, v in by_category], "advice": advice}


def _attribution_docx(title: str, a: dict) -> bytes:
    from docx import Document
    from docx.shared import Pt
    doc = Document()
    doc.styles["Normal"].font.size = Pt(10.5)
    doc.add_heading(title, 0)
    doc.add_paragraph(f"统计区间：{a['start']} 至 {a['end']}　|　工单 {a['total']} 张　|　已解决 {a['resolved']} 张　|　"
                      f"平均解决时长 {a['avg_resolve_hours'] if a['avg_resolve_hours'] is not None else '—'} 小时　|　"
                      f"重复率 {a['duplicate_rate']:.1%}")
    doc.add_heading("一、高频问题 Top10", 1)
    tb = doc.add_table(rows=1, cols=6)
    tb.style = "Light Grid Accent 1"
    for i, h in enumerate(["排名", "问题", "分类", "次数", "主要根因", "典型处理"]):
        tb.rows[0].cells[i].text = h
    for i, c in enumerate(a["top10"], 1):
        cells = tb.add_row().cells
        vals = [str(i), c["label"], c["category"], str(c["count"]), c["top_root_cause"] or "—",
                (c["sample_solution"] or "—")[:50]]
        for j, v in enumerate(vals):
            cells[j].text = v
    doc.add_heading("二、根因类型分布", 1)
    for r in a["root_causes"]:
        doc.add_paragraph(f"{r['name']}：{r['count']} 张", style="List Bullet")
    doc.add_heading("三、客户与设备分布（Top5）", 1)
    doc.add_paragraph("客户：" + "；".join(f"{r['name']} {r['count']}" for r in a["by_customer"][:5]))
    doc.add_paragraph("设备：" + "；".join(f"{r['name']} {r['count']}" for r in a["by_device"][:5]))
    doc.add_heading("四、改善建议", 1)
    for i, s in enumerate(a["advice"], 1):
        doc.add_paragraph(f"{i}. {s['text']}（触发规则：{s['rule']}）")
    doc.add_paragraph("口径说明：统计口径为登记时间落在区间内的全部工单；重复率=判定为 24 小时内同客户同症状重复报修的工单占比；"
                      "解决时长=解决时间−登记时间；根因类型以客服解决时填写为准。")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def generate_report(db: Session, start: date, end: date, period: str, trigger: str = "manual",
                    user_id: int | None = None) -> RptReport:
    from app.core.storage import save_bytes
    t0 = time.perf_counter()
    a = attribution(db, start, end)
    title = f"客服问题归因分析报告（{period}）"
    data = _attribution_docx(title, a)
    f = save_bytes(db, data=data, filename=f"客服问题归因报告_{period}.docx", category="report",
                   key=f"report/cs_attribution/{end:%Y-%m}/客服问题归因报告_{period}.docx", uploader_id=user_id)
    ver = (db.scalar(select(func.max(RptReport.version)).where(RptReport.report_type == "cs_attribution",
                                                                RptReport.period == period)) or 0) + 1
    metrics = {"total": a["total"], "resolved": a["resolved"], "avg_resolve_hours": a["avg_resolve_hours"],
               "duplicate_rate": a["duplicate_rate"], "top10": [{"label": c["label"], "count": c["count"]} for c in a["top10"]],
               "advice": [s["text"] for s in a["advice"]], "start": str(start), "end": str(end)}
    r = RptReport(report_type="cs_attribution", period=period, title=title, version=ver, metrics=metrics,
                  file_ids=[f.id], trigger=trigger, created_by=user_id, cost_ms=int((time.perf_counter() - t0) * 1000))
    db.add(r)
    db.flush()
    return r


def ticket_or_404(db: Session, ticket_id: int) -> CsTicket:
    t = db.get(CsTicket, ticket_id)
    if not t:
        raise not_found("工单")
    return t


def kb_doc_exists(db: Session) -> bool:
    return bool(db.scalar(select(func.count(KbDocument.id))))
