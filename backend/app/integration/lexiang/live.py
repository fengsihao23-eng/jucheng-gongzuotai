"""腾讯乐享开放 API 实现（Live）。

接口（乐享开放平台 lxapi，JSON:API 风格响应）：
- 获取令牌：POST {api_base}/token  {grant_type: client_credentials, app_key, app_secret} → {access_token, expires_in}
- 文档列表：GET  {api_base}/v1/docs?page=&per_page=&sort=-updated_at   （分页，links.next / meta.last_page）
- 文档详情：GET  {api_base}/v1/docs/{id}?include=category              （attributes.content 为 HTML）
- 分类目录：GET  {api_base}/v1/categories?page=&per_page=
请求头：Authorization: Bearer <token>；StaffID 为调用身份（取 LEXIANG_STAFF_ID 环境变量，缺省 admin）。

注意：上线前须用客户侧开通的应用凭证对照乐享最新开放文档核对路径/字段，差异只改本文件。
"""
import html
import os
import re
import threading
import time
from datetime import datetime

import httpx

from app.core.config import settings
from app.core.response import BizError
from app.integration.lexiang import doc_url

PER_PAGE = 50
MAX_PAGES = 200
_TOKEN_LOCK = threading.Lock()
_TOKEN: dict = {"value": None, "exp": 0.0}

# 乐享分类名 → 工作台分类（未映射的按关键词归类，兜底“产品”）
CATEGORY_HINTS = [("制度", "制度"), ("规章", "制度"), ("售前", "售前"), ("解决方案", "售前"), ("模板", "方案模板"),
                  ("案例", "案例"), ("资质", "资质"), ("证书", "资质"), ("客服", "客服"), ("排查", "客服"),
                  ("运维", "客服")]


def _map_category(name: str | None) -> str:
    for hint, cat in CATEGORY_HINTS:
        if name and hint in name:
            return cat
    return "产品"


def html_to_text(src: str) -> str:
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", src or "")
    s = re.sub(r"(?i)<h([1-4])[^>]*>", lambda m: "\n" + "#" * (int(m.group(1)) + 1) + " ", s)
    s = re.sub(r"(?i)</h[1-4]>|<br\s*/?>|</p>|</li>|</tr>|</div>", "\n", s)
    s = re.sub(r"(?i)<li[^>]*>", "- ", s)
    s = re.sub(r"(?i)</t[dh]>", " | ", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t　]+\n", "\n", s)).strip()


def _parse_dt(v) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


class LiveLexiang:
    def __init__(self):
        if not (settings.lexiang_app_key and settings.lexiang_app_secret):
            raise BizError(50201, "乐享凭证未配置（LEXIANG_APP_KEY / LEXIANG_APP_SECRET）")
        self.base = settings.lexiang_api_base.rstrip("/")
        self.staff = os.environ.get("LEXIANG_STAFF_ID", "admin")

    # ---------- token（进程内缓存，提前 5 分钟刷新） ----------
    def _token(self) -> str:
        with _TOKEN_LOCK:
            if _TOKEN["value"] and _TOKEN["exp"] - 300 > time.time():
                return _TOKEN["value"]
            r = httpx.post(f"{self.base}/token", timeout=20, json={
                "grant_type": "client_credentials", "app_key": settings.lexiang_app_key,
                "app_secret": settings.lexiang_app_secret})
            body = r.json() if r.content else {}
            if r.status_code != 200 or "access_token" not in body:
                raise BizError(50201, f"乐享获取令牌失败：{body.get('message') or r.status_code}")
            _TOKEN.update(value=body["access_token"], exp=time.time() + int(body.get("expires_in", 7200)))
            return _TOKEN["value"]

    def _get(self, path: str, params: dict | None = None) -> dict:
        for attempt in range(2):
            r = httpx.get(f"{self.base}{path}", params=params, timeout=30,
                          headers={"Authorization": f"Bearer {self._token()}", "StaffID": self.staff})
            if r.status_code == 401 and attempt == 0:
                _TOKEN["value"] = None  # 令牌失效，刷新重试一次
                continue
            if r.status_code != 200:
                raise BizError(50201, f"乐享接口 {path} 返回 {r.status_code}")
            return r.json()
        raise BizError(50201, "乐享鉴权失败")

    def _paged(self, path: str, params: dict | None = None):
        page = 1
        while page <= MAX_PAGES:
            body = self._get(path, {**(params or {}), "page": page, "per_page": PER_PAGE})
            rows = body.get("data") or []
            yield from rows
            meta, links = body.get("meta") or {}, body.get("links") or {}
            last = meta.get("last_page") or meta.get("total_pages")
            if not rows or (last and page >= int(last)) or (not last and not links.get("next")):
                break
            page += 1

    # ---------- 契约 ----------
    def list_categories(self) -> list[dict]:
        out = []
        for row in self._paged("/v1/categories"):
            a = row.get("attributes") or {}
            out.append({"id": row.get("id"), "name": _map_category(a.get("name")), "raw_name": a.get("name"),
                        "count": a.get("docs_count") or 0})
        return out

    def _to_dto(self, row: dict, with_content: bool) -> dict:
        a = row.get("attributes") or {}
        cat = (((row.get("relationships") or {}).get("category") or {}).get("data") or {})
        cat_name = a.get("category_name") or cat.get("name")
        return {"id": str(row.get("id")), "title": a.get("name") or a.get("title") or "未命名文档",
                "category": _map_category(cat_name), "url": a.get("link") or doc_url(str(row.get("id"))),
                "content": html_to_text(a.get("content") or "") if with_content else None,
                "updated_at": _parse_dt(a.get("updated_at")), "version": str(a.get("version") or "")}

    def list_documents(self, updated_since: datetime | None = None) -> list[dict]:
        out = []
        for row in self._paged("/v1/docs", {"sort": "-updated_at"}):
            dto = self._to_dto(row, with_content=False)
            if updated_since and dto["updated_at"] and dto["updated_at"] <= updated_since:
                break  # 按更新时间倒序，之后都是旧文档
            out.append(dto)
        return out

    def get_document(self, doc_id: str) -> dict:
        body = self._get(f"/v1/docs/{doc_id}", {"include": "category"})
        return self._to_dto(body.get("data") or {}, with_content=True)
