"""腾讯乐享 Mock：内置教育信息化企业文档语料（制度/售前/方案模板/案例/资质/客服手册，29 篇，每篇 800~3000 字）。

文档链接形如 {lexiang_web_base}/teams/k100054/docs/<id>，与 Live 一致，便于出处跳转演示。
"""
from datetime import datetime

from app.core.timeutil import TZ
from app.integration.lexiang import (corpus_cases, corpus_extra, corpus_policy, corpus_presales, corpus_service,
                                     corpus_templates)
from app.integration.lexiang.base import CATEGORIES

CORPUS: list[dict] = [*corpus_policy.DOCS, *corpus_presales.DOCS, *corpus_templates.DOCS, *corpus_cases.DOCS,
                      *corpus_service.DOCS, *corpus_extra.DOCS]
for _d in CORPUS:
    if _d["id"] in corpus_extra.EXTRA and corpus_extra.EXTRA[_d["id"]] not in _d["content"]:
        _d["content"] = _d["content"].rstrip() + "\n\n" + corpus_extra.EXTRA[_d["id"]]
    for _extra in (corpus_extra.EXTRA2, corpus_extra.EXTRA3, corpus_extra.EXTRA4):
        if _d["id"] in _extra and _extra[_d["id"]].strip() not in _d["content"]:
            _d["content"] = _d["content"].rstrip() + "\n\n" + _extra[_d["id"]].strip()
_BY_ID = {d["id"]: d for d in CORPUS}


def _dto(d: dict, with_content: bool = True) -> dict:
    from app.integration.lexiang import doc_url
    y, m, dd = map(int, d["updated"].split("-"))
    return {"id": d["id"], "title": d["title"], "category": d["category"], "url": doc_url(d["id"]),
            "content": d["content"] if with_content else None,
            "updated_at": datetime(y, m, dd, 10, 0, tzinfo=TZ), "version": d.get("version")}


class MockLexiang:
    def list_categories(self) -> list[dict]:
        counts: dict[str, int] = {}
        for d in CORPUS:
            counts[d["category"]] = counts.get(d["category"], 0) + 1
        return [{"id": c, "name": c, "count": counts.get(c, 0)} for c in CATEGORIES if counts.get(c)]

    def list_documents(self, updated_since: datetime | None = None) -> list[dict]:
        out = [_dto(d) for d in CORPUS]
        if updated_since:
            out = [d for d in out if d["updated_at"] > updated_since]
        return out

    def get_document(self, doc_id: str) -> dict:
        d = _BY_ID.get(doc_id)
        if not d:
            from app.core.response import not_found
            raise not_found("乐享文档")
        return _dto(d)
