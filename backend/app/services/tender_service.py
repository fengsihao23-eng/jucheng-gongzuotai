"""产品中心 · 标书助手（PRD 7.7 / FR-06-03 / MCP kb.bid_check）。

1. 文档解析：docx/txt/md → 条款化（编号 一、/1./1.1/（1）/★/▲ + 表格行），识别类型（资质/技术参数/商务/格式）与 ★实质性
2. 合规检查：多轮对话式（资质 → 技术参数 → 商务 → 格式签章 → ★汇总），每轮 findings + 下一步提问，累积整改清单；
   每条整改项可追溯：需求书条款原文 + 投标文件定位 + 乐享制度/资质文档出处链接
3. 方案生成：实施方案/安装调试方案/售前方案，按模板大纲逐章生成，段落标注 [n]，citations 列出处；LLM 可用则写作，否则规则拼装
"""
import io
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.response import BizError, bad_request, not_found
from app.core.timeutil import now
from app.models import ProdBidCheck, ProdGeneration, ProdTenderDoc

DOC_TYPES = {"tender": "需求书/招标文件", "bid": "投标文件", "material": "投喂资料"}
DIMENSIONS = ["资质与资格", "技术参数", "商务条款", "格式与签章", "★实质性条款"]
GEN_TYPES = {"implementation": "实施方案", "installation": "安装调试方案", "presales": "售前方案"}

# ================================================================ 文本抽取
def extract_text(filename: str, data: bytes) -> str:
    name = (filename or "").lower()
    if name.endswith(".docx"):
        return _docx_text(data)
    if name.endswith((".txt", ".md", ".markdown")):
        for enc in ("utf-8-sig", "gb18030"):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
        raise BizError(42201, "文本文件编码无法识别，请另存为 UTF-8 后上传")
    if name.endswith(".pdf"):
        raise BizError(42201, "当前环境未安装 PDF 解析库，暂不支持 PDF：请另存为 Word(.docx) 或文本(.txt) 后上传")
    if name.endswith(".doc"):
        raise BizError(42201, "暂不支持旧版 .doc 格式，请另存为 .docx 后上传")
    raise BizError(42201, "仅支持 docx / txt / md 文件")


def _docx_text(data: bytes) -> str:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    try:
        doc = Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise BizError(42201, f"Word 文件解析失败：{exc.__class__.__name__}") from exc
    lines: list[str] = []
    for el in doc.element.body.iterchildren():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(el, doc)
            txt = p.text.strip()
            if txt:
                style = (p.style.name if p.style is not None else "") or ""
                if style.startswith("Heading") and not _CLAUSE_RE.match(txt):
                    txt = "# " + txt
                lines.append(txt)
        elif tag == "tbl":
            for row in Table(el, doc).rows:
                cells, prev = [], None
                for c in row.cells:
                    v = c.text.strip().replace("\n", " ")
                    if c._tc is not prev:
                        cells.append(v)
                    prev = c._tc
                if any(cells):
                    lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


# ================================================================ 条款化
_CLAUSE_RE = re.compile(
    r"^\s*(?P<mark>[★▲※]\s*)?(?P<no>第[一二三四五六七八九十百]+[章节条部分]|[一二三四五六七八九十]{1,3}、|"
    r"\d{1,2}(?:\.\d{1,2}){0,3}[\.、．]?(?=\s|[^\d\.])|[（(]\s*\d{1,2}\s*[）)])\s*(?P<mark2>[★▲※])?\s*(?P<text>.*)$")
KIND_RULES = [
    ("格式", ["授权委托书", "法定代表人授权", "签字", "签章", "盖章", "公章", "目录", "投标函", "报价一览表", "开标一览表",
              "响应文件组成", "投标文件组成", "投标文件须包含", "投标文件格式"]),
    ("资质", ["资质", "资格", "营业执照", "证书", "认证", "iso", "高新技术", "信用", "业绩", "授权书", "制造商", "能力等级",
              "社保", "无违法", "失信"]),
    ("商务", ["工期", "交货期", "质保", "保修", "付款", "支付", "报价", "有效期", "预付款", "验收款", "响应时间", "到达现场",
              "违约", "履约", "合同"]),
    ("技术参数", ["参数", "分辨率", "亮度", "像素", "英寸", "红外", "存储", "带宽", "接口", "功率", "触控", "规格", "≥", "≤",
                "不低于", "不少于", "不小于", "不大于", "万兆", "千兆", "poe", "技术要求", "wi-fi", "热备"]),
    ("格式", ["正本", "副本", "装订", "密封", "页码", "偏离表", "格式"]),
]


def classify_clause(text: str, chapter: str | None = None) -> str:
    t = text.lower()
    for kind, kws in KIND_RULES:
        if any(k in t for k in kws):
            return kind
    c = (chapter or "").lower()
    for kind, kws in KIND_RULES:
        if any(k in c for k in kws):
            return kind
    return "一般"


def parse_clauses(text: str) -> list[dict]:
    """返回 [{no, text, kind, required, chapter, line, table}]。表格行编号为 “<上级条款>-表<n>”。"""
    clauses: list[dict] = []
    chapter, last_no, table_no, header = None, None, 0, None
    for ln, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("# "):
            chapter = line[2:].strip()
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            body = " ".join(c for c in cells if c)
            if not re.search(r"\d", body) or any(h in cells[:2] for h in ("序号", "项目", "条款")):
                header = cells
                continue
            table_no += 1
            label = " ".join(cells[1:]) if cells and cells[0].isdigit() else body
            if header and len(header) == len(cells):
                label = "；".join(f"{h}：{v}" for h, v in zip(header, cells) if v and h not in ("序号",))
            parent = next((c for c in reversed(clauses) if not c["table"]), None)
            star = "★" in body or bool(parent and parent["no"] == last_no and parent["required"])
            clauses.append({"no": f"{last_no or '表'}-表{table_no}", "text": label.replace("★", "").strip(),
                            "kind": classify_clause(body, chapter) if classify_clause(body, chapter) != "一般" else "技术参数",
                            "required": star, "chapter": chapter, "line": ln, "table": True, "cells": cells})
            continue
        header = None
        m = _CLAUSE_RE.match(line)
        if m and m.group("text").strip():
            no = m.group("no").strip().rstrip("、.．")
            txt = m.group("text").strip()
            star = bool(m.group("mark") or m.group("mark2")) or "★" in txt
            if re.fullmatch(r"[一二三四五六七八九十]{1,3}", no) or no.startswith("第"):
                chapter = txt
            clauses.append({"no": no, "text": txt.replace("★", "").strip(), "kind": classify_clause(txt, chapter),
                            "required": star, "chapter": chapter, "line": ln, "table": False})
            last_no, table_no = no, 0
        elif line.startswith(("★", "▲")):
            clauses.append({"no": f"{last_no or ''}★", "text": line.lstrip("★▲ ").strip(),
                            "kind": classify_clause(line, chapter), "required": True, "chapter": chapter, "line": ln,
                            "table": False})
        elif clauses and not clauses[-1]["table"] and len(line) < 200:
            clauses[-1]["text"] += line  # 续行
    for c in clauses:
        if c["required"] and c["kind"] == "一般":
            c["kind"] = "★"
    return clauses


