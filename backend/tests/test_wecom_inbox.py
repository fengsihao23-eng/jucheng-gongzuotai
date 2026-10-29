"""企业微信应用内对话（文字问小聚 / 搜知识库 / 图片入资料导入 / 菜单）、智能识别与 zip 批量导入。

运行：cd backend && TEST_DB=jucheng_test_wx .venv/bin/python -m pytest tests/test_wecom_inbox.py -q
"""
import io
import json
import time
import uuid
import zipfile

import pytest
from sqlalchemy import select

from tests.conftest import data

EMP, YX = "zengli", "songjia"


def _post(client, xml: str):
    from app.integration.wecom import get_crypto
    c = get_crypto()
    ts, nonce = str(int(time.time())), uuid.uuid4().hex[:8]
    body = c.encrypt_msg(xml, ts, nonce)
    sig = body.split("<MsgSignature><![CDATA[")[1].split("]]>")[0]
    r = client.post("/api/v1/wecom/callback", params={"msg_signature": sig, "timestamp": ts, "nonce": nonce}, content=body)
    assert r.text == "success"


def _msg(user: str, msg_type: str, msg_id: str | None = None, **fields) -> str:
    from app.integration.wecom.mock import MOCK_CORP_ID
    extra = "".join(f"<{k}><![CDATA[{v}]]></{k}>" for k, v in fields.items())
    mid = f"<MsgId>{msg_id or uuid.uuid4().int % 10**18}</MsgId>" if msg_type != "event" else ""
    return (f"<xml><ToUserName><![CDATA[{MOCK_CORP_ID}]]></ToUserName><FromUserName><![CDATA[{user}]]></FromUserName>"
            f"<CreateTime>{int(time.time())}</CreateTime><MsgType><![CDATA[{msg_type}]]></MsgType>{extra}{mid}"
            f"<AgentID>1000002</AgentID></xml>")


def _replies(to: str, since: int) -> list[dict]:
    from app.integration.wecom.mock import read_outbox
    return [m for m in read_outbox(500)[since:] if m["to"] == [to]]


def _outbox_len() -> int:
    from app.integration.wecom.mock import read_outbox
    return len(read_outbox(100000))


@pytest.fixture
def sync_waiter(monkeypatch):
    """小聚回复改为同步：直接等待任务结束再断言。"""
    from app.services import wecom_inbox_service as svc
    monkeypatch.setattr(svc, "start_waiter", lambda *a: svc.wait_and_reply(*a, poll=0.05))


def test_help_and_kb_search_reply(client):
    n = _outbox_len()
    _post(client, _msg(EMP, "text", Content="帮助"))
    _post(client, _msg(EMP, "text", Content="搜 年休假"))
    out = _replies(EMP, n)
    assert "上传资料" in out[0]["content"]
    assert "在知识库找到" in out[1]["content"] and "年休假" in out[1]["content"] and "/kb?q=" in out[1]["content"]


def test_duplicate_callback_handled_once(client, db):
    from app.models import SysWecomInbound
    n = _outbox_len()
    mid = str(uuid.uuid4().int % 10**18)
    for _ in range(3):  # 企微超时重试
        _post(client, _msg(EMP, "text", msg_id=mid, Content="帮助"))
    assert len(_replies(EMP, n)) == 1
    rows = db.scalars(select(SysWecomInbound).where(SysWecomInbound.msg_id == mid)).all()
    assert len(rows) == 1 and rows[0].status == "done" and rows[0].preview == "帮助"


def test_unknown_member_is_told_not_enabled(client, db):
    from app.models import SysWecomInbound
    n = _outbox_len()
    _post(client, _msg("stranger-001", "text", Content="查一下合同"))
    out = _replies("stranger-001", n)
    assert out and "没有开通" in out[0]["content"]
    row = db.scalar(select(SysWecomInbound).where(SysWecomInbound.wecom_userid == "stranger-001"))
    assert row.status == "ignored" and row.user_id is None


