"""企业微信应用内对话：成员在「聚成工作台」应用里发消息，工作台以本人身份处理并回复。

- 文字：交给小聚（同一成员固定一个「企业微信对话」会话，上下文连续），执行结束后把结果回复到企业微信；
  需要确认的写操作不在聊天里执行，回复里附「打开工作台确认」链接。小聚不可用时退回知识库检索。
  以「搜 / 知识库」开头的文字直接检索知识库，「帮助 / ?」回复使用说明。
- 图片（以及客户端下发的文件消息）：下载临时素材，走统一资料导入（channel=wecom），回复去向与下一步。
- 菜单点击（EventKey）：help / kb_hot 等，回复说明或常用入口。
- 身份：只处理已绑定当前企业通讯录的有效成员；msg_id 唯一去重企微回调重试。只存消息摘要，不存全文。

企业微信「接收消息」官方只下发文本、图片、语音、视频、位置、链接；成员把文件发给应用并不保证回调，
所以上传文件的主入口是应用菜单里的「上传资料」网页（企业微信内自动登录）。
"""
import logging
import threading
import time
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.response import BizError
from app.core.timeutil import TZ, utcnow
from app.integration.wecom import get_wecom
from app.integration.wecom.base import MessageDto
from app.models import SysUser, SysWecomInbound

log = logging.getLogger("wecom.inbox")
REPLY_LIMIT = 600  # 企微文本消息上限 2048 字节，留出链接
HELP_WORDS = {"帮助", "help", "?", "？", "菜单", "你好", "您好", "在吗", "hi", "hello"}
SEARCH_PREFIX = ("搜", "查知识库", "知识库", "kb ")
KB_PREFIX_STRIP = ("搜索", "搜", "查知识库", "知识库", "kb")
EXT_BY_TYPE = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp",
               "audio/amr": ".amr", "video/mp4": ".mp4"}

HELP_TEXT = ("我是聚成工作台的小聚，可以直接在这里和我说话：\n"
             "1. 发文字：问制度、查合同回款、找商机、写周报，我以你的账号权限去办，办完回复你。\n"
             "2. 发图片：会存进「资料导入」，由整理人归档。\n"
             "3. Excel、Word、PDF 请点下方菜单「上传资料」，可一次选多个或传 zip，系统自动分到台账或知识库。\n"
             "4. 发「搜 关键词」：直接查知识库，例如「搜 年休假」。")


def _abs(path: str) -> str:
    return settings.public_base_url.rstrip("/") + path


def _bound_user(db: Session, wecom_userid: str) -> SysUser | None:
    u = db.scalar(select(SysUser).where(SysUser.wecom_userid == wecom_userid))
    if not u or u.status != 1 or u.is_deleted:
        return None
    if settings.source_mode("wecom") == "live":
        marker = (u.preferences or {}).get("company_directory")
        if not isinstance(marker, dict) or marker.get("corp_id") != settings.wecom_corp_id:
            return None
    return u


def reply(wecom_userid: str, text: str, *, title: str = "", url: str | None = None, card: bool = False) -> bool:
    """直接回复发消息的成员（不写站内通知，避免聊天刷屏）。card=True 用文本卡片（带按钮），否则纯文本。"""
    try:
        provider = get_wecom()
        if card and url:
            res = provider.send_app_message([wecom_userid], MessageDto("textcard", title or "聚成工作台", text[:500], url))
        else:
            body = text if not url else f'{text}\n<a href="{url}">{title or "打开工作台"}</a>'
            res = provider.send_app_message([wecom_userid], MessageDto("text", "", body))
        return bool(res.ok)
    except Exception:  # noqa: BLE001 凭证未配置等，记录即可
        log.exception("wecom reply failed")
        return False


def _record(db: Session, msg: dict, user: SysUser | None) -> SysWecomInbound | None:
    """写入去重记录；同一 msg_id 已处理过返回 None。"""
    preview = (msg.get("content") or msg.get("file_name") or msg.get("key") or msg.get("url") or "")[:200]
    row = SysWecomInbound(msg_id=msg["msg_id"][:96], wecom_userid=msg["from_user"][:64], user_id=user.id if user else None,
                          msg_type=(msg.get("event") or msg["msg_type"])[:16], preview=preview or None, status="received")
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    return row