def upload_document(db: Session, doc_type: str, filename: str, data: bytes, title: str | None = None,
                    project_code: str | None = None, user_id: int | None = None) -> ProdTenderDoc:
    from app.core.storage import save_bytes
    if doc_type not in DOC_TYPES:
        raise bad_request("doc_type 须为 tender / bid / material")
    text = extract_text(filename, data)
    if len(text.strip()) < 20:
        raise BizError(42201, "未能从文件中抽取到有效文字")
    f = save_bytes(db, data=data, filename=filename, category="tender", uploader_id=user_id)
    return create_document(db, doc_type, title or re.sub(r"\.\w+$", "", filename), text, project_code, user_id, f.id)


def create_document(db: Session, doc_type: str, title: str, text: str, project_code: str | None = None,
                    user_id: int | None = None, file_id: int | None = None) -> ProdTenderDoc:
    d = ProdTenderDoc(doc_type=doc_type, title=title[:200], project_code=project_code, file_id=file_id,
                      content_text=text, clauses=parse_clauses(text), uploader_id=user_id)
    db.add(d)
    db.flush()
    return d


def doc_or_404(db: Session, doc_id: int, doc_type: str | None = None) -> ProdTenderDoc:
    d = db.get(ProdTenderDoc, doc_id)
    if not d or (doc_type and d.doc_type != doc_type):
        raise not_found(DOC_TYPES.get(doc_type or "", "文档"))
    return d


def doc_brief(d: ProdTenderDoc, with_clauses: bool = False) -> dict:
    kinds: dict[str, int] = {}
    for c in d.clauses or []:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    out = {"id": d.id, "doc_type": d.doc_type, "doc_type_text": DOC_TYPES.get(d.doc_type), "title": d.title,
           "project_code": d.project_code, "file_id": d.file_id, "clause_count": len(d.clauses or []),
           "star_count": sum(1 for c in d.clauses or [] if c.get("required")), "kinds": kinds,
           "size": len(d.content_text or ""), "created_at": d.created_at, "url": doc_link(d)}
    if with_clauses:
        out["clauses"] = d.clauses
        out["content_text"] = d.content_text
    return out


def doc_link(d: ProdTenderDoc, clause_no: str | None = None) -> str:
    base = f"{settings.public_base_url.rstrip('/')}/tender?tab=docs&doc={d.id}"
    return base + (f"&clause={clause_no}" if clause_no else "")


# ================================================================ 参数抽取与比对
@dataclass
class Param:
    key: str
    pattern: re.Pattern
    unit: str
    scope: str  # device | global


_NUM = r"(\d+(?:\.\d+)?)"
_OP = r"(≥|≤|>=|<=|不低于|不少于|不小于|不超过|不大于|不高于|至少|最多|大于等于|小于等于|在|为|：|:)?\s*"
PARAMS = [
    Param("尺寸", re.compile(r"(?:尺寸|屏幕|英寸)[^\d≥≤]{0,6}" + _OP + _NUM + r"\s*(?:英寸|寸|\")"), "英寸", "device"),
    Param("亮度", re.compile(r"亮度[^\d≥≤]{0,6}" + _OP + _NUM + r"\s*(?:cd|尼特|nit)", re.I), "cd/㎡", "device"),
    Param("像素", re.compile(r"(?:像素|分辨率)[^\d≥≤]{0,6}" + _OP + _NUM + r"\s*万(?:像素)?"), "万像素", "device"),
    Param("红外距离", re.compile(r"红外[^\d≥≤]{0,8}" + _OP + _NUM + r"\s*(?:米|m\b)", re.I), "米", "device"),
    Param("触控点数", re.compile(r"触控[^\d≥≤]{0,6}" + _OP + _NUM + r"\s*点"), "点", "device"),
    Param("存储时长", re.compile(r"(?:保存|存储)[^\d≥≤]{0,8}" + _OP + _NUM + r"\s*天"), "天", "device"),
    Param("工期", re.compile(r"(?:工期|交货期|完工)[^\d≥≤%]{0,20}" + _OP + _NUM + r"\s*(?:个)?(?:日历天|天|日)"), "天", "global"),
    Param("质保期", re.compile(r"(?:质保|保修)[^\d≥≤]{0,8}" + _OP + _NUM + r"\s*年"), "年", "global"),
    Param("报价有效期", re.compile(r"(?:报价|投标)有效期[^\d≥≤]{0,6}" + _OP + _NUM + r"\s*(?:个)?(?:日历天|天|日)"), "天", "global"),
    Param("响应时间", re.compile(r"(?:到达现场|响应时间|到场)[^\d≥≤]{0,6}" + _OP + _NUM + r"\s*小时|" + _NUM +
                             r"\s*小时内?(?:到达现场|到场|响应)"), "小时", "global"),
]
# 默认方向：数值越大越好(≥) / 越小越好(≤)
DEFAULT_DIR = {"尺寸": ">=", "亮度": ">=", "像素": ">=", "红外距离": ">=", "触控点数": ">=", "存储时长": ">=",
               "工期": "<=", "质保期": ">=", "报价有效期": ">=", "响应时间": "<="}
COMMERCIAL_KEYS = {"工期", "质保期", "报价有效期", "响应时间"}
DEVICE_WORDS = ["交互智能平板", "交互大屏", "智能平板", "网络摄像机", "摄像机", "NVR", "硬盘录像机", "广播终端", "IP广播",
                "核心交换机", "交换机", "无线AP", "AP", "门禁", "人脸识别终端", "电子班牌", "录播", "服务器", "存储"]


def _op(raw: str | None, key: str) -> str:
    raw = (raw or "").strip()
    if raw in ("≥", ">=", "不低于", "不少于", "不小于", "至少", "大于等于"):
        return ">="
    if raw in ("≤", "<=", "不超过", "不大于", "不高于", "最多", "小于等于"):
        return "<="
    if key in ("工期", "响应时间") and raw in ("在", "为", "：", ":", ""):
        return "<="
    if key in ("质保期", "报价有效期") and raw in ("为", "：", ":", "", "在"):
        return ">="
    return "=" if raw else DEFAULT_DIR.get(key, ">=")


