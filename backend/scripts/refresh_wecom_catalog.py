"""重建企业微信接口目录（app/integration/wecom/catalog.json）。

数据源：Apifox 公开镜像 qiyeweixin.apifox.cn（企业微信官方文档的完整搬运），
逐个接口页读取请求方法与路径。官方文档站有调用频率限制，镜像更适合批量抓取；
抓完再与官方文档页关联（可选 --official-cache）。

用法：
    cd backend && .venv/bin/python -m scripts.refresh_wecom_catalog
    .venv/bin/python -m scripts.refresh_wecom_catalog --method-cache /tmp/apifox_methods.json
"""
import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

import httpx

MIRROR = "https://qiyeweixin.apifox.cn"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
OUT = Path(__file__).resolve().parents[1] / "app" / "integration" / "wecom" / "catalog.json"

ENTRY = re.compile(r'\\"([^\\"]*)\\",\\"apiDetail\.(\d+)\\",\\"([^\\"]*)\\",\[([^\]]*)\]')
# 部分条目没有前置名称：10061348,\"apiDetail.10061348\",\"/cgi-bin/message/send\",[...]
ENTRY_BARE = re.compile(r'(\d+),\\"apiDetail\.(\d+)\\",\\"([^\\"]*)\\",\[([^\]]*)\]')
# 侧边栏每一行：<a title="创建成员" href="/api-10061199"> … <span class="truncate">POST</span>
ANCHOR = re.compile(r'<a[^>]*title="([^"]+)"[^>]*href="/api-(\d+)"[^>]*>(.*?)</a>', re.S)
LABEL = re.compile(r'<span class="truncate">(GET|POST)</span>')

# 路径前缀 → 文档大类（顺序敏感，具体规则在前）
SECTIONS = [
    ("/cgi-bin/gettoken", "基础"),
    ("/cgi-bin/getcallbackip", "基础"),
    ("/cgi-bin/get_api_domain_ip", "基础"),
    ("/cgi-bin/auth", "身份验证"),
    ("/cgi-bin/sso", "身份验证"),
    ("/cgi-bin/user/getuserinfo", "身份验证"),
    ("/cgi-bin/user/getuserdetail", "身份验证"),
    ("/cgi-bin/user/authsucc", "身份验证"),
    ("/cgi-bin/user", "通讯录管理"),
    ("/cgi-bin/department", "通讯录管理"),
    ("/cgi-bin/tag", "通讯录管理"),
    ("/cgi-bin/batch", "通讯录管理"),
    ("/cgi-bin/export", "通讯录管理"),
    ("/cgi-bin/corp", "通讯录管理"),
    ("/cgi-bin/linkedcorp", "企业互联"),
    ("/cgi-bin/miniprogram", "企业互联"),
    ("/cgi-bin/externalcontact", "客户联系"),
    ("/cgi-bin/kf", "微信客服"),
    ("/cgi-bin/checkin", "打卡"),
    ("/cgi-bin/hardware", "打卡"),
    ("/cgi-bin/health", "家校应用"),
    ("/cgi-bin/pstncc", "公费电话"),
    ("/cgi-bin/dial", "公费电话"),
    ("/cgi-bin/externalpay", "企业支付"),
    ("/cgi-bin/oa/applyevent", "审批"),
    ("/cgi-bin/oa/getapproval", "审批"),
    ("/cgi-bin/oa/approval", "审批"),
    ("/cgi-bin/oa/gettemplateinfo", "审批"),
    ("/cgi-bin/oa/vacation", "审批"),
    ("/cgi-bin/oa/getuserbalance", "审批"),
    ("/cgi-bin/oa/modifyuserbalance", "审批"),
    ("/cgi-bin/oa/journal", "汇报"),
    ("/cgi-bin/oa/meetingroom", "会议室"),
    ("/cgi-bin/oa/calendar", "日程"),
    ("/cgi-bin/oa/schedule", "日程"),
    ("/cgi-bin/oa", "办公"),
    ("/cgi-bin/meeting", "会议"),
    ("/cgi-bin/wedoc", "文档"),
    ("/cgi-bin/wedrive", "微盘"),
    ("/cgi-bin/media", "素材管理"),
    ("/cgi-bin/message", "消息推送"),
    ("/cgi-bin/appchat", "消息推送"),
    ("/cgi-bin/webhook", "消息推送"),
    ("/cgi-bin/agent", "应用管理"),
    ("/cgi-bin/menu", "应用管理"),
    ("/cgi-bin/living", "直播"),
    ("/cgi-bin/card/invoice", "电子发票"),
    ("/cgi-bin/msgaudit", "会话内容存档"),
    ("/cgi-bin/exmail", "邮件"),
    ("/cgi-bin/pay", "企业支付"),
    ("/cgi-bin/school", "家校沟通"),
    ("/cgi-bin/edu", "家校沟通"),
    ("/cgi-bin/report", "政民沟通"),
    ("/cgi-bin/grid", "政民沟通"),
    ("/cgi-bin/service", "服务商"),
    ("/cgi-bin/ticket", "基础"),
]


def section_of(path: str) -> str:
    for prefix, name in SECTIONS:
        if path.startswith(prefix):
            return name
    return "其它"


