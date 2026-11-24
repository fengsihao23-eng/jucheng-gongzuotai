"""企业知识库（PRD FR-06，2026-10 改为自建）：同事在工作台上传原件 → 存服务器 → 抽取正文分块 → 本地检索。

- 两级分类 kb_category：一级名写入 kb_document.category，二级名写入 subcategory。一级内置分类被标书/客服/人事
  等模块按名引用，不可改名或删除；二级与新增一级可自由维护。
- 可见范围 visibility：all 全员 / dept 指定部门 / roles 指定角色；检索只返回已发布且对当前用户可见的文档。
  不传 viewer 的内部调用（标书生成、客服建议、MCP 无主体时）只用「已发布 + 全员可见」的文档，受限文档不外流。
- 审核：分类审核人（kb_category.reviewer_roles，管理员与 kb:manage 恒可）上传即发布；其他人上传进入待审核。
- 原件按版本存 sys_file（key 固定为 kb/{source_doc_id}，同文档再传版本 +1），下载走 1h 签名链接。
- 分块：按小标题（## / 一、/ 第X章）切节，节内按段落合并，单块 ≤ 600 字；超长行按句号切分。
- 检索：中文 bigram + 英文/数字词，BM25 打分 + 标题/小标题加权 + 查询覆盖度；排序键 (-score, doc_id, seq) 稳定可复现。
- 索引常驻内存，按 (chunk 数, max chunk id, max 文档更新时间, 文档数) 签名自动失效重建。
- source=lexiang 的文档只来自历史乐享镜像 / 演示种子（scripts/purge_demo_kb.py 可清除），生产不再同步乐享。
"""
import html
import math
import re
import threading
import uuid
from pathlib import Path

from sqlalchemy import delete, func, insert, or_, select, text, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.permissions import DEPARTMENTS, ROLE_DEFS
from app.core.response import bad_request, conflict, forbidden, not_found
from app.core.timeutil import utcnow
from app.models import ImportBatch, KbCategory, KbChunk, KbDocument, SysFile, SysUser

MAX_CHUNK = 600
SNIPPET_LEN = 140
K1, B = 1.2, 0.75
STATUS_NAMES = {"pending": "待审核", "published": "已发布", "rejected": "已驳回", "archived": "已下架"}
VISIBILITY = ("private", "all", "dept", "roles")

# 默认分类树：(一级名, 说明, 审核角色, [二级名])。一级名同时是各模块检索用的分类键，务必保持稳定。
DEFAULT_TREE = [
    ("制度", "公司规章制度与内部流程", ["RS-01", "ZB-02"], ["人事制度", "财务制度", "行政制度", "业务流程"]),
    ("售前", "解决方案与售前材料（按业务线）", ["CP-01"], ["数字校园", "综合智慧安防", "素质教育考评", "运维服务"]),
    ("方案模板", "投标与实施文档模板", ["CP-01"], ["投标文件", "实施方案", "安装调试", "验收资料"]),
    ("案例", "已交付项目案例（按业务线）", ["CP-01", "SC-01"], ["数字校园", "综合智慧安防", "素质教育考评"]),
    ("资质", "公司与人员资质证书", ["ZB-02", "CP-01"], ["公司资质", "人员证书", "荣誉奖项"]),
    ("产品", "产品介绍、参数与报价", ["CP-01"], ["产品手册", "技术参数", "报价清单"]),
    ("客服", "运维排查手册与常见问题", ["KF-01"], ["排查手册", "常见问题", "运维规范"]),
    ("合同范本", "合同与协议模板", ["YX-01", "CW-01"], ["销售合同", "采购合同", "保密协议", "其他协议"]),
]
BUILTIN = {name for name, *_ in DEFAULT_TREE}
CATEGORY_ORDER = [name for name, *_ in DEFAULT_TREE]

# 上传时的分类建议：(一级, 关键词, {二级: 关键词})。按标题命中 ×3、正文前 3000 字命中 ×1 计分，取最高。
SUGGEST_RULES = [
    ("合同范本", ["合同范本", "协议模板", "合同模板", "协议书", "合同书", "保密协议", "框架协议"],
     {"销售合同": ["销售合同", "购销"], "采购合同": ["采购合同", "供货"], "保密协议": ["保密"]}),
    ("制度", ["制度", "管理办法", "管理规定", "实施细则", "守则", "规范", "流程"],
     {"人事制度": ["考勤", "休假", "请假", "薪酬", "招聘", "入职", "离职", "绩效", "社保"],
      "财务制度": ["报销", "差旅", "费用", "借款", "付款", "发票"], "行政制度": ["行政", "用车", "办公", "印章", "会议"]}),
    ("资质", ["资质", "证书", "认证", "许可证", "营业执照", "高新技术", "ISO", "荣誉"],
     {"人员证书": ["建造师", "工程师证", "人员证书", "职称"], "荣誉奖项": ["荣誉", "奖", "表彰"]}),
    ("方案模板", ["模板", "投标文件", "技术标", "商务标", "实施方案", "调试方案", "验收", "施工组织"],
     {"投标文件": ["投标", "技术标", "商务标"], "实施方案": ["实施方案", "施工组织"], "安装调试": ["安装", "调试"],
      "验收资料": ["验收"]}),
    ("案例", ["案例", "项目总结", "交付", "复盘", "成功经验"], {}),
    ("客服", ["排查", "故障", "常见问题", "FAQ", "运维", "巡检", "维修", "报修", "无信号", "离线"],
     {"排查手册": ["排查", "故障"], "常见问题": ["常见问题", "FAQ", "问答"], "运维规范": ["运维", "巡检"]}),
    ("产品", ["产品手册", "产品介绍", "技术参数", "规格书", "彩页", "报价"],
     {"技术参数": ["参数", "规格"], "报价清单": ["报价", "价格"]}),
    ("售前", ["解决方案", "建设方案", "规划方案", "售前", "方案"], {}),
]
LINE_HINTS = {"数字校园": ["数字校园", "智慧校园", "班班通", "交互大屏", "录播", "校园网"],
              "综合智慧安防": ["安防", "监控", "门禁", "明厨亮灶", "人脸", "周界"],
              "素质教育考评": ["素质教育", "综合评价", "体质健康", "考评", "体测"],
              "运维服务": ["运维", "维保", "巡检"]}