def extract_params(text: str) -> list[dict]:
    out, seen = [], set()
    for p in PARAMS:
        for m in p.pattern.finditer(text):
            groups = [g for g in m.groups()]
            nums = [g for g in groups if g and re.fullmatch(r"\d+(?:\.\d+)?", g)]
            if not nums or p.key in seen:
                continue
            op_raw = next((g for g in groups if g and not re.fullmatch(r"\d+(?:\.\d+)?", g)), None)
            out.append({"key": p.key, "op": _op(op_raw, p.key), "value": float(nums[0]), "unit": p.unit,
                        "scope": p.scope, "raw": m.group(0).strip()})
            seen.add(p.key)
            break
    return out


def _device_of(text: str) -> str | None:
    return next((w for w in DEVICE_WORDS if w.lower() in text.lower()), None)


def _bigrams(s: str) -> set[str]:
    s = re.sub(r"\s+", "", s)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def _judge(req: dict, resp: dict | None) -> tuple[str, str]:
    """→ (status, text)：满足 / 正偏离 / 负偏离 / 未响应"""
    if resp is None:
        return "未响应", "投标文件未找到对应参数响应"
    rv, bv, op = req["value"], resp["value"], req["op"]
    if op == ">=":
        ok, better = bv >= rv, bv > rv
    elif op == "<=":
        ok, better = bv <= rv, bv < rv
    else:
        ok, better = bv == rv, False
    sym = {">=": "≥", "<=": "≤", "=": "="}[op]
    desc = f"要求 {req['key']} {sym}{_fmt(rv)}{req['unit']}，投标响应 {_fmt(bv)}{resp['unit']}"
    if not ok:
        return "负偏离", desc
    return ("正偏离" if better else "满足"), desc


# ================================================================ 证据（出处）
def _kb_evidence(db: Session, query: str, category=None, limit: int = 1) -> list[dict]:
    try:
        from app.services import kb_service
        hits = kb_service.search(db, query, category=category, limit=limit)
    except ImportError:
        return []
    return [{"type": "lexiang", "title": h["title"], "url": h["url"], "excerpt": re.sub(r"</?mark>", "", h["snippet"]),
             "heading": h["heading"]} for h in hits]


def _tender_ev(d: ProdTenderDoc, c: dict) -> dict:
    return {"type": "tender", "title": f"{d.title} · 条款 {c['no']}", "url": doc_link(d, c["no"]),
            "excerpt": c["text"][:200], "clause_no": c["no"], "line": c.get("line")}


def _bid_ev(d: ProdTenderDoc, c: dict | None, note: str | None = None) -> dict:
    if c is None:
        return {"type": "bid", "title": f"{d.title} · 未找到对应内容", "url": doc_link(d), "excerpt": note or "投标文件中未检索到对应响应"}
    return {"type": "bid", "title": f"{d.title} · {c['no']}", "url": doc_link(d, c["no"]), "excerpt": c["text"][:200],
            "clause_no": c["no"], "line": c.get("line")}


def _finding(dimension: str, c: dict, severity: str, deviation: str, suggestion: str, evidence: list[dict],
             status: str = "待整改", **extra) -> dict:
    return {"clause_no": c["no"], "clause_text": c["text"], "dimension": dimension, "severity": severity,
            "deviation": deviation, "suggestion": suggestion, "evidence": evidence, "status": status,
            "required": bool(c.get("required")), **extra}


# ================================================================ 各维度检查
QUAL_VOCAB: dict[str, list[str]] = {
    "营业执照": ["营业执照"], "高新技术企业证书": ["高新技术企业"], "ISO9001 质量管理体系认证": ["iso9001", "质量管理体系"],
    "ISO14001 环境管理体系认证": ["iso14001", "环境管理体系"],
    "ISO45001 职业健康安全管理体系认证": ["iso45001", "职业健康安全"],
    "安防工程企业设计施工维护能力证书": ["安防工程企业", "安防工程企业设计施工维护能力", "安全技术防范"],
    "CMMI 认证": ["cmmi"], "信息系统建设和服务能力等级证书": ["信息系统建设和服务能力", "cs 证书", "cs证书"],
    "信用等级证书": ["信用等级", "aaa", "信用企业"], "制造商授权书": ["制造商授权", "厂家授权", "原厂授权", "项目授权", "制造商"],
    "类似项目业绩": ["业绩", "类似项目"], "项目经理资格证书": ["建造师", "项目管理师", "项目经理证"],
    "无重大违法记录声明": ["无重大违法", "违法记录"], "社保缴纳证明": ["社保"],
}


def _qual_items(text: str) -> list[str]:
    t = text.lower()
    return [name for name, kws in QUAL_VOCAB.items() if any(k in t for k in kws)]


def check_qualification(db: Session, tender: ProdTenderDoc, bid: ProdTenderDoc) -> tuple[list[dict], list[dict]]:
    bid_text = bid.content_text.lower()
    findings, matches = [], []
    for c in tender.clauses:
        if c["kind"] != "资质":
            continue
        items = _qual_items(c["text"])
        for name in items:
            kws = QUAL_VOCAB[name]
            hit = next((bc for bc in bid.clauses if any(k in bc["text"].lower() for k in kws)), None)
            if hit or any(k in bid_text for k in kws):
                matches.append({"clause_no": c["no"], "item": name, "status": "已提供",
                                "bid_location": hit["no"] if hit else None})
                continue
            ev = [_tender_ev(tender, c), _bid_ev(bid, None, f"投标文件资格证明部分未检索到「{name}」"),
                  *_kb_evidence(db, f"{name} 资质证书", category="资质")]
            findings.append(_finding("资质与资格", c, "高" if c.get("required") or "须" in c["text"] else "中",
                                     f"未提供「{name}」", f"补充{name}复印件并加盖公章；依据公司资质证书清单核对证书有效期与等级"
                                     "（以证书原件为准）。如公司不具备该资质，须评估是否放弃投标或联合体投标。",
                                     ev, item=name))
    return findings, matches


def _find_bid_param(bid: ProdTenderDoc, req: dict, device: str | None) -> tuple[dict | None, dict | None]:
    cands = bid.clauses
    if device and req["scope"] == "device":
        dev_cl = [bc for bc in cands if device.lower() in bc["text"].lower()]
        if not dev_cl:
            dev_cl = sorted(cands, key=lambda bc: -len(_bigrams(device) & _bigrams(bc["text"])))[:1]
        cands = dev_cl
    for bc in cands:
        for p in extract_params(bc["text"]):
            if p["key"] == req["key"]:
                return p, bc
    if req["scope"] == "global":
        for p in extract_params(bid.content_text):
            if p["key"] == req["key"]:
                return p, None
    return None, None