def test_text_goes_to_xiaoju_and_reply_comes_back(client, monkeypatch, sync_waiter, db):
    from app.services import xiaoju_service
    calls = []

    def fake_create(message, context, user_id, session_id=None, current_page="", **kw):
        calls.append((message, user_id, session_id, current_page))
        return {"id": f"task-{len(calls)}", "session_id": session_id, "state": "queued"}

    states = {"task-1": {"state": "succeeded", "message": "本月新签合同 3 份，合计 128 万元。", "links": [], "business_results": []},
              "task-2": {"state": "awaiting_confirmation", "message": "已生成商机登记单：智慧食堂，30 万元。", "links": []}}
    monkeypatch.setattr(xiaoju_service, "runtime_info", lambda: {"available": True})
    monkeypatch.setattr(xiaoju_service, "create", fake_create)
    monkeypatch.setattr(xiaoju_service, "read", lambda task_id, user_id: {"id": task_id, **states[task_id]})
    n = _outbox_len()
    _post(client, _msg(YX, "text", Content="这个月新签了多少合同"))
    _post(client, _msg(YX, "text", Content="帮我登记一个商机，智慧食堂 30 万"))
    out = [m["content"] for m in _replies(YX, n)]
    assert out[0] == "收到，正在处理…"
    assert "128 万元" in out[1]
    assert "需要你确认" in out[3] and "/xiaoju/" in out[3]
    # 同一成员沿用同一个企业微信对话
    assert calls[0][2] == calls[1][2] and "企业微信" in calls[0][3]
    from app.models import SysUser
    u = db.scalar(select(SysUser).where(SysUser.wecom_userid == YX))
    db.refresh(u)
    assert u.preferences["wecom_chat"]["session_id"] == calls[0][2]


def test_text_falls_back_to_kb_when_xiaoju_unavailable(client, monkeypatch):
    from app.services import xiaoju_service
    monkeypatch.setattr(xiaoju_service, "runtime_info", lambda: {"available": False})
    n = _outbox_len()
    _post(client, _msg(EMP, "text", Content="报销流程是怎样的"))
    out = _replies(EMP, n)
    assert "小聚暂时不可用" in out[0]["content"] and "知识库" in out[0]["content"]


def test_image_goes_to_import_pool(client, login):
    from app.integration.wecom.mock import put_media
    media = f"media-{uuid.uuid4().hex[:8]}"
    put_media(media, b"\x89PNG\r\n\x1a\n" + uuid.uuid4().bytes, "", "image/png")
    n = _outbox_len()
    _post(client, _msg(EMP, "image", MediaId=media, PicUrl="https://example.invalid/p.png"))
    out = _replies(EMP, n)
    assert out[0]["type"] == "textcard" and "整理人" in out[0]["content"] and out[0]["url"].endswith("/imports")
    mine = data(client.get("/api/v1/imports", headers=login(EMP)))["items"]
    b = next(x for x in mine if x["filename"].startswith("企业微信图片-"))
    assert b["channel"] == "wecom" and b["status"] == "pool" and b["filename"].endswith(".png")


def test_file_message_routes_document_to_kb(client):
    """客户端若下发文件消息（非官方保证），同样进入资料导入。"""
    from docx import Document
    from app.integration.wecom.mock import put_media
    buf = io.BytesIO()
    d = Document()
    d.add_paragraph(f"员工出差住宿标准与报销流程说明 {uuid.uuid4().hex[:6]}，差旅费用按级别执行。")
    d.save(buf)
    media = f"media-{uuid.uuid4().hex[:8]}"
    put_media(media, buf.getvalue(), "差旅报销管理办法.docx")
    n = _outbox_len()
    _post(client, _msg(EMP, "file", MediaId=media, FileName="差旅报销管理办法.docx"))
    out = _replies(EMP, n)
    assert "知识库资料" in out[0]["content"] and "制度" in out[0]["content"]


