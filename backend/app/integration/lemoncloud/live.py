"""柠檬云 Live：开放平台 HTTP 调用（专业版及以上开通开放 API，App ID + 密钥授权）。

凭证配置：settings.lemoncloud_app_id / lemoncloud_app_secret / lemoncloud_api_base（环境变量 LEMONCLOUD_*）。
接口路径/字段以柠檬云开放平台文档为准，集中定义在 PATHS / 字段映射函数中，联调时仅需调整这里。
- 鉴权：POST {base}/oauth/token {app_id, app_secret, grant_type=client_credentials} → access_token（缓存至过期前 60s）
- 业务接口统一带 Authorization: Bearer <token>，返回 {code:0, data:{list:[...], total}}；分页 page/page_size
- 错误：code != 0 抛 LemonCloudApiError；429/5xx 指数退避重试 3 次
"""
from __future__ import annotations

import threading
import time
from datetime import date
from decimal import Decimal

import httpx

from app.core.config import settings
from app.integration.lemoncloud.base import (CashJournalDto, DateRange, ProjectAuxDto, SubjectBalanceDto, VoucherDto)

PATHS = {
    "token": "/oauth/token",
    "vouchers": "/api/v1/voucher/list",
    "balances": "/api/v1/report/subject-balance",
    "cash_journal": "/api/v1/journal/list",
    "project_aux": "/api/v1/aux/project/summary",
}
PAGE_SIZE = 500


class LemonCloudApiError(Exception):
    def __init__(self, code, msg):
        super().__init__(f"柠檬云接口错误 {code}: {msg}")
        self.code = code


def _d(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(Decimal("0.01"))


class LiveLemonCloud:
    _lock = threading.Lock()
    _token: str | None = None
    _exp: float = 0

    def __init__(self, db=None, client: httpx.Client | None = None):
        if not settings.lemoncloud_app_id or not settings.lemoncloud_app_secret:
            raise LemonCloudApiError("config", "未配置 LEMONCLOUD_APP_ID / LEMONCLOUD_APP_SECRET")
        self.base = settings.lemoncloud_api_base.rstrip("/")
        self.http = client or httpx.Client(timeout=30)

    # ---- 基础 ----
    def _get_token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._exp - 60:
                return self._token
            r = self.http.post(self.base + PATHS["token"], json={
                "app_id": settings.lemoncloud_app_id, "app_secret": settings.lemoncloud_app_secret,
                "grant_type": "client_credentials"})
            body = r.json()
            if r.status_code != 200 or body.get("code", 0) != 0:
                raise LemonCloudApiError(body.get("code", r.status_code), body.get("msg") or body.get("message"))
            data = body.get("data") or body
            LiveLemonCloud._token = data["access_token"]
            LiveLemonCloud._exp = time.time() + int(data.get("expires_in", 7200))
            return LiveLemonCloud._token

    def _call(self, path: str, params: dict) -> dict:
        for attempt in range(4):
            r = self.http.post(self.base + path, json=params, headers={"Authorization": f"Bearer {self._get_token()}"})
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            body = r.json()
            code = body.get("code", 0)
            if code in (401, 40001, "TOKEN_EXPIRED") and attempt < 3:
                LiveLemonCloud._token = None
                continue
            if code != 0:
                raise LemonCloudApiError(code, body.get("msg") or body.get("message"))
            return body.get("data") or {}
        raise LemonCloudApiError("retry", "重试次数用尽")

    def _pages(self, path: str, params: dict) -> list[dict]:
        out, page = [], 1
        while True:
            data = self._call(path, {**params, "page": page, "page_size": PAGE_SIZE})
            rows = data.get("list") or []
            out += rows
            if len(rows) < PAGE_SIZE or len(out) >= int(data.get("total", len(out))):
                return out
            page += 1

    # ---- 契约 ----
    def fetch_vouchers(self, date_range: DateRange) -> list[VoucherDto]:
        out = []
        for v in self._pages(PATHS["vouchers"], {"start_date": date_range.start.isoformat(),
                                                  "end_date": date_range.end.isoformat()}):
            no = f"{v.get('word', '记')}-{int(v.get('num', v.get('voucher_num', 0))):03d}"
            vd = date.fromisoformat(str(v.get("date") or v.get("voucher_date"))[:10])
            for i, e in enumerate(v.get("entries") or v.get("lines") or [], start=1):
                aux = e.get("aux") or {}
                out.append(VoucherDto(no, i, vd, e.get("summary") or v.get("summary") or "",
                                      str(e.get("subject_code")), e.get("subject_name") or "",
                                      debit=_d(e.get("debit")), credit=_d(e.get("credit")),
                                      aux_project=aux.get("project_code") or aux.get("project")))
        return out

    def fetch_subject_balances(self, period: str) -> list[SubjectBalanceDto]:
        rows = self._pages(PATHS["balances"], {"period": period})
        return [SubjectBalanceDto(period, str(r["subject_code"]), r.get("subject_name", ""),
                                  opening_debit=_d(r.get("begin_debit")), opening_credit=_d(r.get("begin_credit")),
                                  debit_amount=_d(r.get("debit")), credit_amount=_d(r.get("credit")),
                                  debit_balance=_d(r.get("end_debit")), credit_balance=_d(r.get("end_credit")))
                for r in rows]

    def fetch_cash_journal(self, date_range: DateRange) -> list[CashJournalDto]:
        rows = self._pages(PATHS["cash_journal"], {"start_date": date_range.start.isoformat(),
                                                    "end_date": date_range.end.isoformat()})
        return [CashJournalDto(date.fromisoformat(str(r["date"])[:10]), r.get("account_name", ""),
                               1 if _d(r.get("income")) else 2, _d(r.get("income")) or _d(r.get("expense")),
                               r.get("summary", ""), r.get("voucher_no")) for r in rows]

    def fetch_project_aux(self, project_code: str) -> ProjectAuxDto:
        data = self._call(PATHS["project_aux"], {"aux_type": "project", "aux_code": project_code})
        return ProjectAuxDto(project_code, income=_d(data.get("income")), cost=_d(data.get("cost")),
                             subjects=data.get("subjects") or [])