def _param_check(db: Session, tender: ProdTenderDoc, bid: ProdTenderDoc, keys_filter, dimension: str):
    findings, matches = [], []
    for c in tender.clauses:
        params = [p for p in extract_params(c["text"]) if keys_filter(p["key"])]
        if not params:
            continue
        device = _device_of(c["text"])
        for req in params:
            resp, bc = _find_bid_param(bid, req, device)
            status, desc = _judge(req, resp)
            row = {"clause_no": c["no"], "device": device, "param": req["key"], "required_value": req["raw"],
                   "bid_value": resp["raw"] if resp else None, "status": status, "desc": desc,
                   "bid_location": bc["no"] if bc else None}
            if status in ("满足", "正偏离"):
                matches.append(row)
                continue
            star = c.get("required")
            sev = "高" if star or status == "未响应" and dimension == "商务条款" else ("高" if dimension == "商务条款" else "中")
            if status == "负偏离" and dimension == "技术参数" and not star:
                sev = "中"
            kb_q = {"质保期": "售后服务 质保期 承诺", "工期": "进度计划 工期", "响应时间": "售后 响应时效 到场",
                    "报价有效期": "投标 有效期"}.get(req["key"], f"{device or ''} {req['key']}")
            kb_cat = ["方案模板", "资质"] if dimension == "商务条款" else ["售前", "方案模板"]
            ev = [_tender_ev(tender, c), _bid_ev(bid, bc, f"投标文件未找到「{req['key']}」响应"),
                  *_kb_evidence(db, kb_q, category=kb_cat)]
            suggestion = (f"将{device or ''}{req['key']}调整为满足招标要求（{req['raw']}），并同步修改技术偏离表；"
                          "如现有型号无法满足，更换满足参数的型号并核对厂家彩页与检测报告。") if dimension == "技术参数" else \
                         (f"按招标要求响应{req['key']}（{req['raw']}），同步修改商务偏离表与相关承诺函。"
                          + ("该条款为★实质性条款，负偏离将导致废标。" if star else ""))
            findings.append(_finding(dimension, c, sev, f"{status}：{desc}" + ("（★实质性条款）" if star else ""),
                                     suggestion, ev, param=req["key"], device=device))
    return findings, matches


def check_technical(db, tender, bid):
    return _param_check(db, tender, bid, lambda k: k not in COMMERCIAL_KEYS, "技术参数")


def check_commercial(db, tender, bid):
    findings, matches = _param_check(db, tender, bid, lambda k: k in COMMERCIAL_KEYS, "商务条款")
    for c in tender.clauses:
        if c["kind"] == "商务" and any(k in c["text"] for k in ("付款", "支付")):
            hit = next((bc for bc in bid.clauses if any(k in bc["text"] for k in ("付款", "支付"))), None)
            if hit:
                matches.append({"clause_no": c["no"], "param": "付款方式", "status": "已响应", "bid_location": hit["no"]})
            else:
                findings.append(_finding("商务条款", c, "中", "未响应付款方式条款", "在商务偏离表中明确响应付款方式。",
                                         [_tender_ev(tender, c), _bid_ev(bid, None)]))
    return findings, matches


FORMAT_ITEMS = {"目录": ["目录"], "投标函": ["投标函"], "法定代表人授权委托书": ["授权委托书", "法定代表人授权"],
                "开标一览表/报价表": ["开标一览表", "报价一览表", "报价表", "分项报价"], "技术偏离表": ["技术偏离表", "技术响应"],
                "商务偏离表": ["商务偏离表", "商务响应"], "资格证明文件": ["资格证明", "资质文件"]}
SEAL_WORDS = ["签字", "签章", "盖章", "公章", "签名"]


def check_format(db: Session, tender: ProdTenderDoc, bid: ProdTenderDoc):
    findings, matches = [], []
    fmt_clauses = [c for c in tender.clauses if c["kind"] == "格式"]
    bid_text = bid.content_text
    for name, kws in FORMAT_ITEMS.items():
        req = next((c for c in fmt_clauses if any(k in c["text"] for k in kws)), None)
        if not req:
            continue
        hit = next((bc for bc in bid.clauses if any(k in bc["text"] for k in kws)), None)
        if hit or any(k in bid_text for k in kws):
            matches.append({"clause_no": req["no"], "item": name, "status": "已提供", "bid_location": hit["no"] if hit else None})
        else:
            findings.append(_finding("格式与签章", req, "高" if req.get("required") else "中", f"投标文件缺少「{name}」",
                                     f"按招标文件格式要求补充{name}，并编入目录。",
                                     [_tender_ev(tender, req), _bid_ev(bid, None, f"未检索到「{name}」")]))
    seal_req = next((c for c in fmt_clauses if any(k in c["text"] for k in SEAL_WORDS)), None)
    if seal_req:
        seal_lines = [bc for bc in bid.clauses if any(k in bc["text"] for k in SEAL_WORDS)]
        if seal_lines:
            matches.append({"clause_no": seal_req["no"], "item": "签字盖章", "status": "已标注",
                            "bid_location": seal_lines[0]["no"]})
        else:
            findings.append(_finding(
                "格式与签章", seal_req, "高", "投标文件未见法定代表人/授权代表签字及单位盖章说明（签章页缺失）",
                "在投标函、授权委托书、开标一览表、偏离表等页面落款处补充“法定代表人或授权代表（签字）”与“投标人（盖章）”，"
                "装订前逐页核对签字盖章，必要时加盖骑缝章。",
                [_tender_ev(tender, seal_req), _bid_ev(bid, None, "全文未检索到签字/盖章字样"),
                 *_kb_evidence(db, "投标文件 资质 复印件 加盖公章", category="资质")]))
    return findings, matches


def check_substantive(db: Session, tender: ProdTenderDoc, bid: ProdTenderDoc, prior: list[dict]):
    findings, matches = [], []
    by_clause: dict[str, list[dict]] = {}
    for f in prior:
        by_clause.setdefault(f["clause_no"], []).append(f)
    stars = [c for c in tender.clauses if c.get("required")]
    bid_grams = _bigrams(bid.content_text[:200000])
    for c in stars:
        if c["no"] in by_clause:
            matches.append({"clause_no": c["no"], "status": "存在偏离", "text": c["text"][:80],
                            "finding_dims": sorted({f["dimension"] for f in by_clause[c["no"]]})})
            continue
        sub_prefix = f"{c['no']}-表"
        sub = [f for f in prior if f["clause_no"].startswith(sub_prefix)]
        if sub:
            matches.append({"clause_no": c["no"], "status": "存在偏离", "text": c["text"][:80],
                            "finding_dims": sorted({f["dimension"] for f in sub})})
            continue
        grams = _bigrams(c["text"])
        cover = len(grams & bid_grams) / len(grams) if grams else 1
        if cover >= 0.45:
            matches.append({"clause_no": c["no"], "status": "已响应", "text": c["text"][:80], "coverage": round(cover, 2)})
        else:
            findings.append(_finding("★实质性条款", c, "高", f"★实质性条款未见明确响应（文本覆盖度 {cover:.0%}）",
                                     "在投标文件中逐条明确响应该★条款，并在偏离表中注明“无偏离”。",
                                     [_tender_ev(tender, c), _bid_ev(bid, None)]))
    return findings, matches