def test_expired_media_tells_user(client):
    n = _outbox_len()
    _post(client, _msg(EMP, "image", MediaId="media-gone"))
    assert "过期" in _replies(EMP, n)[0]["content"]


def test_menu_click_and_push(client, login):
    n = _outbox_len()
    _post(client, _msg(YX, "event", Event="click", EventKey="my_imports"))
    _post(client, _msg(YX, "event", Event="click", EventKey="help"))
    out = _replies(YX, n)
    assert ("最近的导入" in out[0]["content"] or "还没有导入" in out[0]["content"]) and "上传资料" in out[1]["content"]
    # enter_agent 等其他事件不回复
    n = _outbox_len()
    _post(client, _msg(YX, "event", Event="enter_agent"))
    assert not _replies(YX, n)
    assert client.post("/api/v1/admin/wecom/menu", headers=login(EMP)).json()["code"] == 40301
    menu = data(client.post("/api/v1/admin/wecom/menu", headers=login("admin")))["menu"]
    from app.integration.wecom.mock import read_menu
    assert read_menu() == menu
    assert [b["name"] for b in menu["button"]] == ["上传资料", "知识库", "更多"]
    assert menu["button"][0]["url"].endswith("/imports") and len(menu["button"][2]["sub_button"]) <= 5


# ============================================================ 智能识别
def _fake_llm(monkeypatch, answer: dict | None):
    from app.integration import llm
    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(llm, "complete", lambda *a, **k: json.dumps(answer, ensure_ascii=False) if answer else None)


def test_describe_document_uses_llm_and_validates(monkeypatch, db):
    from app.services import smart_intake
    _fake_llm(monkeypatch, {"title": "智慧安防项目实施方案", "summary": "某中学安防改造的实施步骤。", "tags": ["综合智慧安防", "2026"],
                            "category": "方案模板", "subcategory": "实施方案", "reason": "正文是实施步骤"})
    r = smart_intake.describe_document(db, "安防方案(1)-最终版.docx", "第一章 项目概况……")
    assert r == {"title": "智慧安防项目实施方案", "summary": "某中学安防改造的实施步骤。", "tags": ["综合智慧安防", "2026"],
                 "category": "方案模板", "subcategory": "实施方案", "reason": "正文是实施步骤", "source": "llm"}
    # 编造的分类不采纳，退回规则；不存在的二级分类置空
    _fake_llm(monkeypatch, {"title": "x", "category": "不存在的分类", "subcategory": "y"})
    r = smart_intake.describe_document(db, "考勤管理制度.docx", "迟到早退按考勤制度处理。")
    assert r["category"] == "制度" and r["source"] == "llm"
    _fake_llm(monkeypatch, {"title": "x", "category": "售前", "subcategory": "编造的"})
    assert smart_intake.describe_document(db, "a.docx", "正文")["subcategory"] is None
    # 模型失败：规则兜底，并清理文件名噪声
    _fake_llm(monkeypatch, None)
    r = smart_intake.describe_document(db, "考勤管理制度-副本(2).docx", "迟到早退按考勤制度处理。")
    assert r["source"] == "rule" and r["title"] == "考勤管理制度" and r["category"] == "制度"


def _xlsx(rows):
    from openpyxl import Workbook
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_table_target_by_llm_when_rules_fail(client, login, monkeypatch):
    yx = login(YX)
    tag = uuid.uuid4().hex[:6]
    content = _xlsx([["单位", "签了多少钱", "哪天签的", "做什么的"], [f"某中学{tag}", "128000", "2026-09-15", "数字校园"]])
    _fake_llm(monkeypatch, {"target": "contract", "description": "已签合同清单", "reason": "有单位、金额与签约日期"})
    b = data(client.post("/api/v1/imports", files={"file": (f"表{tag}.xlsx", content)}, headers=yx))
    assert b["route"] == "clean" and b["target"] == "contract" and "AI 判断" in b["suggestion"]["reason"]
    _fake_llm(monkeypatch, {"target": "none", "description": "车辆使用登记", "reason": "车牌与里程"})
    b = data(client.post("/api/v1/imports", files={"file": (f"车{tag}.xlsx", _xlsx([["日期", "车牌", "里程"], ["2026-10-01", tag, "1"]]))},
                         headers=yx))
    assert b["status"] == "pool" and "车辆使用登记" in b["suggestion"]["reason"]


