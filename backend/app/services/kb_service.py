"""知识库服务（PRD FR-06）：腾讯乐享 → 本地镜像（kb_document / kb_chunk）+ 关键词检索。

- 主数据源为乐享，工作台不提供上传/版本管理；本地只做镜像与检索，出处 url 指回乐享原文。
- 分块：按小标题（## / 一、/ 第X章）切节，节内按段落合并，单块 ≤ 600 字；超长行按句号切分。
- 检索：中文 bigram + 英文/数字词，BM25 打分 + 标题/小标题加权 + 查询覆盖度；排序键 (-score, doc_id, seq) 稳定可复现。
- 索引常驻内存，按 (chunk 数, max chunk id, max 文档更新时间) 签名自动失效重建。
"""
import html
import math
import re
import threading
import time
import uuid
from datetime import datetime

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.response import not_found
from app.core.timeutil import utcnow
from app.models import KbChunk, KbDocument

CATEGORY_ORDER = ["制度", "售前", "方案模板", "案例", "资质", "客服", "产品"]
MAX_CHUNK = 600
SNIPPET_LEN = 140
K1, B = 1.2, 0.75

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
    sig = _signature(db)
    if _INDEX.sig == sig:
        return _INDEX
    with _LOCK:
        if _INDEX.sig == sig:
            return _INDEX
        q = (select(KbChunk.id, KbChunk.doc_id, KbChunk.seq, KbChunk.heading, KbChunk.chunk_text, KbChunk.terms,
                    KbDocument.title, KbDocument.category, KbDocument.url, KbDocument.source)
             .join(KbDocument, KbDocument.id == KbChunk.doc_id).order_by(KbChunk.doc_id, KbChunk.seq))
        rows, df, inv, total = [], {}, {}, 0
        for r in db.execute(q):
            i = len(rows)
            tset = set(r.terms or [])
            rows.append({"chunk_id": r.id, "doc_id": r.doc_id, "seq": r.seq, "heading": r.heading,
                         "text": r.chunk_text, "lower": r.chunk_text.lower(), "title": r.title,
                         "title_lower": r.title.lower(), "heading_lower": (r.heading or "").lower(),
                         "category": r.category, "url": r.url, "source": r.source, "terms": tset,
                         "len": max(len(r.chunk_text), 1)})
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
def _score_rows(idx: _Index, terms: list[str], categories: set[str] | None):
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
           per_doc: bool = True) -> list[dict]:
    """检索乐享镜像。返回 [{doc_id, title, category, url, heading, snippet(<mark>), score, chunk_id, seq}]。

    per_doc=True 时每篇文档只返回得分最高的一个分块（列表展示）；False 返回分块级结果（方案生成取材）。
    """
    terms = query_terms(query)
    if not terms:
        return []
    cats = {category} if isinstance(category, str) and category else (set(category) if category else None)
    idx = _index(db)
    scored, eff = _score_rows(idx, terms, cats)
    out, seen = [], set()
    for s, r in scored:
        if per_doc and r["doc_id"] in seen:
            continue
        seen.add(r["doc_id"])
        out.append({"doc_id": r["doc_id"], "title": r["title"], "category": r["category"], "url": r["url"],
                    "heading": r["heading"], "snippet": highlight(r["text"], eff), "score": s,
                    "chunk_id": r["chunk_id"], "seq": r["seq"], "text": r["text"], "source": r["source"]})
        if len(out) >= limit:
            break
    if per_doc:
        for o in out:
            o.pop("text")
    return out


# ---------------------------------------------------------------- 同步
def _rebuild_chunks(db: Session, doc: KbDocument) -> int:
    db.execute(delete(KbChunk).where(KbChunk.doc_id == doc.id))
    rows = chunk_document(doc.content_text)
    if rows:
        db.execute(insert(KbChunk), [{"doc_id": doc.id, "seq": c["seq"], "heading": c["heading"],
                                      "chunk_text": c["text"], "terms": c["terms"]} for c in rows])
    return len(rows)


def sync_state(db: Session) -> dict:
    from app.services import dict_service
    state = dict(dict_service.get(db, "kb_sync", "state", {}) or {})
    state["total_docs"] = db.scalar(select(func.count(KbDocument.id))) or 0
    state["total_chunks"] = db.scalar(select(func.count(KbChunk.id))) or 0
    state["mode"] = settings.source_mode("lexiang")
    return state