# ================================================================ 多轮会话
QUESTIONS = {
    1: "第 1 轮 · 资质与资格：已对照需求书的资格条款逐项核对投标文件。请确认上表中“未提供”的资质：公司是否具备、能否在截止前补充？"
       "（可回复如“安防工程企业能力证书已补充”；回复“无”继续）",
    2: "第 2 轮 · 技术参数：已逐项比对需求书参数与投标响应（≥/≤/不低于/不少于），负偏离与未响应项列于右侧。"
       "请确认是否更换型号或修改响应值？（如“亮度已改为 400cd”；回复“继续”进入下一轮）",
    3: "第 3 轮 · 商务条款：已核对工期、质保期、付款方式、报价有效期、售后响应等。负偏离的★条款将导致废标，请确认是否调整？",
    4: "第 4 轮 · 格式与签章：已检查目录、投标函、授权委托书、报价表、偏离表、签字盖章。请确认缺失材料是否已补齐？",
    5: "第 5 轮 · ★实质性条款汇总：以下为全部★条款的响应状态。回复“确认”生成最终整改清单。",
}
AFFIRM = ("已补充", "已补", "已修改", "已改", "改为", "调整为", "已调整", "已提供", "具备", "已盖章", "已签字", "已补齐", "已响应")


def _run_round(db: Session, n: int, tender: ProdTenderDoc, bid: ProdTenderDoc, prior: list[dict]):
    if n == 1:
        return check_qualification(db, tender, bid)
    if n == 2:
        return check_technical(db, tender, bid)
    if n == 3:
        return check_commercial(db, tender, bid)
    if n == 4:
        return check_format(db, tender, bid)
    return check_substantive(db, tender, bid, prior)


def _apply_answer(findings: list[dict], answer: str, round_findings_ids: list[int]) -> int:
    """用户回答“已补充/已修改 X”→ 对应整改项标记为“已说明（待核验）”，保留在清单中供复核。"""
    if not answer or not any(a in answer for a in AFFIRM):
        return 0
    n = 0
    for f in findings:
        if f["id"] not in round_findings_ids:
            continue
        keys = [k for k in (f.get("item"), f.get("param"), f.get("device")) if k]
        keys += [w for w in re.findall(r"[一-鿿A-Za-z0-9]{2,}", f["clause_text"])[:6]]
        if any(k and (k in answer or k[:4] in answer) for k in keys):
            f["status"] = "已说明（待核验）"
            f["user_note"] = answer[:200]
            n += 1
    return n


def _sort_key(f: dict):
    return ({"高": 0, "中": 1, "低": 2}[f["severity"]], DIMENSIONS.index(f["dimension"]), f["id"])


def _round_payload(n: int, round_findings: list[dict], matches: list[dict]) -> dict:
    return {"round": n, "dimension": DIMENSIONS[n - 1], "question": QUESTIONS[n], "answer": None,
            "finding_ids": [f["id"] for f in round_findings], "matches": matches,
            "summary": f"{DIMENSIONS[n - 1]}：发现 {len(round_findings)} 项问题，{len(matches)} 项符合/已响应",
            "at": now().isoformat()}


def start_check(db: Session, tender_doc_id: int, bid_doc_id: int, user_id: int | None = None) -> ProdBidCheck:
    tender = doc_or_404(db, tender_doc_id, "tender")
    bid = doc_or_404(db, bid_doc_id, "bid")
    chk = ProdBidCheck(tender_doc_id=tender.id, bid_doc_id=bid.id, status="running", rounds=[], findings=[],
                       created_by=user_id)
    db.add(chk)
    db.flush()
    _advance(db, chk, tender, bid)
    return chk


def _advance(db: Session, chk: ProdBidCheck, tender: ProdTenderDoc, bid: ProdTenderDoc) -> None:
    n = len(chk.rounds) + 1
    findings = list(chk.findings or [])
    new, matches = _run_round(db, n, tender, bid, findings)
    next_id = max([f["id"] for f in findings], default=0) + 1
    for f in new:
        f["id"] = next_id
        next_id += 1
    findings.extend(new)
    findings.sort(key=_sort_key)
    chk.findings = findings
    chk.rounds = [*chk.rounds, _round_payload(n, new, matches)]


def reply(db: Session, chk: ProdBidCheck, answer: str) -> ProdBidCheck:
    if chk.status == "done":
        raise bad_request("该合规检查已完成，可导出整改清单或新建检查")
    rounds = [dict(r) for r in chk.rounds]
    cur = rounds[-1]
    cur["answer"] = (answer or "").strip() or "继续"
    findings = [dict(f) for f in chk.findings]
    cur["resolved_by_answer"] = _apply_answer(findings, cur["answer"], cur["finding_ids"])
    chk.rounds, chk.findings = rounds, findings
    if len(rounds) >= len(DIMENSIONS):
        chk.status = "done"
    else:
        _advance(db, chk, doc_or_404(db, chk.tender_doc_id), doc_or_404(db, chk.bid_doc_id))
    db.flush()
    return chk


def check_stats(findings: list[dict]) -> dict:
    sev = {"高": 0, "中": 0, "低": 0}
    dim = {d: 0 for d in DIMENSIONS}
    for f in findings:
        sev[f["severity"]] += 1
        dim[f["dimension"]] += 1
    return {"total": len(findings), "by_severity": sev, "by_dimension": dim,
            "open": sum(1 for f in findings if f.get("status") == "待整改")}


def check_view(db: Session, chk: ProdBidCheck) -> dict:
    tender, bid = db.get(ProdTenderDoc, chk.tender_doc_id), db.get(ProdTenderDoc, chk.bid_doc_id)
    cur = chk.rounds[-1] if chk.rounds else None
    return {"id": chk.id, "status": chk.status, "tender": doc_brief(tender) if tender else None,
            "bid": doc_brief(bid) if bid else None, "rounds": chk.rounds, "findings": chk.findings,
            "current_round": len(chk.rounds), "total_rounds": len(DIMENSIONS),
            "next_question": None if chk.status == "done" else (cur or {}).get("question"),
            "stats": check_stats(chk.findings or []), "report_file_id": chk.report_file_id,
            "created_at": chk.created_at}


def bid_check(db: Session, tender_doc_id: int, bid_doc_id: int, persist: bool = True,
              user_id: int | None = None) -> dict:
    """一次性全维度检查（MCP kb.bid_check 复用）：自动走完 5 轮，返回整改清单。"""
    chk = start_check(db, tender_doc_id, bid_doc_id, user_id)
    while chk.status != "done":
        reply(db, chk, "继续")
    if persist:
        export_check(db, chk, "xlsx", user_id)
    view = check_view(db, chk)
    if not persist:
        db.delete(chk)
        db.flush()
        view["id"] = None
    return view