# ============================================================ zip 批量
class _GbkInfo(zipfile.ZipInfo):
    """模拟 Windows 自带压缩：文件名按 GBK 写入、不置 UTF-8 标志位。"""
    def _encodeFilenameFlags(self):
        return self.filename.encode("gbk"), self.flag_bits & ~0x800


def _zip(entries: dict[str, bytes], gbk: bool = False) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(_GbkInfo(name) if gbk else name, content)
    return buf.getvalue()


def test_zip_batch_import(client, login):
    from docx import Document
    emp = login(EMP)
    tag = uuid.uuid4().hex[:6]
    doc = io.BytesIO()
    d = Document()
    d.add_paragraph(f"交互大屏无信号的排查步骤 {tag}：检查信号源、OPS 模块与 HDMI 线。")
    d.save(doc)
    z = _zip({f"资料/大屏排查手册{tag}.docx": doc.getvalue(), f"资料/客户清单{tag}.xlsx": _xlsx([["日期", "车牌"], ["1", tag]]),
              "__MACOSX/._x": b"junk", "资料/.DS_Store": b"x", "资料/旧文件.doc": b"x" * 50, "资料/内层.zip": b"PK"}, gbk=True)
    r = data(client.post("/api/v1/imports", files={"file": (f"我的资料{tag}.zip", z)}, headers=emp))
    names = sorted(b["filename"] for b in r["batches"])
    assert names == sorted([f"大屏排查手册{tag}.docx", f"客户清单{tag}.xlsx", "旧文件.doc"])  # GBK 文件名已还原
    assert all(b["archive_name"] == f"我的资料{tag}.zip" for b in r["batches"])
    assert {s["filename"] for s in r["skipped"]} == {"内层.zip"}
    doc_b = next(b for b in r["batches"] if b["filename"].endswith(".docx"))
    assert doc_b["route"] == "kb" and doc_b["suggestion"]["category"] == "客服"
    old = next(b for b in r["batches"] if b["filename"] == "旧文件.doc")
    assert old["status"] == "pool" and "docx" in old["suggestion"]["reason"]
    # 再传同一个压缩包：里面的文件都已导入过
    assert client.post("/api/v1/imports", files={"file": (f"我的资料{tag}.zip", z)}, headers=emp).json()["code"] == 40001


def test_zip_rejects_bad_archives(client, login):
    emp = login(EMP)
    assert client.post("/api/v1/imports", files={"file": ("坏.zip", b"PK\x03\x04garbage")}, headers=emp).json()["code"] == 40001
    assert client.post("/api/v1/imports", files={"file": ("a.rar", b"Rar!")}, headers=emp).json()["code"] == 40001
    many = _zip({f"f{i}.txt": f"内容{i} {uuid.uuid4().hex}".encode() for i in range(101)})
    r = client.post("/api/v1/imports", files={"file": ("多.zip", many)}, headers=emp).json()
    assert r["code"] == 40001 and "100" in r["message"]


def test_handler_action_notifies_uploader(client, login):
    from app.integration.wecom.mock import read_outbox
    emp, yx = login(EMP), login(YX)
    b = data(client.post("/api/v1/imports", files={"file": (f"图{uuid.uuid4().hex[:6]}.png", b"\x89PNG" + uuid.uuid4().bytes)},
                         headers=emp))
    n = len(read_outbox(100000))
    data(client.post(f"/api/v1/imports/{b['id']}/dismiss", json={"note": "图片不需要入库"}, headers=yx))
    sent = [m for m in read_outbox(100000)[n:] if m["to"] == [EMP]]
    assert sent and "图片不需要入库" in sent[0]["content"]
