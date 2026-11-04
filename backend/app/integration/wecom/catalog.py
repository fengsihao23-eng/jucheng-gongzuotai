"""企业微信服务端接口目录。

数据来自官方开发者文档（developer.work.weixin.qq.com）按目录抓取后固化，
不依赖运行时网络。每条记录对应文档里的一个接口页：方法是文档标注的请求方式，
path 是 qyapi 路径，query_keys 是文档示例里的查询参数（已去掉 access_token）。
"""
import json
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

DATA_FILE = Path(__file__).with_name("catalog.json")

# 文档大类 → 默认凭证；接口中心允许管理员单次改用其他凭证
SECTION_CREDENTIAL = {
    "客户联系": "external",
    "微信客服": "kf",
    "打卡": "checkin",
    "会议": "meeting",
    "会议室": "meeting",
}

# 通讯录写操作必须用通讯录同步助手，自建应用 Secret 没有写权限
CONTACTS_WRITE_PREFIXES = ("/cgi-bin/user/create", "/cgi-bin/user/update", "/cgi-bin/user/delete",
                           "/cgi-bin/user/batchdelete", "/cgi-bin/batch/", "/cgi-bin/tag/create",
                           "/cgi-bin/tag/update", "/cgi-bin/tag/delete", "/cgi-bin/tag/addtagusers",
                           "/cgi-bin/tag/deltagusers")


def credential_for(section: str, path: str, method: str) -> str:
    if path.startswith("/cgi-bin/webhook"):
        return "webhook"
    if path.startswith(CONTACTS_WRITE_PREFIXES):
        return "contacts"
    if path.startswith("/cgi-bin/externalcontact"):
        return "external"
    if path.startswith("/cgi-bin/kf"):
        return "kf"
    if path.startswith("/cgi-bin/checkin"):
        return "checkin"
    if path.startswith("/cgi-bin/meeting"):
        return "meeting"
    if path.startswith(("/cgi-bin/wedoc", "/cgi-bin/wedrive")):
        return "doc"
    return SECTION_CREDENTIAL.get(section, "app")


def _normalize(row: dict) -> dict:
    url = row.get("url") or ""
    parsed = urlsplit(url)
    path = parsed.path if parsed.scheme else ""
    query = []
    if parsed.query:
        for pair in parsed.query.split("&"):
            key = pair.split("=", 1)[0]
            if key and key != "access_token" and key not in query:
                query.append(key)
    doc_path = row["doc_path"]
    if doc_path.startswith("w"):
        doc_url = "https://developer.work.weixin.qq.com/document/path/91770"
    elif doc_path.startswith("a"):
        doc_url = f"https://qiyeweixin.apifox.cn/api-{doc_path[1:]}"
    else:
        doc_url = f"https://developer.work.weixin.qq.com/document/path/{doc_path}"
    item = {
        "id": doc_path,
        "doc_path": doc_path,
        "doc_url": doc_url,
        "name": row["name"],
        "section": row.get("section") or "未分类",
        "group": " / ".join((row.get("trail") or [])[1:]) or row.get("section") or "",
        "method": (row.get("method") or "POST").upper(),
        "path": path,
        "query_keys": query,
        "has_body": (row.get("method") or "POST").upper() != "GET",
        "permission": row.get("permission") or "",
    }
    item["credential"] = credential_for(item["section"], item["path"], item["method"])
    return item


@lru_cache
def items() -> tuple[dict, ...]:
    if not DATA_FILE.exists():
        return ()
    raw = json.loads(DATA_FILE.read_text())
    return tuple(_normalize(row) for row in raw if row.get("url"))


@lru_cache
def by_id() -> dict[str, dict]:
    return {item["id"]: item for item in items()}


def get(doc_path: str) -> dict | None:
    return by_id().get(doc_path)


def tree() -> list[dict]:
    """按文档大类 → 子分组组织，供接口中心左侧导航。"""
    sections: dict[str, dict] = {}
    for item in items():
        section = sections.setdefault(item["section"], {"section": item["section"], "count": 0, "groups": {}})
        group = section["groups"].setdefault(item["group"] or "其它", [])
        group.append({key: item[key] for key in ("id", "name", "method", "path", "credential")})
        section["count"] += 1
    out = []
    for section in sections.values():
        groups = [{"name": name, "items": rows} for name, rows in section["groups"].items()]
        out.append({"section": section["section"], "count": section["count"], "groups": groups})
    out.sort(key=lambda s: (-s["count"], s["section"]))
    return out


def stats() -> dict:
    rows = items()
    return {
        "total": len(rows),
        "sections": len({row["section"] for row in rows}),
        "read": sum(1 for row in rows if row["method"] == "GET"),
        "write": sum(1 for row in rows if row["method"] != "GET"),
        "by_credential": {name: sum(1 for row in rows if row["credential"] == name)
                          for name in {row["credential"] for row in rows}},
    }