_TOKEN_RE = re.compile(r"[一-鿿]+|[a-z0-9]+")
_HEAD_RE = re.compile(r"^(#{1,6}\s*\S.*|[一二三四五六七八九十]{1,3}、\S.{0,40}|第[一二三四五六七八九十百0-9]{1,4}[章节部分篇]\S{0,40}.{0,20})$")
_SENT_SPLIT = re.compile(r"(?<=[。！？；!?;])")
STOP_TERMS = {"什么", "怎么", "如何", "请问", "一下", "可以", "问题", "的问", "有没", "没有", "是否", "哪些", "需要"}
# 口语 → 文档用语扩展（同义词只用于召回，命中仍按 BM25 打分）
SYNONYMS = {
    "年假": "年休假", "休年假": "年休假", "大屏": "交互大屏 智能平板", "白板": "交互大屏", "一体机": "交互大屏",
    "黑屏": "无信号", "没信号": "无信号", "没画面": "无信号", "没声音": "无声", "没有声音": "无声", "不响": "无声",
    "监控": "摄像机", "摄像头": "摄像机", "掉线": "离线", "断线": "离线", "wifi": "无线 ap", "刷脸": "人脸识别",
    "饭卡": "一卡通 消费机", "打卡": "考勤", "报账": "报销", "出差": "差旅", "社保": "社会保险", "五险一金": "社会保险 公积金",
    "素质评价": "素质教育 综合评价", "体测": "体质健康", "后厨": "明厨亮灶",
}



# ---------------------------------------------------------------- 分词 / 分块
def tokenize(text: str) -> list[str]:
    """中文连续串 → bigram（单字串保留单字）；英文/数字 → 小写词。保持出现顺序（含重复）。"""
    out: list[str] = []
    for m in _TOKEN_RE.finditer(text.lower()):
        s = m.group()
        if "一" <= s[0] <= "鿿":
            if len(s) == 1:
                out.append(s)
            else:
                out.extend(s[i:i + 2] for i in range(len(s) - 1))
        else:
            out.append(s)
    return out


def terms_of(text: str) -> list[str]:
    return sorted(set(tokenize(text)))


def query_terms(query: str) -> list[str]:
    q = (query or "").strip().lower()
    expanded = q + " " + " ".join(v for k, v in SYNONYMS.items() if k in q)
    seen, out = set(), []
    for t in tokenize(expanded):
        if t not in seen and t not in STOP_TERMS:
            seen.add(t)
            out.append(t)
    return out


def _split_long(line: str, max_len: int) -> list[str]:
    if len(line) <= max_len:
        return [line]
    parts, buf = [], ""
    for sent in _SENT_SPLIT.split(line):
        while len(sent) > max_len:  # 无标点的超长串硬切
            if buf:
                parts.append(buf)
                buf = ""
            parts.append(sent[:max_len])
            sent = sent[max_len:]
        if len(buf) + len(sent) > max_len and buf:
            parts.append(buf)
            buf = ""
        buf += sent
    if buf:
        parts.append(buf)
    return parts


