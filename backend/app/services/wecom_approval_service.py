"""企业微信审批提交：按模板控件生成 apply_data，工作台直接发起企微审批。

模板 ID 存在 sys_dict(wecom_approval/templates)，管理员可在系统管理里改，不用改代码。
控件值按「控件标题」匹配，前端只提交业务字段；找不到的必填控件会明确报错，不静默丢字段。
"""
import time
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.response import BizError
from app.core.timeutil import TZ
from app.integration.wecom.credentials import Gateway, WeComApiError
from app.services import dict_service

DICT_TYPE = "wecom_approval"
DICT_KEY = "templates"
DEFAULT_KINDS = {"expense": "费用报销", "contract": "合同审批"}

# 业务字段别名：前端传简写也能落到模板标题
ALIASES = {
    "expense": {"amount": "报销金额", "reason": "报销事由", "category": "费用类型", "date": "费用日期",
                "project": "项目名称", "applicant": "申请人"},
    "contract": {"customer": "客户名称", "name": "合同名称", "amount": "合同金额", "project": "项目名称",
                 "date": "签订日期", "owner": "负责人"},
}

_CACHE: dict[str, tuple[float, dict]] = {}
CACHE_TTL = 300


def config(db: Session) -> dict:
    saved = dict_service.get(db, DICT_TYPE, DICT_KEY, {}) or {}
    out = {}
    for key, label in DEFAULT_KINDS.items():
        row = saved.get(key) if isinstance(saved, dict) else None
        out[key] = {"kind": key, "name": (row or {}).get("name") or label,
                    "template_id": (row or {}).get("template_id") or "",
                    "configured": bool((row or {}).get("template_id"))}
    return out


def put_config(db: Session, patch: dict) -> dict:
    saved = dict_service.get(db, DICT_TYPE, DICT_KEY, {}) or {}
    for key, row in (patch or {}).items():
        if key not in DEFAULT_KINDS or not isinstance(row, dict):
            continue
        saved[key] = {"name": row.get("name") or DEFAULT_KINDS[key],
                      "template_id": (row.get("template_id") or "").strip()}
    dict_service.put(db, DICT_TYPE, DICT_KEY, saved, "企业微信审批模板")
    db.commit()
    _CACHE.clear()
    return config(db)


def _template_id(db: Session, kind: str) -> str:
    row = config(db).get(kind)
    if not row or not row["template_id"]:
        raise BizError(40010, f"尚未配置「{row['name'] if row else kind}」的企业微信审批模板 ID")
    return row["template_id"]


def controls(db: Session, kind: str, *, refresh: bool = False) -> dict:
    template_id = _template_id(db, kind)
    cached = _CACHE.get(template_id)
    if cached and not refresh and time.time() < cached[0]:
        return cached[1]
    try:
        data = Gateway.call("app", "POST", "/cgi-bin/oa/gettemplateinfo", json={"template_id": [template_id]})
    except WeComApiError as error:
        raise BizError(50201, f"读取审批模板失败（{error.errcode}）：{error.errmsg}") from None
    rows = data.get("template_list") or []
    if not rows:
        raise BizError(40401, "企业微信未返回该审批模板，请核对模板 ID 与应用可见范围")
    row = rows[0]
    content = row.get("template_content") or {}
    out = {"template_id": template_id, "template_name": row.get("template_name") or "",
           "controls": [_control_view(c) for c in content.get("controls") or []]}
    _CACHE[template_id] = (time.time() + CACHE_TTL, out)
    return out


def _control_view(control: dict) -> dict:
    prop = control.get("property") or {}
    kind = control.get("control") or ""
    view = {"id": control.get("id") or "", "control": kind, "title": prop.get("title") or "",
            "placeholder": prop.get("placeholder") or "", "required": bool(prop.get("require"))}
    if kind == "Selector":
        options = ((prop.get("selector") or {}).get("options")) or []
        view["options"] = [{"key": o.get("key"), "text": _option_text(o)} for o in options]
        view["multi"] = (prop.get("selector") or {}).get("type") == "multi"
    return view


def _option_text(option: dict) -> str:
    values = option.get("value") or []
    return (values[0] or {}).get("text") if values else option.get("key", "")