def _done(db: Session, row: SysWecomInbound, status: str = "done", error: str | None = None, **fields) -> None:
    row.status, row.error, row.replied_at = status, (error or None) and error[:300], utcnow()
    for k, v in fields.items():
        setattr(row, k, v)
    db.commit()


# ---------------------------------------------------------------- 入口
def handle(msg: dict) -> None:
    """回调后台任务入口。自带数据库会话，异常只记日志（企微不会因此重试）。"""
    db = SessionLocal()
    try:
        _handle(db, msg)
    except Exception:  # noqa: BLE001
        log.exception("wecom inbound failed")
    finally:
        db.close()


def _handle(db: Session, msg: dict) -> None:
    if settings.source_mode("wecom") == "live" and settings.wecom_agent_id and msg.get("agent_id") \
            and str(msg["agent_id"]) != str(settings.wecom_agent_id):
        return
    user = _bound_user(db, msg["from_user"])
    row = _record(db, msg, user)
    if row is None:
        return  # 企微重试的重复消息
    if user is None:
        reply(msg["from_user"], "你的企业微信账号还没有开通聚成工作台，请联系平台管理员。")
        _done(db, row, "ignored", "未绑定公司通讯录")
        return
    kind = msg.get("event") or msg["msg_type"]
    if kind in ("click", "view"):
        _on_click(db, row, user, msg)
    elif kind == "text":
        _on_text(db, row, user, (msg.get("content") or "").strip())
    elif kind in ("image", "file", "video", "voice") and msg.get("media_id"):
        if kind in ("video", "voice"):
            reply(user.wecom_userid, "暂时听不了语音、看不了视频。请直接打字给我，文件请点下方菜单「上传资料」。")
            _done(db, row, "ignored")
        else:
            _on_media(db, row, user, msg)
    elif kind == "link":
        reply(user.wecom_userid, "收到链接。要我处理这篇内容的话，请把要做的事用文字告诉我；文件请点菜单「上传资料」。")
        _done(db, row, "ignored")
    else:
        reply(user.wecom_userid, "这类消息我还处理不了，请发文字或点下方菜单。")
        _done(db, row, "ignored")


# ---------------------------------------------------------------- 菜单
def _on_click(db: Session, row, user: SysUser, msg: dict) -> None:
    key = msg.get("key") or ""
    if key == "help":
        reply(user.wecom_userid, HELP_TEXT)
    elif key == "my_imports":
        reply(user.wecom_userid, _imports_digest(db, user), title="查看我的导入", url=_abs("/imports"))
    elif key == "kb_review":
        reply(user.wecom_userid, _review_digest(db, user), title="去审核", url=_abs("/kb?tab=review"))
    else:
        _done(db, row, "ignored")
        return
    _done(db, row)


def _imports_digest(db: Session, user: SysUser) -> str:
    from app.models import ImportBatch
    from app.services.import_service import STATUS_NAMES
    rows = list(db.scalars(select(ImportBatch).where(ImportBatch.uploader_id == user.id)
                           .order_by(ImportBatch.created_at.desc()).limit(5)))
    if not rows:
        return "你还没有导入过资料。点菜单「上传资料」就能开始。"
    lines = [f"{i}. {b.filename} · {STATUS_NAMES.get(b.status, b.status)}" for i, b in enumerate(rows, 1)]
    return "你最近的导入：\n" + "\n".join(lines)


def _review_digest(db: Session, user: SysUser) -> str:
    from app.core.deps import build_principal
    from app.services import kb_service
    p = build_principal(user)
    docs = kb_service.list_documents(db, viewer=p, scope="review")
    if not docs:
        return "目前没有需要你审核的知识库文档。"
    lines = [f"{i}. {d['title']}（{d['category']}，{d['uploader'] or '—'}）" for i, d in enumerate(docs[:5], 1)]
    return f"有 {len(docs)} 篇文档等你审核：\n" + "\n".join(lines)