def sync(db: Session, full: bool = False, provider=None) -> dict:
    """乐享 → 本地镜像。按 source_doc_id upsert；增量按上次水位 updated_since；full 时重建全部分块并清理乐享已删文档。"""
    from app.integration.lexiang import get_lexiang
    from app.services import dict_service
    t0 = time.perf_counter()
    state = dict(dict_service.get(db, "kb_sync", "state", {}) or {})
    started = utcnow()
    try:
        prov = provider or get_lexiang()
        since = None
        if not full and state.get("watermark"):
            since = datetime.fromisoformat(state["watermark"])
        docs = prov.list_documents(updated_since=since)
        existing = {d.source_doc_id: d for d in db.scalars(select(KbDocument).where(KbDocument.source == "lexiang"))}
        added = updated = skipped = removed = chunks = 0
        watermark = since
        for dto in docs:
            if dto.get("updated_at") and (watermark is None or dto["updated_at"] > watermark):
                watermark = dto["updated_at"]
            row = existing.get(dto["id"])
            if row and not full and row.version == dto.get("version") and row.source_updated_at == dto.get("updated_at"):
                skipped += 1
                continue
            content = dto.get("content")
            if content is None:
                content = prov.get_document(dto["id"])["content"] or ""
            if row is None:
                row = KbDocument(source="lexiang", source_doc_id=dto["id"])
                db.add(row)
                added += 1
            else:
                updated += 1
            row.title, row.category, row.url = dto["title"][:200], dto["category"] or "产品", dto["url"]
            row.content_text, row.version, row.source_updated_at = content, dto.get("version"), dto.get("updated_at")
            row.updated_at = utcnow()
            db.flush()
            chunks += _rebuild_chunks(db, row)
        if full:
            alive = {d["id"] for d in docs}
            for sid, row in existing.items():
                if sid not in alive:
                    db.delete(row)
                    removed += 1
        state.update(last_run_at=started.isoformat(), last_success_at=utcnow().isoformat(), full=full,
                     added=added, updated=updated, skipped=skipped, removed=removed, chunks=chunks, error=None,
                     cost_ms=int((time.perf_counter() - t0) * 1000),
                     watermark=watermark.isoformat() if watermark else state.get("watermark"))
        dict_service.put(db, "kb_sync", "state", state, remark="乐享同步状态（系统维护）")
        db.commit()
    except Exception as exc:
        db.rollback()
        state.update(last_run_at=started.isoformat(), error=str(exc)[:300])
        dict_service.put(db, "kb_sync", "state", state)
        db.commit()
        raise
    finally:
        invalidate_index()
    return sync_state(db)


# ---------------------------------------------------------------- 查询 / 沉淀
def _doc_brief(d: KbDocument) -> dict:
    return {"id": d.id, "source": d.source, "source_doc_id": d.source_doc_id, "title": d.title,
            "category": d.category, "url": d.url, "version": d.version, "source_updated_at": d.source_updated_at,
            "size": len(d.content_text or "")}


def categories(db: Session) -> list[dict]:
    rows = db.execute(select(KbDocument.category, func.count(KbDocument.id)).group_by(KbDocument.category)).all()
    counts = dict(rows)
    order = CATEGORY_ORDER + sorted(c for c in counts if c not in CATEGORY_ORDER)
    return [{"name": c, "count": counts[c]} for c in order if counts.get(c)]


def list_documents(db: Session, category: str | list[str] | None = None, keyword: str | None = None) -> list[dict]:
    q = select(KbDocument).order_by(KbDocument.category, KbDocument.title)
    if isinstance(category, str) and category:
        q = q.where(KbDocument.category == category)
    elif category:
        q = q.where(KbDocument.category.in_(list(category)))
    if keyword:
        q = q.where(KbDocument.title.ilike(f"%{keyword}%"))
    return [_doc_brief(d) for d in db.scalars(q)]


def get_doc(db: Session, doc_id: int | str) -> dict:
    d = db.get(KbDocument, int(doc_id)) if str(doc_id).isdigit() else None
    if d is None:
        d = db.scalar(select(KbDocument).where(KbDocument.source_doc_id == str(doc_id)))
    if d is None:
        raise not_found("知识库文档")
    chunks = db.scalars(select(KbChunk).where(KbChunk.doc_id == d.id).order_by(KbChunk.seq)).all()
    return {**_doc_brief(d), "content_text": d.content_text,
            "chunks": [{"id": c.id, "seq": c.seq, "heading": c.heading, "text": c.chunk_text} for c in chunks]}


def add_local_document(db: Session, title: str, category: str, content: str, url: str | None = None,
                       source_doc_id: str | None = None) -> KbDocument:
    """其他模块沉淀知识（source=local，如客服已解决工单经验、生成的方案）。同 source_doc_id 覆盖更新。"""
    sid = source_doc_id or f"local-{uuid.uuid4().hex[:12]}"
    d = db.scalar(select(KbDocument).where(KbDocument.source_doc_id == sid))
    if d is None:
        d = KbDocument(source="local", source_doc_id=sid, url="")
        db.add(d)
    d.title, d.category, d.content_text, d.version = title[:200], category, content, "local"
    d.source_updated_at = d.updated_at = utcnow()
    db.flush()
    d.url = url or f"{settings.public_base_url.rstrip('/')}/kb?doc={d.id}"
    _rebuild_chunks(db, d)
    db.flush()
    invalidate_index()
    return d


def search_any(db: Session, query: str, limit: int = 5) -> list[dict]:
    """兼容别名：全分类检索。"""
    return search(db, query, None, limit)


__all__ = ["search", "sync", "sync_state", "get_doc", "add_local_document", "categories", "list_documents",
           "chunk_document", "tokenize", "query_terms", "highlight"]
