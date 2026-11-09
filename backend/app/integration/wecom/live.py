"""企业微信 Live 适配器：真实开放平台 API（gettoken 缓存、错误码重试、频控退避）。"""
import json
import re
import threading
import time
from datetime import datetime, timedelta

import httpx

from app.core.config import settings
from app.core.timeutil import TZ
from app.integration.wecom.base import ApprovalDto, DeptDto, JournalDto, MediaDto, MessageDto, SendResult, UserDto


class WeComApiError(Exception):
    def __init__(self, errcode: int, errmsg: str):
        super().__init__(f"企微接口错误 {errcode}: {errmsg}")
        self.errcode = errcode


def normalize_contents(contents: list[dict]) -> dict[str, str]:
    """把 getapprovaldetail 的 apply_data.contents 归一化为 {控件标题: 文本值}。"""
    out: dict[str, str] = {}
    for c in contents or []:
        title = next((t.get("text") for t in c.get("title", []) if t.get("lang", "zh_CN") == "zh_CN"), None) \
            or (c.get("title") or [{}])[0].get("text") or c.get("id")
        v = c.get("value") or {}
        ctl = c.get("control")
        if ctl in ("Text", "Textarea"):
            val = v.get("text", "")
        elif ctl == "Number":
            val = str(v.get("new_number", v.get("number", "")))
        elif ctl == "Money":
            val = str(v.get("new_money", v.get("money", "")))
        elif ctl == "Date":
            ts = int((v.get("date") or {}).get("s_timestamp") or 0)
            val = datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d") if ts else ""
        elif ctl == "Selector":
            opts = (v.get("selector") or {}).get("options") or []
            val = ",".join(t.get("text", "") for o in opts for t in o.get("value", [])[:1])
        elif ctl == "Contact":
            val = ",".join(m.get("userid", "") for m in v.get("members", []))
        elif ctl == "Table":
            val = str(v.get("children", ""))
        else:
            val = str(v.get("text") or "")
        out[title] = val
    return out