# ---------------------------------------------------------------- 文字
def _kb_answer(db: Session, user: SysUser, query: str) -> tuple[str, str]:
    from app.core.deps import build_principal
    from app.services import kb_service
    hits = kb_service.search(db, query, limit=3, viewer=build_principal(user))
    if not hits:
        return f"知识库里没有找到「{query}」相关的内容。可以换个说法，或把资料上传进来。", _abs("/kb")
    lines = []
    for i, h in enumerate(hits, 1):
        snippet = h["snippet"].replace("<mark>", "").replace("</mark>", "")[:80]
        lines.append(f"{i}. 《{h['title']}》{('§' + h['heading']) if h.get('heading') else ''}\n{snippet}")
    from urllib.parse import quote
    return "在知识库找到：\n" + "\n".join(lines), _abs(f"/kb?q={quote(query)}")


def _on_text(db: Session, row, user: SysUser, text: str) -> None:
    if not text:
        _done(db, row, "ignored")
        return
    if text.lower() in HELP_WORDS:
        reply(user.wecom_userid, HELP_TEXT)
        _done(db, row)
        return
    if text.lower().startswith(SEARCH_PREFIX):
        q = text
        for prefix in KB_PREFIX_STRIP:
            if q.lower().startswith(prefix):
                q = q[len(prefix):].strip(" ：:")
                break
        answer, url = _kb_answer(db, user, q or text)
        reply(user.wecom_userid, answer, title="在知识库查看", url=url)
        _done(db, row)
        return
    _ask_xiaoju(db, row, user, text)


def _session_for(db: Session, user: SysUser) -> str:
    """该成员固定的企业微信对话；失效（权限/连接变化）或不存在时新建。"""
    from app.services import xiaoju_sessions as sessions
    prefs = dict(user.preferences or {})
    sid = (prefs.get("wecom_chat") or {}).get("session_id")
    if sid:
        try:
            sessions.load(user.id, sid)
            return sid
        except BizError:
            pass
    sid = sessions.create(user.id, title="企业微信对话")["id"]
    prefs["wecom_chat"] = {"session_id": sid, "created_at": datetime.now(TZ).isoformat()}
    user.preferences = prefs
    db.commit()
    return sid


def _ask_xiaoju(db: Session, row, user: SysUser, text: str) -> None:
    from app.services import xiaoju_service
    if not xiaoju_service.runtime_info()["available"]:
        answer, url = _kb_answer(db, user, text)
        reply(user.wecom_userid, "小聚暂时不可用，先帮你查了知识库。\n" + answer, title="在知识库查看", url=url)
        _done(db, row)
        return
    try:
        sid = _session_for(db, user)
        task = xiaoju_service.create(text, [], user.id, session_id=sid,
                                     current_page="企业微信应用对话（回复会以企业微信纯文本发给本人，不要输出页面操作指引）")
    except BizError as exc:
        busy = "还在执行" in exc.message
        reply(user.wecom_userid, "上一件事还在办，办完我会回复你，稍等一下再发新的要求。" if busy else f"没能开始处理：{exc.message}")
        _done(db, row, "failed", exc.message)
        return
    row.task_id = task["id"]
    db.commit()
    reply(user.wecom_userid, "收到，正在处理…")
    start_waiter(row.id, user.id, user.wecom_userid, task["id"], sid)


def start_waiter(row_id: int, user_id: int, wecom_userid: str, task_id: str, session_id: str) -> None:
    """后台等小聚任务结束再回复。测试里替换为同步执行。"""
    threading.Thread(target=wait_and_reply, args=(row_id, user_id, wecom_userid, task_id, session_id),
                     name=f"wecom-reply-{task_id[:8]}", daemon=True).start()