# ================================================================ 导出整改清单
def export_check(db: Session, chk: ProdBidCheck, fmt: str = "xlsx", user_id: int | None = None):
    from app.core.storage import save_bytes
    tender = db.get(ProdTenderDoc, chk.tender_doc_id)
    base = f"合规检查整改清单_{(tender.title if tender else chk.id)}"[:80]
    data = _check_xlsx(chk, tender) if fmt == "xlsx" else _check_docx(chk, tender)
    f = save_bytes(db, data=data, filename=f"{base}.{fmt}", category="tender",
                   key=f"tender/check/{chk.id}/{base}.{fmt}", uploader_id=user_id)
    chk.report_file_id = f.id
    db.flush()
    return f


def _ev_text(f: dict) -> tuple[str, str, str]:
    tender = next((e for e in f["evidence"] if e["type"] == "tender"), None)
    bid = next((e for e in f["evidence"] if e["type"] == "bid"), None)
    kb = [e for e in f["evidence"] if e["type"] in ("lexiang", "local")]
    return ((tender or {}).get("url", ""), (bid or {}).get("title", ""),
            "\n".join(f"{e['title']} {e['url']}" for e in kb))


def _check_xlsx(chk: ProdBidCheck, tender) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "整改清单"
    head = ["序号", "严重度", "维度", "条款号", "需求书条款原文", "偏离说明", "整改建议", "状态", "投标文件定位", "需求书出处链接", "依据出处（乐享）"]
    ws.append(head)
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1B3275")
    for i, f in enumerate(chk.findings, 1):
        t_url, b_loc, kb = _ev_text(f)
        ws.append([i, f["severity"], f["dimension"], f["clause_no"], f["clause_text"], f["deviation"], f["suggestion"],
                   f.get("status"), b_loc, t_url, kb])
    for col, w in zip("ABCDEFGHIJK", [6, 8, 12, 10, 48, 40, 48, 14, 28, 36, 48]):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    ws2 = wb.create_sheet("轮次记录")
    ws2.append(["轮次", "维度", "系统提问", "用户答复", "小结"])
    for r in chk.rounds:
        ws2.append([r["round"], r["dimension"], r["question"], r.get("answer") or "", r["summary"]])
    ws3 = wb.create_sheet("口径说明")
    for line in [
        ["项目", "说明"],
        ["检查对象", f"需求书：{tender.title if tender else ''}；投标文件 ID：{chk.bid_doc_id}"],
        ["检查维度", "资质与资格 → 技术参数 → 商务条款 → 格式与签章 → ★实质性条款，共 5 轮"],
        ["严重度", "高：★实质性条款偏离、缺失必备资质或签章，可能导致废标；中：一般负偏离/未响应；低：建议优化"],
        ["参数比对", "识别 ≥/≤/不低于/不少于/不超过 等比较词与数值单位，投标值不满足即判定负偏离；找不到对应参数判定未响应"],
        ["出处", "每条整改项附需求书条款原文与链接、投标文件定位、乐享制度/资质文档链接，供人工复核"],
        ["免责声明", "本清单由规则引擎辅助生成，最终以人工复核与招标文件原文为准"],
    ]:
        ws3.append(line)
    ws3.column_dimensions["A"].width, ws3.column_dimensions["B"].width = 14, 100
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _check_docx(chk: ProdBidCheck, tender) -> bytes:
    from docx import Document
    doc = Document()
    doc.add_heading("投标文件合规检查整改清单", 0)
    doc.add_paragraph(f"需求书：{tender.title if tender else ''}　生成时间：{now():%Y-%m-%d %H:%M}")
    st = check_stats(chk.findings)
    doc.add_paragraph(f"共 {st['total']} 项：高 {st['by_severity']['高']} / 中 {st['by_severity']['中']} / 低 {st['by_severity']['低']}")
    for i, f in enumerate(chk.findings, 1):
        doc.add_heading(f"{i}. [{f['severity']}] {f['dimension']} · 条款 {f['clause_no']}", 2)
        doc.add_paragraph(f"需求书原文：{f['clause_text']}")
        doc.add_paragraph(f"偏离说明：{f['deviation']}")
        doc.add_paragraph(f"整改建议：{f['suggestion']}")
        for e in f["evidence"]:
            p = doc.add_paragraph(style="List Bullet")
            p.add_run(f"{e['title']}：")
            _add_hyperlink(p, e["url"], e["url"])
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _add_hyperlink(paragraph, url: str, text: str):
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    r_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    h = OxmlElement("w:hyperlink")
    h.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "1B3275")
    u = OxmlElement("w:u")
    u.set(qn("w:val"), "single")
    rpr.append(color)
    rpr.append(u)
    run.append(rpr)
    t = OxmlElement("w:t")
    t.text = text
    run.append(t)
    h.append(run)
    paragraph._p.append(h)


# ================================================================ 方案生成（带出处标注）
# 每章：(标题, 模板小标题关键词, 需求书条款关键词, 额外乐享检索词/分类)
OUTLINES: dict[str, dict] = {
    "implementation": {"template": "mb3001", "template_query": "实施方案模板", "chapters": [
        ("项目概况", "项目概况", ["项目名称", "采购人", "建设内容", "预算"], None),
        ("需求理解与响应", "需求理解", ["技术", "参数", "要求", "满足"], None),
        ("总体实施思路", "总体实施思路", ["施工", "教学", "工期"], ("数字校园 实施 经验", ["案例"])),
        ("项目组织机构", "项目组织机构", ["项目经理", "人员", "团队"], None),
        ("进度计划", "进度计划", ["工期", "日历天", "完成"], ("项目管理 进度管理", ["方案模板"])),
        ("质量保障措施", "质量保障措施", ["质量", "检测", "验收"], ("质量管理 ISO9001", ["方案模板", "资质"])),
        ("安全文明施工", "安全文明施工", ["安全", "施工"], None),
        ("培训方案", "培训方案", ["培训"], None),
        ("验收方案", "验收方案", ["验收", "初验", "终验"], None),
        ("售后服务", "售后服务", ["质保", "售后", "到达现场", "报修"], ("售后服务 响应时效 备品备件", ["方案模板"])),
    ]},
    "installation": {"template": "mb3002", "template_query": "安装调试方案模板", "chapters": [
        ("工程概况", "工程概况", ["项目名称", "建设内容", "采购人"], None),
        ("编制依据", "编制依据", ["规范", "标准", "要求"], None),
        ("施工准备", "施工准备", ["施工", "现场", "勘察"], None),
        ("设备安装工艺", "设备安装工艺", ["交互智能平板", "摄像机", "AP", "交换机", "安装"], ("安装 承重 立杆 防水", ["案例"])),
        ("系统调试", "系统调试", ["参数", "联动", "调试", "广播"], None),
        ("测试与检验标准", "测试与检验标准", ["亮度", "像素", "红外", "保存", "检测"], None),
        ("安全与成品保护", "安全与成品保护", ["安全"], None),
        ("交付与移交", "交付与移交", ["验收", "交付", "培训"], None),
    ]},
    "presales": {"template": None, "template_query": "解决方案", "chapters": [
        ("公司简介与资质", None, ["资质", "资格", "证书"], ("公司资质证书清单 高新技术企业 ISO", ["资质", "制度"])),
        ("需求分析", None, ["建设内容", "需求", "项目名称"], ("建设背景 需求分析", ["售前"])),
        ("总体方案设计", None, ["技术要求", "建设内容"], ("总体架构 解决方案", ["售前"])),
        ("核心子系统方案", None, ["参数", "交互智能平板", "摄像机", "广播"], ("核心子系统 系统组成", ["售前"])),
        ("类似项目案例", None, ["业绩"], ("项目案例 建设内容 项目成效", ["案例"])),
        ("实施与服务承诺", None, ["工期", "质保", "售后"], ("实施 服务保障 质保", ["方案模板", "售前"])),
    ]},
}
LLM_SYSTEM = ("你是广东聚成数智科技有限公司的资深售前方案工程师。请严格只依据用户给出的编号材料撰写方案章节正文，"
              "不得编造材料中没有的数字、资质、案例或承诺；每个段落末尾必须用 [n] 标注所依据材料的编号（可多个，如 [1][3]）。"
              "使用正式书面语，2~4 段，不要输出章节标题。")