class LiveWeCom:
    _lock = threading.Lock()
    _token: str | None = None
    _expire_at: float = 0

    def __init__(self):
        if not (settings.wecom_corp_id and settings.wecom_secret):
            raise RuntimeError("企微 Live 模式需配置 WECOM_CORP_ID / WECOM_SECRET")
        self.http = httpx.Client(base_url=settings.wecom_api_base, timeout=10)

    # access_token 有效期 7200s，提前 300s 刷新
    def _access_token(self, refresh: bool = False) -> str:
        with self._lock:
            if refresh or not LiveWeCom._token or time.time() > LiveWeCom._expire_at - 300:
                r = self.http.get("/cgi-bin/gettoken", params={"corpid": settings.wecom_corp_id,
                                                               "corpsecret": settings.wecom_secret}).json()
                if r.get("errcode"):
                    raise WeComApiError(r["errcode"], r.get("errmsg", ""))
                LiveWeCom._token, LiveWeCom._expire_at = r["access_token"], time.time() + r["expires_in"]
            return LiveWeCom._token

    def _call(self, method: str, path: str, *, params: dict | None = None, json: dict | None = None) -> dict:
        code, r = 0, {}
        for attempt in range(3):
            q = {"access_token": self._access_token(refresh=code in (40014, 42001)), **(params or {})}
            r = self.http.request(method, path, params=q, json=json).json()
            code = r.get("errcode", 0)
            if code == 0:
                return r
            if code in (40014, 42001):  # token 失效
                continue
            if code in (45009, 45033, -1):  # 频控/系统繁忙：退避重试
                time.sleep(1.5 * (attempt + 1))
                continue
            raise WeComApiError(code, r.get("errmsg", ""))
        raise WeComApiError(code, r.get("errmsg", "重试仍失败"))

    def get_addressbook(self):
        depts = [DeptDto(d["id"], d["name"], d.get("parentid") or None)
                 for d in self._call("GET", "/cgi-bin/department/list")["department"]]
        users: dict[str, UserDto] = {}
        for d in depts:
            for u in self._call("GET", "/cgi-bin/user/list", params={"department_id": d.wecom_dept_id}).get("userlist", []):
                users[u["userid"]] = UserDto(u["userid"], u["name"], u.get("department", []), u.get("position"),
                                             u.get("mobile"), u.get("email"), u.get("status", 1))
        return depts, list(users.values())

    def get_approval_list(self, start: datetime, end: datetime) -> list[str]:
        out, cursor = [], ""
        while True:
            r = self._call("POST", "/cgi-bin/oa/getapprovalinfo", json={
                "starttime": int(start.timestamp()), "endtime": int(end.timestamp()), "new_cursor": cursor, "size": 100})
            out += r.get("sp_no_list", [])
            cursor = r.get("new_next_cursor") or ""
            if not cursor:
                return out

    def get_approval_detail(self, sp_no: str) -> ApprovalDto:
        info = self._call("POST", "/cgi-bin/oa/getapprovaldetail", json={"sp_no": sp_no})["info"]
        approved = None
        for node in info.get("sp_record", []):
            for d in node.get("details", []):
                if d.get("sp_status") == 2 and d.get("sptime"):
                    approved = max(approved or 0, d["sptime"])
        return ApprovalDto(
            sp_no=info["sp_no"], template_id=info.get("template_id", ""), sp_name=info.get("sp_name", ""),
            sp_status=info.get("sp_status", 0), apply_time=datetime.fromtimestamp(info.get("apply_time", 0), TZ),
            applicant_userid=(info.get("applyer") or {}).get("userid", ""),
            fields=normalize_contents((info.get("apply_data") or {}).get("contents", [])),
            approved_time=datetime.fromtimestamp(approved, TZ) if approved else None, raw=info)

    def send_app_message(self, user_ids: list[str], content: MessageDto) -> SendResult:
        body: dict = {"touser": "|".join(user_ids), "msgtype": content.msg_type, "agentid": settings.wecom_agent_id}
        if content.msg_type == "textcard":
            body["textcard"] = {"title": content.title, "description": content.content[:500],
                                "url": content.url or settings.public_base_url, "btntxt": "查看详情"}
        elif content.msg_type == "markdown":
            link = f"\n[查看详情]({content.url})" if content.url else ""
            body["markdown"] = {"content": f"**{content.title}**\n{content.content}{link}"}
        else:
            body["text"] = {"content": f"{content.title}\n{content.content}"}
        try:
            r = self._call("POST", "/cgi-bin/message/send", json=body)
        except WeComApiError as e:
            return SendResult(False, error=str(e))
        invalid = [u for u in (r.get("invaliduser") or "").split("|") if u]
        return SendResult(True, r.get("msgid"), invalid)

    def get_journal_records(self, start: datetime, end: datetime) -> list[JournalDto]:
        """企微汇报单次查询跨度不能超过一个月，按 30 天切窗后逐页拉取。"""
        out: list[JournalDto] = []
        cursor_time = start
        while cursor_time < end:
            window_end = min(cursor_time + timedelta(days=30), end)
            out.extend(self._journal_window(cursor_time, window_end))
            cursor_time = window_end
        return out

    def _journal_window(self, start: datetime, end: datetime) -> list[JournalDto]:
        out, cursor = [], 0
        while True:
            r = self._call("POST", "/cgi-bin/oa/journal/get_record_list", json={
                "starttime": int(start.timestamp()), "endtime": int(end.timestamp()), "cursor": cursor, "limit": 100})
            for uuid in r.get("journaluuid_list", []):
                info = self._call("POST", "/cgi-bin/oa/journal/get_record_detail", json={"journaluuid": uuid})["info"]
                out.append(JournalDto(uuid, (info.get("submitter") or {}).get("userid", ""), info.get("template_name", ""),
                                      datetime.fromtimestamp(info.get("report_time", 0), TZ), info.get("apply_data")))
            if r.get("endflag", 1) == 1:
                return out
            cursor = r.get("next_cursor", 0)

    def exchange_user_info(self, code: str) -> str:
        r = self._call("GET", "/cgi-bin/auth/getuserinfo", params={"code": code})
        uid = r.get("userid") or r.get("UserId")
        if not uid:
            raise WeComApiError(40029, "非企业成员或 code 无效")
        return uid

    MAX_MEDIA = 50 * 1024 * 1024

    def get_media(self, media_id: str) -> MediaDto:
        """获取临时素材（成员发给应用的图片/语音/视频，3 天内有效）。成功时返回二进制，失败时返回 JSON 错误。"""
        from urllib.parse import unquote
        for attempt in range(2):
            with self.http.stream("GET", "/cgi-bin/media/get", params={"access_token": self._access_token(refresh=attempt > 0),
                                                                       "media_id": media_id}) as r:
                ctype = r.headers.get("content-type", "")
                if "json" in ctype or "text/plain" in ctype:
                    err = json.loads(r.read() or b"{}")
                    code = err.get("errcode", -1)
                    if code in (40014, 42001) and attempt == 0:
                        continue
                    raise WeComApiError(code, err.get("errmsg", "获取素材失败"))
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf += chunk
                    if len(buf) > self.MAX_MEDIA:
                        raise WeComApiError(-2, "素材超过 50MB")
                cd = r.headers.get("content-disposition", "")
                m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", cd)
                name = unquote(m.group(1)) if m else f"{media_id[:12]}"
                return MediaDto(bytes(buf), name, ctype.split(";")[0] or None)
        raise WeComApiError(-1, "获取素材失败")

    def create_menu(self, menu: dict) -> None:
        self._call("POST", "/cgi-bin/menu/create", params={"agentid": settings.wecom_agent_id}, json=menu)