def wait_and_reply(row_id: int, user_id: int, wecom_userid: str, task_id: str, session_id: str,
                   poll: float = 2.0) -> None:
    from app.services import xiaoju_service
    deadline = time.time() + settings.assistant_task_timeout_seconds + 90
    task = None
    while time.time() < deadline:
        try:
            task = xiaoju_service.read(task_id, user_id)
        except Exception:  # noqa: BLE001
            log.exception("read task failed")
            break
        if task["state"] in xiaoju_service.TERMINAL:
            break
        time.sleep(poll)
    url = _abs(f"/?xiaoju={session_id}")
    if task is None or task["state"] not in xiaoju_service.TERMINAL:
        ok_ = reply(wecom_userid, "这件事办得比较久，还没完成。稍后在工作台里查看进度。", title="打开小聚", url=url)
        status, err = "failed", "等待超时"
    else:
        msg = (task.get("message") or "已处理").strip()
        if len(msg) > REPLY_LIMIT:
            msg = msg[:REPLY_LIMIT] + "…（完整内容见工作台）"
        if task["state"] == "awaiting_confirmation":
            ok_ = reply(wecom_userid, msg + "\n\n这一步需要你确认后才会提交。", title="打开工作台确认", url=url)
        elif task["state"] in ("failed", "cancelled"):
            ok_ = reply(wecom_userid, msg, title="在工作台继续", url=url)
        else:
            ok_ = reply(wecom_userid, msg, title="在工作台查看", url=url if task.get("links") or task.get("business_results") else None)
        status, err = ("done" if ok_ else "failed"), (None if ok_ else "回复发送失败")
    db = SessionLocal()
    try:
        row = db.get(SysWecomInbound, row_id)
        if row is not None:
            _done(db, row, status, err)
    finally:
        db.close()


# ---------------------------------------------------------------- 图片 / 文件
def _on_media(db: Session, row, user: SysUser, msg: dict) -> None:
    from app.core.deps import build_principal
    from app.services import import_service
    try:
        media = get_wecom().get_media(msg["media_id"])
    except Exception as exc:  # noqa: BLE001
        reply(user.wecom_userid, "文件下载失败，可能已过期（企业微信临时文件保留 3 天）。请点菜单「上传资料」重新上传。")
        _done(db, row, "failed", f"get_media: {type(exc).__name__}")
        return
    name = msg.get("file_name") or media.filename or ""
    if "." not in name:
        stamp = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
        name = f"企业微信图片-{stamp}{EXT_BY_TYPE.get(media.content_type or '', '.jpg')}"
    p = build_principal(user)
    try:
        r = import_service.create_many(db, p, media.data, name, None, channel="wecom")
        db.commit()
    except BizError as exc:
        db.rollback()
        reply(user.wecom_userid, f"没能导入《{name}》：{exc.message}")
        _done(db, row, "failed", exc.message)
        return
    b = r["batches"][0]
    if b.status == "pool":
        text = f"《{b.filename}》已收到，交给整理人归档。"
    elif b.route == "kb":
        s = b.suggestion or {}
        cat = f"「{s['category']}{' / ' + s['subcategory'] if s.get('subcategory') else ''}」" if s.get("category") else ""
        text = f"《{b.filename}》已识别为知识库资料{('，建议放入' + cat) if cat else ''}。点开确认分类就能入库。"
    else:
        text = f"《{b.filename}》识别为{b.suggestion.get('target_name') or '业务台账'}，请核对字段后导入。"
    reply(user.wecom_userid, text, title="资料已收到", url=_abs("/imports"), card=True)
    _done(db, row, batch_id=b.id)


# ---------------------------------------------------------------- 应用菜单
def default_menu() -> dict:
    """企业微信自建应用底部菜单（最多 3 个一级、每个最多 5 个二级）。网页在企业微信内打开会自动登录。"""
    return {"button": [
        {"type": "view", "name": "上传资料", "url": _abs("/imports")},
        {"type": "view", "name": "知识库", "url": _abs("/kb")},
        {"name": "更多", "sub_button": [
            {"type": "view", "name": "工作台", "url": _abs("/")},
            {"type": "click", "name": "我的导入进度", "key": "my_imports"},
            {"type": "click", "name": "待我审核", "key": "kb_review"},
            {"type": "click", "name": "使用说明", "key": "help"},
        ]},
    ]}


def push_menu() -> dict:
    menu = default_menu()
    get_wecom().create_menu(menu)
    return menu