def chunk_document(content: str, max_len: int = MAX_CHUNK) -> list[dict]:
    """返回 [{seq, heading, text, terms}]。线性复杂度，50MB 文本在单核上数十秒内完成。"""
    chunks: list[dict] = []
    heading: str | None = None
    buf: list[str] = []
    size = 0

    def flush():
        nonlocal buf, size
        text = "\n".join(buf).strip()
        if text:
            chunks.append({"seq": len(chunks), "heading": heading, "text": text,
                           "terms": terms_of(f"{heading or ''}\n{text}")})
        buf, size = [], 0

    for raw in (content or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if len(line) <= 60 and _HEAD_RE.match(line):
            flush()
            heading = line.lstrip("#").strip()[:200]
            continue
        for piece in _split_long(line, max_len):
            if size + len(piece) > max_len and buf:
                flush()
            buf.append(piece)
            size += len(piece) + 1
    flush()
    return chunks




# ---------------------------------------------------------------- 可见性 / 权限
def _is_manager(viewer) -> bool:
    return bool(viewer) and (viewer.is_admin or viewer.can("kb:manage"))


def _reviewer_roles(db: Session, category: str) -> list[str]:
    row = db.scalar(select(KbCategory).where(KbCategory.parent_id.is_(None), KbCategory.name == category))
    if row is not None:
        return list(row.reviewer_roles or [])
    return next((roles for name, _, roles, _ in DEFAULT_TREE if name == category), [])


def can_review(db: Session, viewer, category: str) -> bool:
    if not viewer:
        return False
    if _is_manager(viewer):
        return True
    return bool(set(viewer.role_codes) & set(_reviewer_roles(db, category)))


def _in_scope(visibility: str, depts, roles, viewer, uploader_id=None) -> bool:
    """可见范围判断（不含状态）。viewer=None 只认全员可见。"""
    if visibility in (None, "", "all"):
        return True
    if viewer is None:
        return False
    if _is_manager(viewer) or uploader_id == viewer.id:
        return True
    if visibility == "private":
        return False
    if visibility == "dept":
        return bool(viewer.dept_code) and viewer.dept_code in (depts or [])
    if visibility == "roles":
        return bool(set(viewer.role_codes) & set(roles or []))
    return False


def can_view(db: Session, viewer, d: KbDocument) -> bool:
    if _is_manager(viewer):
        return True
    if viewer is not None and d.uploader_id == viewer.id:
        return True
    if d.status != "published":
        return can_review(db, viewer, d.category)
    return _in_scope(d.visibility, d.visible_depts, d.visible_roles, viewer, d.uploader_id)


def can_remove_document(db, viewer, document):
    if viewer is None:
        return False
    if viewer.has_delete_grant:
        return can_view(db, viewer, document)
    if document.status in ("pending", "rejected"):
        return document.uploader_id == viewer.id or _is_manager(viewer)
    return can_review(db, viewer, document.category)


def default_visibility(viewer) -> dict:
    """默认仅本部门；无部门时仅本人。扩大到全员必须明确选择。"""
    dept = viewer.dept_code if viewer else None
    return {"visibility": "dept" if dept else "private", "visible_depts": [dept] if dept else [],
            "visible_roles": []}


# ---------------------------------------------------------------- 内存索引
class _Index:
    def __init__(self):
        self.sig = None
        self.rows: list[dict] = []
        self.df: dict[str, int] = {}
        self.inv: dict[str, list[int]] = {}
        self.avgdl = 1.0


_INDEX = _Index()
_LOCK = threading.Lock()


def _signature(db: Session):
    return (db.scalar(select(func.count(KbChunk.id))), db.scalar(select(func.max(KbChunk.id))),
            db.scalar(select(func.max(KbDocument.updated_at))), db.scalar(select(func.count(KbDocument.id))))


def invalidate_index() -> None:
    _INDEX.sig = None


def _index(db: Session) -> _Index:
    """只索引已发布文档；可见范围在打分时按 viewer 过滤。"""
    sig = _signature(db)
    if _INDEX.sig == sig:
        return _INDEX
    with _LOCK:
        if _INDEX.sig == sig:
            return _INDEX
        q = (select(KbChunk.id, KbChunk.doc_id, KbChunk.seq, KbChunk.heading, KbChunk.chunk_text, KbChunk.terms,
                    KbDocument.title, KbDocument.category, KbDocument.subcategory, KbDocument.tags, KbDocument.url,
                    KbDocument.source, KbDocument.visibility, KbDocument.visible_depts, KbDocument.visible_roles,
                    KbDocument.uploader_id)
             .join(KbDocument, KbDocument.id == KbChunk.doc_id).where(KbDocument.status == "published")
             .order_by(KbChunk.doc_id, KbChunk.seq))
        rows, df, inv, total = [], {}, {}, 0
        for r in db.execute(q):
            i = len(rows)
            tset = set(r.terms or [])
            rows.append({"chunk_id": r.id, "doc_id": r.doc_id, "seq": r.seq, "heading": r.heading,
                         "text": r.chunk_text, "lower": r.chunk_text.lower(), "title": r.title,
                         "title_lower": r.title.lower(), "heading_lower": (r.heading or "").lower(),
                         "category": r.category, "subcategory": r.subcategory, "tags": list(r.tags or []),
                         "url": r.url, "source": r.source, "terms": tset, "len": max(len(r.chunk_text), 1),
                         "visibility": r.visibility, "depts": list(r.visible_depts or []),
                         "roles": list(r.visible_roles or []), "uploader_id": r.uploader_id})
            total += len(r.chunk_text)
            for t in tset:
                df[t] = df.get(t, 0) + 1
                inv.setdefault(t, []).append(i)
        _INDEX.rows, _INDEX.df, _INDEX.inv = rows, df, inv
        _INDEX.avgdl = total / len(rows) if rows else 1.0
        _INDEX.sig = sig
        return _INDEX


# ---------------------------------------------------------------- 高亮片段
def highlight(text: str, terms: list[str], length: int = SNIPPET_LEN) -> str:
    lower = text.lower()
    marks = bytearray(len(text))
    positions: list[int] = []
    for t in terms:
        start = lower.find(t)
        while start != -1:
            positions.append(start)
            for k in range(start, start + len(t)):
                marks[k] = 1
            start = lower.find(t, start + 1)
    if len(text) <= length:
        a, b = 0, len(text)
    elif positions:
        positions.sort()
        best, best_n, j = positions[0], 0, 0
        for i, p in enumerate(positions):  # 滑窗找命中最密集的区间
            while positions[j] < p - length + 20:
                j += 1
            if i - j + 1 > best_n:
                best_n, best = i - j + 1, positions[j]
        a = max(0, best - 20)
        b = min(len(text), a + length)
    else:
        a, b = 0, length
    out, i = [], a
    while i < b:
        k = i
        flag = marks[i]
        while k < b and marks[k] == flag:
            k += 1
        seg = html.escape(text[i:k].replace("\n", " "))
        out.append(f"<mark>{seg}</mark>" if flag else seg)
        i = k
    return ("…" if a > 0 else "") + "".join(out) + ("…" if b < len(text) else "")


# ---------------------------------------------------------------- 检索
def _score_rows(idx: _Index, terms: list[str], categories: set[str] | None, subcategory: str | None, viewer):
    N = len(idx.rows)
    eff = [t for t in terms if idx.df.get(t)]
    if not eff:
        return [], []
    idf = {t: math.log(1 + (N - idx.df[t] + 0.5) / (idx.df[t] + 0.5)) for t in eff}
    cand: set[int] = set()
    for t in eff:
        cand.update(idx.inv[t])
    scored = []
    for i in cand:
        r = idx.rows[i]
        if categories and r["category"] not in categories:
            continue
        if subcategory and r["subcategory"] != subcategory:
            continue
        if not _in_scope(r["visibility"], r["depts"], r["roles"], viewer, r["uploader_id"]):
            continue
        s, matched = 0.0, 0
        norm = K1 * (1 - B + B * r["len"] / idx.avgdl)
        for t in eff:
            if t in r["terms"]:
                tf = r["lower"].count(t) or 1
                s += idf[t] * tf * (K1 + 1) / (tf + norm)
                matched += 1
            if t in r["title_lower"]:
                s += idf[t] * 0.8
            if t in r["heading_lower"]:
                s += idf[t] * 0.4
        s *= 0.5 + matched / len(eff)
        if s > 0:
            scored.append((round(s, 4), r))
    scored.sort(key=lambda x: (-x[0], x[1]["doc_id"], x[1]["seq"]))
    return scored, eff


def search(db: Session, query: str, category: str | list[str] | None = None, limit: int = 5,
           per_doc: bool = True, *, viewer=None, subcategory: str | None = None) -> list[dict]:
    """检索已发布文档。返回 [{doc_id, title, category, subcategory, url, heading, snippet(<mark>), score, chunk_id, seq}]。

    viewer 为 Principal：按其部门/角色过滤可见范围；None 时只检索全员可见文档（内部调用的安全默认）。
    per_doc=True 时每篇文档只返回得分最高的一个分块（列表展示）；False 返回分块级结果（方案生成取材）。
    """
    terms = query_terms(query)
    if not terms:
        return []
    cats = {category} if isinstance(category, str) and category else (set(category) if category else None)
    idx = _index(db)
    scored, eff = _score_rows(idx, terms, cats, subcategory, viewer)
    out, seen = [], set()
    for s, r in scored:
        if per_doc and r["doc_id"] in seen:
            continue
        seen.add(r["doc_id"])
        out.append({"doc_id": r["doc_id"], "title": r["title"], "category": r["category"],
                    "subcategory": r["subcategory"], "tags": r["tags"], "url": r["url"],
                    "heading": r["heading"], "snippet": highlight(r["text"], eff), "score": s,
                    "chunk_id": r["chunk_id"], "seq": r["seq"], "text": r["text"], "source": r["source"]})
        if len(out) >= limit:
            break
    if per_doc:
        for o in out:
            o.pop("text")
    return out


def search_any(db: Session, query: str, limit: int = 5) -> list[dict]:
    """兼容别名：全分类检索（全员可见范围）。"""
    return search(db, query, None, limit)


# ---------------------------------------------------------------- 分类
def ensure_categories(db: Session) -> None:
    """分类表为空时写入默认分类树（事务级 advisory lock 防多 worker 重复写入）。"""
    if db.scalar(select(func.count(KbCategory.id))):
        return
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext('kb_category_init'))"))
        if db.scalar(select(func.count(KbCategory.id))):
            return
    for i, (name, desc, roles, children) in enumerate(DEFAULT_TREE):
        top = KbCategory(name=name, sort=i * 10, description=desc, reviewer_roles=roles)
        db.add(top)
        db.flush()
        for j, child in enumerate(children):
            db.add(KbCategory(parent_id=top.id, name=child, sort=j * 10, reviewer_roles=[]))
    db.flush()