def _coerce(control: dict, value) -> dict:
    """把前端值转成企微 apply_data 的 value 结构。"""
    kind = control["control"]
    if kind in ("Text", "Textarea"):
        return {"text": str(value)[:2000]}
    if kind == "Number":
        return {"new_number": str(value)}
    if kind == "Money":
        return {"new_money": f"{Decimal(str(value)):.2f}"}
    if kind == "Date":
        if isinstance(value, (int, float)):
            stamp = int(value / 1000) if value > 1e11 else int(value)
        elif isinstance(value, (date, datetime)):
            stamp = int(datetime(value.year, value.month, value.day, tzinfo=TZ).timestamp())
        else:
            try:
                stamp = int(datetime.fromisoformat(str(value)[:10]).replace(tzinfo=TZ).timestamp())
            except ValueError:
                raise BizError(40001, f"「{control['title']}」日期格式应为 YYYY-MM-DD") from None
        return {"date": {"type": "day", "s_timestamp": str(stamp)}}
    if kind == "Selector":
        options = control.get("options") or []
        texts = value if isinstance(value, list) else [value]
        picked = []
        for text in texts:
            hit = next((o for o in options if o["text"] == text or o["key"] == text), None)
            if hit is None:
                raise BizError(40001, f"「{control['title']}」没有选项 {text}")
            picked.append({"key": hit["key"], "value": [{"text": hit["text"]}]})
        return {"selector": {"type": "multi" if control.get("multi") else "single", "options": picked}}
    if kind == "Contact":
        users = value if isinstance(value, list) else [u.strip() for u in str(value).split(",") if u.strip()]
        return {"members": [{"userid": u} for u in users]}
    raise BizError(40001, f"暂不支持「{control['title']}」的控件类型 {kind}，请在企微客户端提交")


def build_contents(detail: dict, values: dict, kind: str) -> list[dict]:
    aliases = ALIASES.get(kind, {})
    normalized = {}
    for key, value in (values or {}).items():
        normalized[aliases.get(key, key)] = value
    contents, missing = [], []
    for control in detail["controls"]:
        title = control["title"]
        value = normalized.get(title)
        if value in (None, "", []):
            if control["required"] and control["control"] not in ("Attachment", "Table", "File"):
                missing.append(title)
            continue
        contents.append({"id": control["id"], "control": control["control"], "value": _coerce(control, value)})
    if missing:
        raise BizError(40001, f"必填项未填写：{'、'.join(missing)}")
    if not contents:
        raise BizError(40001, "没有可提交的字段，请检查模板控件与填写内容")
    return contents


def submit(db: Session, user, kind: str, values: dict) -> dict:
    if kind not in DEFAULT_KINDS:
        raise BizError(40004, "未知的审批类型")
    detail = controls(db, kind)
    contents = build_contents(detail, values, kind)
    summary = " / ".join(f"{c['title']}：{_summary_text(c, contents)}" for c in detail["controls"][:3]
                         if _summary_text(c, contents))
    body = {
        "creator_userid": user.wecom_userid or "",
        "template_id": detail["template_id"],
        "use_template_approver": 1,
        "notify_type": 1,
        "apply_data": {"contents": contents},
        "summary_list": [{"summary_info": [{"text": (summary or detail["template_name"])[:100], "lang": "zh_CN"}]}],
    }
    if not body["creator_userid"]:
        raise BizError(40001, "当前账号未绑定企业微信 UserID，无法发起审批")
    try:
        data = Gateway.call("app", "POST", "/cgi-bin/oa/applyevent", json=body)
    except WeComApiError as error:
        raise BizError(50201, f"提交审批失败（{error.errcode}）：{error.errmsg}",
                       {"errcode": error.errcode}) from None
    sp_no = data.get("sp_no") or ""
    from app.core.audit import log_op
    log_op(db, user.id, "wecom.approval.submit", "approval", sp_no or kind,
           None, {"kind": kind, "template_id": detail["template_id"], "sp_no": sp_no, "fields": list(values or {})})
    db.commit()
    return {"sp_no": sp_no, "template_id": detail["template_id"], "template_name": detail["template_name"],
            "summary": summary, "note": "审批已在企业微信发起，结果会由审批回调/对账同步回工作台"}


def _summary_text(control: dict, contents: list[dict]) -> str:
    for row in contents:
        if row["id"] != control["id"]:
            continue
        value = row["value"]
        if "text" in value:
            return str(value["text"])[:40]
        if "new_money" in value:
            return f"¥{value['new_money']}"
        if "new_number" in value:
            return str(value["new_number"])
        if "selector" in value:
            return "、".join(o["value"][0]["text"] for o in value["selector"]["options"])
        if "members" in value:
            return "、".join(m["userid"] for m in value["members"])
    return ""