def group_of(path: str) -> str:
    parts = path.split("?")[0].strip("/").split("/")
    return "/".join(parts[1:3]) if len(parts) > 2 else "/".join(parts[1:]) or "root"


# 镜像里少数条目没有标题，补上官方名称
NAME_FALLBACK = {
    "/cgi-bin/message/send": "发送应用消息",
}

# 镜像未收录、但工作台要用的接口（群机器人走独立 key，不是 corp token）
SUPPLEMENTS = [
    {"name": "群机器人发送消息", "method": "POST", "path": "/cgi-bin/webhook/send",
     "section": "消息推送", "trail": ["消息推送", "webhook"], "doc_path": "w1001", "permission": "群机器人 Webhook key"},
]


def load_official(cache: Path | None) -> dict[str, dict]:
    if not cache or not cache.exists():
        return {}
    rows = json.loads(cache.read_text())
    if isinstance(rows, dict):
        rows = list(rows.values())
    out = {}
    for row in rows:
        url = (row.get("url") or "").split("?")[0]
        if url.startswith("https://qyapi.weixin.qq.com"):
            out[url.replace("https://qyapi.weixin.qq.com", "")] = row
    return out


async def fetch_methods(apis: list[dict], cache: Path | None) -> dict[str, dict]:
    if cache and cache.exists():
        return json.loads(cache.read_text())
    result: dict[str, dict] = {}
    sem = asyncio.Semaphore(4)

    async def one(client, api):
        async with sem:
            for attempt in range(3):
                try:
                    text = (await client.get(f"{MIRROR}/api-{api['id']}")).text
                    # 页面首个 method 字段是默认文档，真正准确的是侧边栏每行的标签
                    for title, api_id, inner in ANCHOR.findall(text):
                        label = LABEL.search(inner)
                        if label and api_id not in result:
                            result[api_id] = {"method": label.group(1), "name": title.strip(), "id": api_id}
                    if api["id"] in result:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(1.0 * (attempt + 1))
            result[api["id"]] = {"method": "POST", "name": api["name"], "id": api["id"], "assumed": True}

    headers = {"User-Agent": UA}
    async with httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=True) as client:
        for index in range(0, len(apis), 40):
            batch = [api for api in apis[index:index + 40] if api["id"] not in result]
            if batch:
                await asyncio.gather(*(one(client, api) for api in batch))
            print(f"  方法 {min(index + 40, len(apis))}/{len(apis)}", file=sys.stderr)
    if cache:
        cache.write_text(json.dumps(result, ensure_ascii=False))
    return result


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--method-cache", type=Path, help="复用已抓取的方法结果")
    parser.add_argument("--official-cache", type=Path, help="官方文档抓取结果（可选，用于补充权限说明）")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    print(f"拉取镜像目录 {MIRROR} …", file=sys.stderr)
    async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=40, follow_redirects=True) as client:
        html = (await client.get(MIRROR + "/")).text

    apis, seen = [], set()
    for name, api_id, path, _tags in ENTRY.findall(html):
        path = path.strip()
        if not path.startswith("/cgi-bin") or path in seen:
            continue
        seen.add(path)
        apis.append({"id": api_id, "name": name.strip(), "path": path})
    for _index, api_id, path, _tags in ENTRY_BARE.findall(html):
        path = path.strip()
        if not path.startswith("/cgi-bin") or path in seen:
            continue
        seen.add(path)
        apis.append({"id": api_id, "name": "", "path": path})
    print(f"镜像接口 {len(apis)} 条", file=sys.stderr)

    methods = await fetch_methods(apis, args.method_cache)
    official = load_official(args.official_cache)

    rows, seen_key = [], set()
    for api in apis:
        info = methods.get(api["id"], {"method": "POST", "name": api["name"]})
        path = api["path"].split("?")[0]
        method = info.get("method") or "POST"
        if (method, path) in seen_key:
            continue
        seen_key.add((method, path))
        hit = official.get(path, {})
        rows.append({
            "name": (api["name"] or hit.get("name") or NAME_FALLBACK.get(path) or info.get("name") or path)[:120],
            "method": method,
            "url": "https://qyapi.weixin.qq.com" + path,
            "section": section_of(path),
            "trail": [section_of(path), group_of(path)],
            "doc_path": hit.get("doc_path") or "a" + api["id"],
            "permission": hit.get("permission") or "",
        })
    rows.sort(key=lambda row: (row["section"], row["trail"][1], row["name"]))
    for extra in SUPPLEMENTS:
        if not any(row["url"].endswith(extra["path"]) for row in rows):
            rows.append({"name": extra["name"], "method": extra["method"],
                         "url": "https://qyapi.weixin.qq.com" + extra["path"], "section": extra["section"],
                         "trail": extra["trail"], "doc_path": extra["doc_path"], "permission": extra["permission"]})
    rows.sort(key=lambda row: (row["section"], row["trail"][1], row["name"]))
    args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    assumed = sum(1 for value in methods.values() if value.get("assumed"))
    print(f"写入 {args.out}：{len(rows)} 个接口，{len({r['section'] for r in rows})} 个大类"
          f"，方法待确认 {assumed} 个", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
