"""企业微信 Mock 适配器：数据存于 JSON 文件，接口行为与 Live 一致。

- 通讯录来自 scripts/roster.py（与种子数据同源）
- 目录：{STORAGE_DIR}/../mock/wecom/
- 审批单：approvals.json，可由 /api/v1/dev/mock/wecom-approval 写入并触发加密回调
- 汇报记录：journals.json
- 应用消息：写入 outbox.jsonl（验收时可复核推送内容）
"""
import json
import threading
import uuid
from datetime import datetime
from pathlib import Path

from app.core.config import settings
from app.core.timeutil import TZ, now
from app.integration.wecom.base import ApprovalDto, DeptDto, JournalDto, MessageDto, SendResult, UserDto

# Mock 模式下的回调凭证（与企微后台配置格式一致，便于全链路走真实加解密）
MOCK_CALLBACK_TOKEN = "jcMockToken"
MOCK_AES_KEY = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"
MOCK_CORP_ID = "wwjcmock0000000001"

_lock = threading.Lock()


def mock_dir() -> Path:
    return Path(settings.storage_dir).parent / "mock" / "wecom"


def _path(name: str) -> Path:
    MOCK_DIR = mock_dir()
    MOCK_DIR.mkdir(parents=True, exist_ok=True)
    return MOCK_DIR / name


def _load(name: str) -> list:
    p = _path(name)
    return json.loads(p.read_text("utf-8")) if p.exists() else []


def _dump(name: str, data: list) -> None:
    _path(name).write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), "utf-8")


def put_approval(a: dict) -> None:
    """写入/更新一张 Mock 审批单（键 sp_no）。字段：sp_no, template_id, sp_name, sp_status, apply_time(iso),
    applicant_userid, fields{}, approved_time(iso)?"""
    with _lock:
        items = [x for x in _load("approvals.json") if x["sp_no"] != a["sp_no"]]
        items.append(a)
        _dump("approvals.json", items)


def put_journals(items: list[dict]) -> None:
    with _lock:
        _dump("journals.json", items)


def read_outbox(limit: int = 50) -> list[dict]:
    p = _path("outbox.jsonl")
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text("utf-8").splitlines()[-limit:]]


class MockWeCom:
    def get_addressbook(self):
        from scripts.roster import DEPT_WECOM_IDS, ROSTER
        depts = [DeptDto(1, "广东聚成数智科技有限公司", None)] + [DeptDto(wid, name, 1) for wid, name in DEPT_WECOM_IDS.values()]
        users = [UserDto(u["userid"], u["name"], [DEPT_WECOM_IDS[u["dept"]][0]], u["position"], u.get("mobile"),
                         f"{u['userid']}@jc-digital.cn", 5 if u.get("left") else 1) for u in ROSTER]
        return depts, users

    def get_approval_list(self, start: datetime, end: datetime) -> list[str]:
        out = []
        for a in _load("approvals.json"):
            t = datetime.fromisoformat(a["apply_time"])
            if start <= t <= end:
                out.append(a["sp_no"])
        return out

    def get_approval_detail(self, sp_no: str) -> ApprovalDto:
        a = next((x for x in _load("approvals.json") if x["sp_no"] == sp_no), None)
        if not a:
            raise KeyError(f"审批单 {sp_no} 不存在")
        return ApprovalDto(sp_no=a["sp_no"], template_id=a["template_id"], sp_name=a.get("sp_name", ""),
                           sp_status=a["sp_status"], apply_time=datetime.fromisoformat(a["apply_time"]),
                           applicant_userid=a["applicant_userid"], fields=a.get("fields", {}),
                           approved_time=datetime.fromisoformat(a["approved_time"]) if a.get("approved_time") else None,
                           raw=a)

    def send_app_message(self, user_ids: list[str], content: MessageDto) -> SendResult:
        msgid = uuid.uuid4().hex[:16]
        rec = {"msgid": msgid, "to": user_ids, "type": content.msg_type, "title": content.title,
               "content": content.content, "url": content.url, "sent_at": now().isoformat()}
        with _lock, _path("outbox.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return SendResult(True, msgid)

    def get_journal_records(self, start: datetime, end: datetime) -> list[JournalDto]:
        out = []
        for j in _load("journals.json"):
            t = datetime.fromisoformat(j["report_time"])
            if t.tzinfo is None:
                t = t.replace(tzinfo=TZ)
            if start <= t <= end:
                out.append(JournalDto(j["report_id"], j["userid"], j["template_name"], t, j.get("content")))
        return out

    def exchange_user_info(self, code: str) -> str:
        # Mock OAuth：code 形如 mock-<userid>
        if not code.startswith("mock-"):
            raise ValueError("Mock 模式下 code 需为 mock-<userid>")
        return code[5:]