def _tree(db: Session) -> list[KbCategory]:
    ensure_categories(db)
    return list(db.scalars(select(KbCategory).order_by(KbCategory.sort, KbCategory.id)))


def category_tree(db: Session, viewer=None) -> list[dict]:
    """[{id, name, description, reviewer_roles, builtin, count, children:[{id, name, count}]}]。

    count 只计当前用户可见的已发布文档；文档里出现但目录中没有的一级分类附在末尾（id=None），不丢文档。
    """
    rows = _tree(db)
    docs = db.execute(select(KbDocument.category, KbDocument.subcategory, KbDocument.visibility,
                             KbDocument.visible_depts, KbDocument.visible_roles, KbDocument.uploader_id)
                      .where(KbDocument.status == "published")).all()
    top_n: dict[str, int] = {}
    sub_n: dict[tuple, int] = {}
    for r in docs:
        if not _in_scope(r.visibility, r.visible_depts, r.visible_roles, viewer, r.uploader_id):
            continue
        top_n[r.category] = top_n.get(r.category, 0) + 1
        if r.subcategory:
            sub_n[(r.category, r.subcategory)] = sub_n.get((r.category, r.subcategory), 0) + 1
    tops = [c for c in rows if c.parent_id is None]
    out = []
    for t in tops:
        kids = [c for c in rows if c.parent_id == t.id]
        out.append({"id": t.id, "name": t.name, "description": t.description, "reviewer_roles": t.reviewer_roles or [],
                    "builtin": t.name in BUILTIN, "count": top_n.get(t.name, 0),
                    "children": [{"id": k.id, "name": k.name, "count": sub_n.get((t.name, k.name), 0)} for k in kids]})
    known = {t.name for t in tops}
    for name in sorted(n for n in top_n if n not in known):
        out.append({"id": None, "name": name, "description": None, "reviewer_roles": [], "builtin": False,
                    "count": top_n[name], "children": []})
    return out


def categories(db: Session, viewer=None) -> list[dict]:
    """扁平一级分类 [{name, count}]（只含有文档的分类），供检索筛选与旧调用方使用。"""
    return [{"name": c["name"], "count": c["count"]} for c in category_tree(db, viewer) if c["count"]]


def _check_category(db: Session, category: str, subcategory: str | None) -> None:
    rows = _tree(db)
    top = next((c for c in rows if c.parent_id is None and c.name == category), None)
    if top is None:
        raise bad_request(f"分类「{category}」不存在")
    if subcategory and not any(c.parent_id == top.id and c.name == subcategory for c in rows):
        raise bad_request(f"「{category}」下没有子分类「{subcategory}」")


def _clean_roles(roles) -> list[str]:
    roles = [r for r in (roles or []) if r]
    bad = [r for r in roles if r not in ROLE_DEFS]
    if bad:
        raise bad_request("未知角色：" + "、".join(bad))
    return roles


def save_category(db: Session, viewer, *, cat_id: int | None = None, name: str, parent_id: int | None = None,
                  description: str | None = None, reviewer_roles: list[str] | None = None,
                  sort: int | None = None) -> KbCategory:
    if not _is_manager(viewer):
        raise forbidden("仅知识库管理员可维护分类")
    ensure_categories(db)
    name = (name or "").strip()
    if not name:
        raise bad_request("分类名称不能为空")
    if row := (db.get(KbCategory, cat_id) if cat_id else None):
        parent_id = row.parent_id
    elif cat_id:
        raise not_found("分类")
    if len(name) > (32 if parent_id else 16):
        raise bad_request("分类名称过长（一级 ≤16 字，二级 ≤32 字）")
    parent = db.get(KbCategory, parent_id) if parent_id else None
    if parent_id and (parent is None or parent.parent_id is not None):
        raise bad_request("只支持两级分类")
    dup = db.scalar(select(KbCategory.id).where(KbCategory.name == name, KbCategory.id != (cat_id or 0),
                                                KbCategory.parent_id == parent_id if parent_id else
                                                KbCategory.parent_id.is_(None)))
    if dup:
        raise conflict(f"已存在同名分类「{name}」")
    if row is None:
        siblings = db.scalar(select(func.max(KbCategory.sort)).where(
            KbCategory.parent_id == parent_id if parent_id else KbCategory.parent_id.is_(None)))
        row = KbCategory(parent_id=parent_id, name=name, sort=(siblings or 0) + 10, reviewer_roles=[])
        db.add(row)
    elif row.name != name:
        if row.parent_id is None and row.name in BUILTIN:
            raise bad_request(f"「{row.name}」是内置分类，被标书助手、客服等模块引用，不能改名")
        if row.parent_id is None:
            db.execute(update(KbDocument).where(KbDocument.category == row.name).values(category=name, updated_at=utcnow()))
        else:
            db.execute(update(KbDocument).where(KbDocument.category == parent.name, KbDocument.subcategory == row.name)
                       .values(subcategory=name, updated_at=utcnow()))
        row.name = name
    if description is not None:
        row.description = description[:255] or None
    if reviewer_roles is not None and row.parent_id is None:
        row.reviewer_roles = _clean_roles(reviewer_roles)
    if sort is not None:
        row.sort = sort
    db.flush()
    invalidate_index()
    return row