def project_info(tender: ProdTenderDoc | None) -> dict:
    text = tender.content_text if tender else ""

    def grab(pattern: str) -> str | None:
        m = re.search(pattern, text)
        return m.group(1).strip("。；; ") if m else None
    params = {p["key"]: p for p in extract_params(text)}
    return {"project_name": grab(r"项目名称[：:]\s*([^\n。；]+)") or (tender.title if tender else "【项目名称】"),
            "owner": grab(r"(?:采购人|建设单位|招标人)[：:]\s*([^\n。；]+)") or "【建设单位】",
            "duration": f"{_fmt(params['工期']['value'])} 日历天" if "工期" in params else "【工期】",
            "warranty": f"{_fmt(params['质保期']['value'])} 年" if "质保期" in params else "【质保期】"}


def _fill(text: str, info: dict) -> str:
    return (text.replace("【项目名称】", info["project_name"]).replace("【建设单位】", info["owner"])
            .replace("【工期】", info["duration"]).replace("【质保期】", info["warranty"]))


class _Cites:
    def __init__(self):
        self.items: list[dict] = []
        self._key: dict[tuple, int] = {}

    def add(self, key: tuple, **c) -> int:
        if key in self._key:
            return self._key[key]
        n = len(self.items) + 1
        self.items.append({"n": n, **c})
        self._key[key] = n
        return n


def _template_chunk(db: Session, template_sid: str | None, heading_kw: str | None):
    from app.models import KbChunk, KbDocument
    if not template_sid or not heading_kw:
        return None
    doc = db.scalar(select(KbDocument).where(KbDocument.source_doc_id == template_sid))
    if not doc:
        return None
    for c in db.scalars(select(KbChunk).where(KbChunk.doc_id == doc.id).order_by(KbChunk.seq)):
        if c.heading and heading_kw in c.heading:
            return doc, c
    return None


def _clauses_for(tender: ProdTenderDoc | None, kws: list[str], limit: int = 3) -> list[dict]:
    if not tender:
        return []
    hits = [c for c in tender.clauses if any(k.lower() in c["text"].lower() for k in kws)
            and not re.fullmatch(r"第.+|[一二三四五六七八九十]+", c["no"])]
    return hits[:limit]


def _material_chunks(materials: list[ProdTenderDoc], query: str, limit: int = 2) -> list[tuple]:
    q = _bigrams(query)
    scored = []
    for m in materials:
        for c in m.clauses or []:
            s = len(q & _bigrams(c["text"]))
            if s >= 2:
                scored.append((s, m.id, c["line"], m, c))
    scored.sort(key=lambda x: (-x[0], x[1], x[2]))
    return [(m, c) for _, _, _, m, c in scored[:limit]]


def _strip_md(s: str) -> str:
    return re.sub(r"^\s*\d+[\.、]\s*", "", s.strip())


def _gather(db: Session, gen_type: str, chapter: tuple, tender, materials, info, cites: _Cites):
    """为一章收集素材，返回 [(n, kind, text)]。"""
    from app.services import kb_service
    heading, tpl_kw, clause_kws, extra = chapter
    outline = OUTLINES[gen_type]
    srcs: list[tuple[int, str, str]] = []
    tc = _template_chunk(db, outline["template"], tpl_kw)
    if tc:
        doc, ch = tc
        n = cites.add(("kb", ch.id), type="lexiang", title=f"{doc.title} · {ch.heading}", url=doc.url,
                      excerpt=ch.chunk_text[:160])
        srcs.append((n, "template", _fill(ch.chunk_text, info)))
    for c in _clauses_for(tender, clause_kws):
        n = cites.add(("tender", tender.id, c["no"]), type="tender", title=f"{tender.title} · 条款 {c['no']}",
                      url=doc_link(tender, c["no"]), excerpt=c["text"][:160])
        srcs.append((n, "clause", c["text"]))
    for m, c in _material_chunks(materials, heading + " " + " ".join(clause_kws)):
        n = cites.add(("material", m.id, c["no"], c["line"]), type="material", title=f"{m.title} · {c['no']}",
                      url=doc_link(m, c["no"]), excerpt=c["text"][:160])
        srcs.append((n, "material", c["text"]))
    if extra or not tc:
        q, cats = extra or (f"{heading} {outline['template_query']}", ["方案模板", "售前", "案例"])
        for h in kb_service.search(db, q, category=cats, limit=2, per_doc=True):
            if tc and h["doc_id"] == tc[0].id:
                continue
            ch_text = next((r["text"] for r in kb_service.search(db, q, category=cats, limit=6, per_doc=False)
                            if r["chunk_id"] == h["chunk_id"]), re.sub(r"</?mark>", "", h["snippet"]))
            n = cites.add(("kb", h["chunk_id"]), type="lexiang" if h.get("source", "lexiang") == "lexiang" else "local",
                          title=f"{h['title']}" + (f" · {h['heading']}" if h["heading"] else ""), url=h["url"],
                          excerpt=re.sub(r"</?mark>", "", h["snippet"])[:160])
            srcs.append((n, "kb", ch_text))
    return srcs