def delete_category(db: Session, viewer, cat_id: int) -> None:
    if not _is_manager(viewer):
        raise forbidden("仅知识库管理员可维护分类")
    row = db.get(KbCategory, cat_id)
    if row is None:
        raise not_found("分类")
    if row.parent_id is None:
        if row.name in BUILTIN:
            raise bad_request(f"「{row.name}」是内置分类，不能删除")
        used = db.scalar(select(func.count(KbDocument.id)).where(KbDocument.category == row.name))
    else:
        parent = db.get(KbCategory, row.parent_id)
        used = db.scalar(select(func.count(KbDocument.id)).where(KbDocument.category == parent.name,
                                                                 KbDocument.subcategory == row.name))
    if used:
        raise conflict(f"该分类下还有 {used} 篇文档，请先移到其他分类")
    db.delete(row)
    db.flush()


def suggest_category(title: str, content: str = "") -> dict:
    """按关键词给出分类建议 {category, subcategory, reason}；没把握时 category=None。"""
    title_l, body_l = (title or "").lower(), (content or "")[:3000].lower()
    best, best_score, best_hits = None, 0, []
    for cat, words, subs in SUGGEST_RULES:
        score, hits = 0, []
        for w in words:
            wl = w.lower()
            n = (3 if wl in title_l else 0) + min(body_l.count(wl), 3)
            if n:
                score += n
                hits.append(w)
        if score:  # 标题命中二级分类关键词说明更具体，加分（如「运维巡检规范」归客服/运维规范而不是制度）
            score += sum(2 for ws in subs.values() for w in ws if w.lower() in title_l)
        if score > best_score:
            best, best_score, best_hits = cat, score, hits
    if best is None or best_score < 2:
        return {"category": None, "subcategory": None, "reason": "没有明显的分类特征，请手动选择"}
    subs = next(s for c, _, s in SUGGEST_RULES if c == best)
    sub = None
    text_all = title_l + " " + body_l
    if best in ("售前", "案例"):
        cand = [(sum(text_all.count(w.lower()) for w in ws), line) for line, ws in LINE_HINTS.items()
                if best == "售前" or line != "运维服务"]
        cand.sort(key=lambda x: -x[0])
        sub = cand[0][1] if cand and cand[0][0] else None
    else:
        cand = sorted(((sum(text_all.count(w.lower()) for w in ws), name) for name, ws in subs.items()),
                      key=lambda x: -x[0])
        sub = cand[0][1] if cand and cand[0][0] else None
    return {"category": best, "subcategory": sub, "reason": "命中关键词：" + "、".join(best_hits[:5])}


# ---------------------------------------------------------------- 文档：查询
def _rebuild_chunks(db: Session, doc: KbDocument) -> int:
    db.execute(delete(KbChunk).where(KbChunk.doc_id == doc.id))
    rows = chunk_document(doc.content_text)
    if rows:
        db.execute(insert(KbChunk), [{"doc_id": doc.id, "seq": c["seq"], "heading": c["heading"],
                                      "chunk_text": c["text"], "terms": c["terms"]} for c in rows])
    return len(rows)


def _names(db: Session, ids) -> dict[int, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return dict(db.execute(select(SysUser.id, SysUser.name).where(SysUser.id.in_(ids))).all())


def _doc_brief(d: KbDocument, names: dict[int, str] | None = None) -> dict:
    names = names or {}
    return {"id": d.id, "source": d.source, "source_doc_id": d.source_doc_id, "title": d.title,
            "category": d.category, "subcategory": d.subcategory, "tags": d.tags or [], "summary": d.summary,
            "url": d.url, "version": d.version, "source_updated_at": d.source_updated_at,
            "updated_at": d.updated_at, "size": len(d.content_text or ""), "status": d.status,
            "status_name": STATUS_NAMES.get(d.status, d.status), "visibility": d.visibility,
            "visible_depts": d.visible_depts or [], "visible_roles": d.visible_roles or [],
            "uploader_id": d.uploader_id, "uploader": names.get(d.uploader_id), "has_file": bool(d.file_id),
            "review_note": d.review_note, "reviewed_at": d.reviewed_at}


def list_documents(db: Session, category: str | list[str] | None = None, keyword: str | None = None, *,
                   viewer=None, scope: str = "published", subcategory: str | None = None,
                   tag: str | None = None) -> list[dict]:
    """scope：published 已发布（按可见范围）| mine 我上传的（全部状态）| review 待我审核 | archived 已下架（审核人）。"""
    q = select(KbDocument).order_by(KbDocument.category, KbDocument.title)
    if isinstance(category, str) and category:
        q = q.where(KbDocument.category == category)
    elif category:
        q = q.where(KbDocument.category.in_(list(category)))
    if subcategory:
        q = q.where(KbDocument.subcategory == subcategory)
    if keyword:
        q = q.where(or_(KbDocument.title.ilike(f"%{keyword}%"), KbDocument.summary.ilike(f"%{keyword}%")))
    if scope == "mine":
        if viewer is None:
            return []
        q = q.where(KbDocument.uploader_id == viewer.id)
    elif scope == "review":
        q = q.where(KbDocument.status == "pending")
    elif scope == "archived":
        q = q.where(KbDocument.status == "archived")
    else:
        q = q.where(KbDocument.status == "published")
    rows = list(db.scalars(q))
    if tag:
        rows = [d for d in rows if tag in (d.tags or [])]
    if scope == "published":
        rows = [d for d in rows if _in_scope(d.visibility, d.visible_depts, d.visible_roles, viewer, d.uploader_id)]
    elif scope in ("review", "archived"):
        ok_cats = {c for c in {d.category for d in rows} if can_review(db, viewer, c)}
        rows = [d for d in rows if d.category in ok_cats]
    names = _names(db, (d.uploader_id for d in rows))
    reviewers = {category for category in {d.category for d in rows} if can_review(db, viewer, category)}
    return [{**_doc_brief(d, names), "can_delete": (bool(viewer and viewer.has_delete_grant) or
             ((d.uploader_id == viewer.id or _is_manager(viewer)) if viewer is not None and
              d.status in ("pending", "rejected") else d.category in reviewers))}
            for d in rows]


def _get(db: Session, doc_id: int | str) -> KbDocument:
    d = db.get(KbDocument, int(doc_id)) if str(doc_id).isdigit() else None
    if d is None:
        d = db.scalar(select(KbDocument).where(KbDocument.source_doc_id == str(doc_id)))
    if d is None:
        raise not_found("知识库文档")
    return d


def get_doc(db: Session, doc_id: int | str, viewer=None) -> dict:
    """viewer 为空时视为内部调用（不做可见性判断）；接口层必须传 viewer。不可见与不存在同样返回 404。"""
    from app.core.storage import file_brief
    d = _get(db, doc_id)
    if viewer is not None and not can_view(db, viewer, d):
        raise not_found("知识库文档")
    chunks = db.scalars(select(KbChunk).where(KbChunk.doc_id == d.id).order_by(KbChunk.seq)).all()
    names = _names(db, [d.uploader_id, d.reviewer_id])
    reviewer = can_review(db, viewer, d.category) if viewer is not None else False
    mine = viewer is not None and d.uploader_id == viewer.id
    f = db.get(SysFile, d.file_id) if d.file_id else None
    versions = []
    if f is not None:
        versions = [{"version": x.version, "filename": x.filename, "size": x.size, "created_at": x.created_at,
                     **(file_brief(x) or {})} for x in
                    db.scalars(select(SysFile).where(SysFile.storage_key == f.storage_key).order_by(SysFile.version.desc()))]
    return {**_doc_brief(d, names), "content_text": d.content_text, "reviewer": names.get(d.reviewer_id),
            "file": file_brief(f), "versions": versions,
            "chunks": [{"id": c.id, "seq": c.seq, "heading": c.heading, "text": c.chunk_text} for c in chunks],
            "can_edit": reviewer or (mine and d.status in ("pending", "rejected")),
            "can_review": reviewer and d.status == "pending",
            "can_archive": d.status == "published" and (reviewer or mine),
            "can_restore": reviewer and d.status == "archived",
            "can_delete": can_remove_document(db, viewer, d),
            "can_new_version": d.source == "upload" and d.status != "archived" and (reviewer or mine)}


def stats(db: Session, viewer=None) -> dict:
    pub = db.execute(select(KbDocument.id, KbDocument.source, KbDocument.visibility, KbDocument.visible_depts,
                            KbDocument.visible_roles, KbDocument.uploader_id)
                     .where(KbDocument.status == "published")).all()
    visible = [r for r in pub if _in_scope(r.visibility, r.visible_depts, r.visible_roles, viewer, r.uploader_id)]
    pending = db.scalars(select(KbDocument.category).where(KbDocument.status == "pending")).all()
    ok_cats = {c for c in set(pending) if can_review(db, viewer, c)}
    return {"total_docs": len(visible),
            "total_chunks": db.scalar(select(func.count(KbChunk.id)).where(KbChunk.doc_id.in_([r.id for r in visible]))) or 0,
            "pending_review": sum(1 for c in pending if c in ok_cats),
            "my_uploads": db.scalar(select(func.count(KbDocument.id)).where(KbDocument.uploader_id == viewer.id))
            if viewer is not None else 0,
            "can_manage": _is_manager(viewer),
            "demo_docs": sum(r.source == "lexiang" for r in visible),
            "default_visibility": default_visibility(viewer)}


def document_chunks(db: Session, viewer, doc_id: int | str, offset=0, limit=5) -> dict:
    """按权限分页提供完整分块，避免小聚只拿到长文档的截断开头。"""
    d = _get(db, doc_id)
    if not can_view(db, viewer, d):
        raise not_found("知识库文档")
    offset, limit = max(0, offset), max(1, min(limit, 10))
    query = select(KbChunk).where(KbChunk.doc_id == d.id)
    total = db.scalar(select(func.count(KbChunk.id)).where(KbChunk.doc_id == d.id)) or 0
    rows = db.scalars(query.order_by(KbChunk.seq).offset(offset).limit(limit)).all()
    return {"doc_id": d.id, "title": d.title, "source": d.source, "status": d.status, "url": d.url,
            "total_chunks": total, "offset": offset, "next_offset": offset + len(rows) if offset + len(rows) < total else None,
            "has_more": offset + len(rows) < total,
            "chunks": [{"seq": c.seq, "heading": c.heading, "text": c.chunk_text} for c in rows]}


# ---------------------------------------------------------------- 文档：上传 / 维护
def _norm_tags(tags) -> list[str]:
    if isinstance(tags, str):
        tags = re.split(r"[,，;；\s]+", tags)
    out = []
    for t in tags or []:
        t = str(t).strip()[:20]
        if t and t not in out:
            out.append(t)
    return out[:10]


def department_options(db):
    from app.models import SysDepartment
    rows=list(db.scalars(select(SysDepartment).order_by(SysDepartment.sort,SysDepartment.id)))
    return [{'code':d.dept_code,'name':d.name} for d in rows] or [{'code':c,'name':n} for c,n in DEPARTMENTS]


def _norm_visibility(visibility: str | None, depts, roles, db=None) -> tuple[str, list, list]:
    visibility = visibility or "all"
    if visibility not in VISIBILITY:
        raise bad_request("可见范围只能是 仅本人 / 全员 / 指定部门 / 指定角色")
    depts = [x for x in (depts or []) if x]
    roles = _clean_roles(roles)
    known = {code for code, _ in DEPARTMENTS}
    if db is not None: known.update(d['code'] for d in department_options(db))
    if any(x not in known for x in depts):
        raise bad_request("未知部门：" + "、".join(x for x in depts if x not in known))
    if visibility == "dept" and not depts:
        raise bad_request("请至少选择一个可见部门")
    if visibility == "roles" and not roles:
        raise bad_request("请至少选择一个可见角色")
    return visibility, depts if visibility == "dept" else [], roles if visibility == "roles" else []


def _notify_reviewers(db: Session, d: KbDocument, uploader_name: str) -> None:
    from app.services import notify_service
    from app.core.deps import build_principal
    users = [u for u in db.scalars(select(SysUser).where(SysUser.status == 1, SysUser.is_deleted.is_(False)))
             if u.id != d.uploader_id and can_review(db, build_principal(u), d.category)]
    notify_service.send(db, users, "kb_review", f"知识库待审核：{d.title}",
                        f"**{uploader_name}** 上传了《{d.title}》（{d.category}{' / ' + d.subcategory if d.subcategory else ''}），请审核发布。",
                        link="/kb?tab=review", ref_type="kb_document", ref_id=str(d.id))


def upload_document(db: Session, viewer, *, data: bytes, filename: str, title: str | None = None,
                    category: str | None = None, subcategory: str | None = None, tags=None,
                    summary: str | None = None, visibility: str | None = None, visible_depts=None,
                    visible_roles=None, replace_doc_id: int | None = None, owner=None,
                    content: str | None = None) -> KbDocument:
    """上传一份文档（或给已有文档传新版本）。审核人直接发布，其他人进入待审核并通知审核人。

    content 用于复用「资料导入」已抽取/识别好的正文（尤其是扫描件），避免确认入库时重复调用多模态模型。
    """
    from app.core.storage import save_bytes
    from app.services.kb_extract import extract_text
    import hashlib

    content = (content or "").strip()
    if content:
        if len(re.sub(r"\s", "", content)) < 10:
            raise bad_request("没有从文件里读到正文内容")
    else:
        content = extract_text(data, filename)
    sha = hashlib.sha256(data).hexdigest()
    owner = owner or viewer
    old = None
    if replace_doc_id:
        old = db.get(KbDocument, replace_doc_id)
        if old is None:
            raise not_found("知识库文档")
        if old.source != "upload":
            raise bad_request("这篇文档不是上传文档，不能传新版本")
        if not (can_review(db, viewer, old.category) or old.uploader_id == viewer.id):
            raise forbidden("只有上传人或该分类审核人可以更新版本")
        if old.status == "archived":
            raise conflict("已下架的文档请先恢复发布再更新版本")
        if old.sha256 == sha:
            raise conflict("与当前版本内容完全相同，无需重新上传")
        category = category or old.category
        subcategory = subcategory if subcategory is not None else old.subcategory
        title = title or old.title
        summary = summary if summary is not None else old.summary
        if tags is None:
            tags = old.tags
        if visibility is None:
            visibility, visible_depts, visible_roles = old.visibility, old.visible_depts, old.visible_roles
    else:
        # 不向其他部门泄漏受限文档的标题、编号或是否存在；允许各自保存授权副本。
        dup = next((d for d in db.scalars(select(KbDocument).where(
            KbDocument.sha256 == sha, KbDocument.status != "archived")) if can_view(db, owner, d)), None)
        if dup is not None:
            raise conflict(f"同样的文件已经在知识库里：《{dup.title}》", data={"doc_id": dup.id})
    title = (title or "").strip() or Path(filename).stem
    if not category:
        category = suggest_category(title, content)["category"]
        if not category:
            raise bad_request("请选择文档分类")
    _check_category(db, category, subcategory or None)
    if visibility is None:
        defaults = default_visibility(owner)
        visibility, visible_depts, visible_roles = defaults["visibility"], defaults["visible_depts"], defaults["visible_roles"]
    visibility, depts, roles = _norm_visibility(visibility, visible_depts, visible_roles, db=db)
    publish = can_review(db, viewer, category)
    now = utcnow()

    sid = old.source_doc_id if old is not None else f"up-{uuid.uuid4().hex[:16]}"
    f = save_bytes(db, data=data, filename=Path(filename).name[:200], category="kb", key=f"kb/{sid}",
                   uploader_id=owner.id)
    d = old
    if d is None:
        d = KbDocument(source="upload", source_doc_id=sid, url="", uploader_id=owner.id)
        db.add(d)
    d.title, d.category, d.subcategory = title[:200], category, subcategory or None
    d.tags, d.summary = _norm_tags(tags), (summary or "").strip()[:500] or None
    d.visibility, d.visible_depts, d.visible_roles = visibility, depts, roles
    d.content_text, d.file_id, d.sha256, d.version = content, f.id, sha, f"v{f.version}"
    d.status = "published" if publish else "pending"
    d.reviewer_id, d.reviewed_at = (viewer.id, now) if publish else (None, None)
    d.review_note = None
    d.source_updated_at = d.updated_at = now
    db.flush()
    d.url = f"{settings.public_base_url.rstrip('/')}/kb?doc={d.id}"
    _rebuild_chunks(db, d)
    db.flush()
    invalidate_index()
    if not publish:
        _notify_reviewers(db, d, owner.user.name)
    return d


def update_meta(db: Session, viewer, doc_id: int, **fields) -> KbDocument:
    d = _get(db, doc_id)
    reviewer = can_review(db, viewer, d.category)
    if not (reviewer or (d.uploader_id == viewer.id and d.status in ("pending", "rejected"))):
        raise forbidden("已发布文档只能由分类审核人修改；你可以上传新版本")
    category = fields.get("category") or d.category
    subcategory = fields["subcategory"] if "subcategory" in fields else d.subcategory
    if category != d.category or subcategory != d.subcategory:
        _check_category(db, category, subcategory or None)
    if "visibility" in fields or "visible_depts" in fields or "visible_roles" in fields:
        d.visibility, d.visible_depts, d.visible_roles = _norm_visibility(
            fields.get("visibility", d.visibility), fields.get("visible_depts", d.visible_depts),
            fields.get("visible_roles", d.visible_roles), db=db)
    if fields.get("title"):
        d.title = fields["title"].strip()[:200]
    if "summary" in fields:
        d.summary = (fields["summary"] or "").strip()[:500] or None
    if "tags" in fields:
        d.tags = _norm_tags(fields["tags"])
    moved = category != d.category
    d.category, d.subcategory = category, subcategory or None
    if d.status == "rejected" or (moved and d.status == "published" and not can_review(db, viewer, category)):
        d.status, d.reviewer_id, d.reviewed_at = "pending", None, None  # 驳回后修改即重新提交
        _notify_reviewers(db, d, viewer.user.name)
    d.updated_at = utcnow()
    db.flush()
    invalidate_index()
    return d


def review(db: Session, viewer, doc_id: int, approve: bool, note: str | None = None) -> KbDocument:
    d = _get(db, doc_id)
    if d.status != "pending":
        raise conflict("这篇文档不在待审核状态")
    if not can_review(db, viewer, d.category):
        raise forbidden(f"你不是「{d.category}」分类的审核人")
    if not approve and not (note or "").strip():
        raise bad_request("驳回请写明原因，方便上传人修改")
    d.status = "published" if approve else "rejected"
    d.reviewer_id, d.reviewed_at, d.review_note = viewer.id, utcnow(), (note or "").strip()[:255] or None
    d.updated_at = utcnow()
    db.flush()
    invalidate_index()
    if d.uploader_id and d.uploader_id != viewer.id:
        from app.services import notify_service
        u = db.get(SysUser, d.uploader_id)
        notify_service.send(db, [u], "kb_review", f"知识库审核{'通过' if approve else '驳回'}：{d.title}",
                            f"《{d.title}》已{'发布到知识库' if approve else '被驳回'}"
                            f"{'：' + d.review_note if d.review_note else ''}",
                            link=f"/kb?doc={d.id}", ref_type="kb_document", ref_id=str(d.id))
    return d


def set_archived(db: Session, viewer, doc_id: int, archived: bool) -> KbDocument:
    d = _get(db, doc_id)
    reviewer = can_review(db, viewer, d.category)
    if archived:
        if d.status != "published":
            raise conflict("只有已发布的文档可以下架")
        if not (reviewer or d.uploader_id == viewer.id):
            raise forbidden("只有上传人或该分类审核人可以下架")
        d.status = "archived"
    else:
        if d.status != "archived":
            raise conflict("这篇文档没有下架")
        if not reviewer:
            raise forbidden("只有该分类审核人可以恢复发布")
        d.status = "published"
    d.updated_at = utcnow()
    db.flush()
    invalidate_index()
    return d


def delete_document(db: Session, viewer, doc_id: int) -> None:
    """只能删除未发布（待审核/已驳回）的文档；已发布文档请下架，保留审计链。原件文件保留在归档目录。"""
    d = _get(db, doc_id)
    if d.status not in ("pending", "rejected"):
        raise conflict("已发布或已下架的文档不能删除，请使用「下架」")
    if not (d.uploader_id == viewer.id or _is_manager(viewer)):
        raise forbidden("只能删除自己上传的文档")
    db.execute(update(ImportBatch).where(ImportBatch.kb_doc_id == d.id).values(kb_doc_id=None))
    db.delete(d)
    db.flush()
    invalidate_index()


def remove_document(db, viewer, doc_id, ip=None):
    from app.services import record_removals

    document = db.scalar(select(KbDocument).where(KbDocument.id == doc_id).with_for_update())
    if not document or not can_view(db, viewer, document):
        raise not_found("知识库文档")
    if not can_remove_document(db, viewer, document):
        raise forbidden("只有文档审核人可删除已发布或下架的文档")
    record_removals.mark(db, viewer, KbDocument.__tablename__, doc_id, name=document.title, ip=ip)
    invalidate_index()


def add_local_document(db: Session, title: str, category: str, content: str, url: str | None = None,
                       source_doc_id: str | None = None) -> KbDocument:
    """其他模块沉淀知识（source=local，如客服已解决工单经验、生成的方案）。同 source_doc_id 覆盖更新；全员可见、直接发布。"""
    sid = source_doc_id or f"local-{uuid.uuid4().hex[:12]}"
    d = db.scalar(select(KbDocument).where(KbDocument.source_doc_id == sid).execution_options(include_removed_records=True))
    if d is None:
        d = KbDocument(source="local", source_doc_id=sid, url="", status="published", visibility="all",
                       visible_depts=[], visible_roles=[], tags=[])
        db.add(d)
    d.title, d.category, d.content_text, d.version = title[:200], category, content, "local"
    d.source_updated_at = d.updated_at = utcnow()
    db.flush()
    d.url = url or f"{settings.public_base_url.rstrip('/')}/kb?doc={d.id}"
    _rebuild_chunks(db, d)
    db.flush()
    invalidate_index()
    return d


# ---------------------------------------------------------------- 演示语料（仅种子/测试）
def load_demo_corpus(db: Session, provider=None) -> int:
    """把乐享 Mock 语料写入知识库（source=lexiang），供本地开发与自动化测试使用。生产用 scripts/purge_demo_kb.py 清除。"""
    from app.integration.lexiang.mock import MockLexiang
    prov = provider or MockLexiang()
    existing = {d.source_doc_id: d for d in db.scalars(select(KbDocument).where(KbDocument.source == "lexiang"))}
    n = 0
    for dto in prov.list_documents():
        row = existing.get(dto["id"])
        if row is None:
            row = KbDocument(source="lexiang", source_doc_id=dto["id"], status="published", visibility="all",
                             visible_depts=[], visible_roles=[], tags=[])
            db.add(row)
        content = dto.get("content")
        if content is None:
            content = prov.get_document(dto["id"])["content"] or ""
        row.title, row.category, row.url = dto["title"][:200], dto["category"] or "产品", dto["url"]
        row.content_text, row.version, row.source_updated_at = content, dto.get("version"), dto.get("updated_at")
        row.updated_at = utcnow()
        db.flush()
        _rebuild_chunks(db, row)
        n += 1
    db.flush()
    invalidate_index()
    return n


def purge_demo_corpus(db: Session) -> dict:
    """删除演示语料（source=lexiang）。上传文档与系统沉淀文档（local）不受影响。"""
    ids = list(db.scalars(select(KbDocument.id).where(KbDocument.source == "lexiang")))
    chunks = db.scalar(select(func.count(KbChunk.id)).where(KbChunk.doc_id.in_(ids))) if ids else 0
    if ids:
        db.execute(update(ImportBatch).where(ImportBatch.kb_doc_id.in_(ids)).values(kb_doc_id=None))
        db.execute(delete(KbChunk).where(KbChunk.doc_id.in_(ids)))
        db.execute(delete(KbDocument).where(KbDocument.id.in_(ids)))
    db.flush()
    invalidate_index()
    return {"docs": len(ids), "chunks": chunks or 0}


__all__ = ["search", "search_any", "get_doc", "add_local_document", "categories", "category_tree", "list_documents",
           "upload_document", "update_meta", "review", "set_archived", "delete_document", "suggest_category",
           "can_view", "can_review", "stats", "chunk_document", "tokenize", "query_terms", "highlight",
           "load_demo_corpus", "purge_demo_corpus", "ensure_categories", "save_category", "delete_category"]