def _rule_body(heading: str, srcs: list[tuple[int, str, str]], info: dict) -> str:
    paras: list[str] = []
    tpl = [s for s in srcs if s[1] == "template"]
    clauses = [s for s in srcs if s[1] == "clause"]
    mats = [s for s in srcs if s[1] == "material"]
    kbs = [s for s in srcs if s[1] == "kb"]
    for n, _, text in tpl:
        body = " ".join(_strip_md(x) for x in text.splitlines() if x.strip())
        paras.append(f"{body}[{n}]")
    if clauses:
        pts = "；".join(f"{_strip_md(t).rstrip('。；;')}[{n}]" for n, _, t in clauses)
        paras.append(f"针对招标文件要求，本章逐项响应如下：{pts}。我司承诺上述要求均予以满足，具体以技术/商务偏离表为准。")
    if mats:
        pts = "；".join(f"{_strip_md(t).rstrip('。；;')}[{n}]" for n, _, t in mats)
        paras.append(f"结合前期现场勘察与项目资料：{pts}。实施中将据此细化{heading}相关安排。")
    for n, _, text in kbs[:2]:
        lines = [_strip_md(x) for x in text.splitlines() if x.strip()][:3]
        if lines:
            paras.append(f"参考我司同类项目经验与标准资料：{'；'.join(l.rstrip('。；;') for l in lines)}。[{n}]")
    if not paras:
        paras.append(f"{info['project_name']}{heading}将按照公司标准流程执行，具体内容待补充。")
    return "\n\n".join(paras)


def _llm_body(heading: str, srcs: list[tuple[int, str, str]], info: dict) -> str | None:
    from app.integration import llm
    if not llm.enabled() or not srcs:
        return None
    material = "\n\n".join(f"[{n}] {text[:600]}" for n, _, text in srcs)
    out = llm.complete(LLM_SYSTEM, f"项目：{info['project_name']}；建设单位：{info['owner']}；工期：{info['duration']}。\n"
                                    f"请撰写章节「{heading}」。\n可用材料：\n{material}", temperature=0.3)
    if not out:
        return None
    allowed = {n for n, _, _ in srcs}
    used = {int(x) for x in re.findall(r"\[(\d+)\]", out)}
    if not used or not used <= allowed:
        return None  # 未标注或引用越界 → 规则兜底
    return out.strip()


def generate(db: Session, gen_type: str, tender_doc_id: int | None = None, material_ids: list[int] | None = None,
             title: str | None = None, user_id: int | None = None) -> ProdGeneration:
    if gen_type not in GEN_TYPES:
        raise bad_request("gen_type 须为 implementation / installation / presales")
    tender = doc_or_404(db, tender_doc_id, "tender") if tender_doc_id else None
    if gen_type != "presales" and not tender:
        raise bad_request(f"生成{GEN_TYPES[gen_type]}须选择需求书")
    materials = [doc_or_404(db, i) for i in (material_ids or [])]
    info = project_info(tender)
    outline = OUTLINES[gen_type]
    chapters = list(outline["chapters"])
    if gen_type == "presales":
        info.setdefault("project_name", "【项目名称】")
    cites = _Cites()
    sections, engines = [], set()
    for i, ch in enumerate(chapters, 1):
        srcs = _gather(db, gen_type, ch, tender, materials, info, cites)
        if not srcs and tender:  # 保底：至少引用需求书首条款
            c = tender.clauses[0]
            n = cites.add(("tender", tender.id, c["no"]), type="tender", title=f"{tender.title} · 条款 {c['no']}",
                          url=doc_link(tender, c["no"]), excerpt=c["text"][:160])
            srcs = [(n, "clause", c["text"])]
        body = _llm_body(ch[0], srcs, info)
        engine = "llm" if body else "rule"
        body = body or _rule_body(ch[0], srcs, info)
        engines.add(engine)
        used = sorted({int(x) for x in re.findall(r"\[(\d+)\]", body)})
        sections.append({"no": i, "heading": f"{_cn(i)}、{ch[0]}", "body": body, "engine": engine,
                         "citations": [c for c in cites.items if c["n"] in used]})
    g = ProdGeneration(gen_type=gen_type, title=(title or f"{info['project_name']}{GEN_TYPES[gen_type]}")[:200],
                       tender_doc_id=tender.id if tender else None, material_ids=[m.id for m in materials],
                       sections=sections, engine="llm" if engines == {"llm"} else ("rule" if engines == {"rule"} else "llm+rule"),
                       created_by=user_id)
    db.add(g)
    db.flush()
    return g


def _cn(i: int) -> str:
    return "一二三四五六七八九十"[i - 1] if i <= 10 else str(i)


def generation_view(g: ProdGeneration) -> dict:
    refs, seen = [], set()
    for s in g.sections:
        for c in s["citations"]:
            if c["n"] not in seen:
                seen.add(c["n"])
                refs.append(c)
    refs.sort(key=lambda c: c["n"])
    return {"id": g.id, "gen_type": g.gen_type, "gen_type_text": GEN_TYPES.get(g.gen_type), "title": g.title,
            "tender_doc_id": g.tender_doc_id, "material_ids": g.material_ids, "engine": g.engine,
            "sections": g.sections, "references": refs, "file_id": g.file_id, "created_at": g.created_at}


def export_generation(db: Session, g: ProdGeneration, user_id: int | None = None):
    from docx import Document
    from docx.shared import Pt

    from app.core.storage import save_bytes
    v = generation_view(g)
    doc = Document()
    doc.styles["Normal"].font.size = Pt(10.5)
    doc.add_heading(g.title, 0)
    doc.add_paragraph(f"编制单位：广东聚成数智科技有限公司　生成方式：{'大模型辅助' if 'llm' in g.engine else '规则引擎'}　"
                      f"日期：{now():%Y-%m-%d}　说明：正文 [n] 为参考出处编号，详见文末参考文献，供校对核查。")
    for s in g.sections:
        doc.add_heading(s["heading"], 1)
        for para in s["body"].split("\n\n"):
            doc.add_paragraph(para.strip())
    doc.add_heading("参考出处", 1)
    for c in v["references"]:
        p = doc.add_paragraph()
        p.add_run(f"[{c['n']}] {c['title']}　")
        _add_hyperlink(p, c["url"], c["url"])
        doc.add_paragraph(f"摘录：{c['excerpt']}").runs[0].font.size = Pt(9)
    buf = io.BytesIO()
    doc.save(buf)
    name = re.sub(r"[\\/:*?\"<>|]", "_", g.title)[:80]
    f = save_bytes(db, data=buf.getvalue(), filename=f"{name}.docx", category="tender",
                   key=f"tender/generation/{g.id}/{name}.docx", uploader_id=user_id)
    g.file_id = f.id
    db.flush()
    return f


def presales_library(db: Session) -> dict:
    from app.services import kb_service
    cats = ["售前", "方案模板", "案例", "资质"]
    return {c: kb_service.list_documents(db, c) for c in cats}
