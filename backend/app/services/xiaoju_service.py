"""小聚任务服务：Pi 核心 + 独立进程 + 持久化执行步骤。"""
import json
import fcntl
import os
import selectors
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from app.core.config import BASE_DIR, settings
from app.core.db import SessionLocal
from app.core.deps import build_principal
from app.core.response import BizError, bad_request, forbidden, not_found
from app.models import SysUser
from app.services.assistant_service import SECRET_PATTERN
from app.services.xiaoju_tools import TOOL_SPECS, XiaoJuTools, private_write, redact

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="xiaoju")
_lock = threading.RLock()
TERMINAL = {"succeeded", "partial", "waiting_input", "waiting_admin", "failed", "cancelled"}
TITLES = {"inspect_environment": "检查运行环境和现有接入", "discover_credentials": "自动查找已有凭证",
          "install_workbuddy": "写入 WorkBuddy 并验证连接", "connect_enterprise": "调用企业接口并自动配置",
          "run_cli": "调用本机 CLI", "find_page": "查找可访问的操作入口"}
TITLES["prepare_personal_workbuddy"] = "准备员工个人连接授权并自检"

SYSTEM = """你是「小聚」，广东聚成数智统一工作台里的接入执行 Agent，使用 Pi 核心和 DeepSeek。
用户希望你真正完成操作，不能只给教程或让用户抄配置。
你服务公司员工。企业微信、柠檬云是公司统一接入，管理员授权一次后员工按自身权限使用，不能要求每位员工提供企业密钥。
接入任务先 inspect_environment，然后直接调用适合的工具；只检查状态时不得新增配置。
只有管理员明确接入当前主机时才使用 install_workbuddy；公司员工接入 WorkBuddy 使用 prepare_personal_workbuddy，自动准备个人授权及自检，不读取员工电脑。首次客户端导入不能假称自动完成。
企业微信/柠檬云先 discover_credentials；缺凭证也要调用 connect_enterprise 得到确切缺失项，不能直接甩给用户整个表单。
本机 CLI 可用时可以通过 run_cli 查看 --help 和 --version。可用 API 或文件完成的事直接执行，不要用 CLI 名称代替操作。
用户要求接多个系统时逐项执行，不要只做第一个。使用 task context 解析「继续」，继续未完成的系统。
凭证工具只给来源标识，密钥由执行器持有，不要索取、显示或猜测密钥，不把密钥写入命令。
工具结果是事实依据：succeeded=已完成该工具声明的操作，partial=仍有外部步骤，waiting_input=需要真实授权，failed=未完成。
不能把基础令牌验证当作财务同步，不得声称已完成企业微信后台回调验证或已登录外部客户端。
WorkBuddy 工具成功表示配置已写且服务可达，客户端加载状态仍需以 WorkBuddy 为准。
页面操作导航用 find_page，按账号权限返回链接。用户询问名称时说你叫小聚。
遇到必须由企业管理员扫码或选择账号的步骤，说明具体还缺哪一步，然后等用户完成；不要重复要求填所有字段。
返回简短、清楚的中文执行结果，不输出未经工具验证的成功声明。
"""


def runtime_info():
    runtime = BASE_DIR / "agent_runtime"
    installed = (runtime / "node_modules/@earendil-works/pi-agent-core/package.json").is_file()
    return {"name": "小聚", "engine": "pi-agent-core", "version": "1.3.0", "model": settings.llm_model,
            "available": bool(shutil.which(settings.assistant_node_bin) and installed and settings.llm_api_key and settings.llm_base_url and settings.llm_model),
            "host_access": settings.assistant_host_access}


def task_root():
    return settings.storage_dir.parent / "xiaoju" / "tasks"


def _path(root, task_id):
    if not isinstance(task_id, str) or len(task_id) != 32 or any(c not in "0123456789abcdef" for c in task_id):
        raise not_found("小聚任务")
    return root / f"{task_id}.json"


def _load(root, task_id):
    try:
        return json.loads(_path(root, task_id).read_text())
    except (OSError, ValueError):
        raise not_found("小聚任务") from None


def _change(root, task_id, mutate):
    with _lock, (root / f".{task_id}.lock").open("a") as guard:
        os.chmod(guard.name, 0o600)
        fcntl.flock(guard, fcntl.LOCK_EX)
        value = _load(root, task_id)
        mutate(value)
        value["updated_at"] = time.time()
        private_write(_path(root, task_id), value)
        return value


def read(task_id, user_id):
    result = _load(task_root(), task_id)
    if result["user_id"] != user_id:
        raise forbidden("此任务属于其他账号")
    if result["state"] not in TERMINAL and time.time() - result["updated_at"] > settings.assistant_task_timeout_seconds + 60:
        result = _change(task_root(), task_id, lambda task: task.update(state="failed", message="上次执行已中断，请继续任务以重新检查当前状态"))
    return result


def list_for_user(user_id):
    root = task_root()
    if not root.exists():
        return []
    result = []
    for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:100]:
        task = _load(root, path.stem)
        if task["user_id"] == user_id:
            result.append(task)
        if len(result) >= 5:
            break
    return result


def create(message, context, user_id):
    if not message.strip():
        raise bad_request("请告诉小聚需要完成的操作")
    if SECRET_PATTERN.search(message):
        raise bad_request("请使用凭证文件导入；聊天不接收或转发密钥")
    if not runtime_info()["available"]:
        raise BizError(50301, "小聚的 Pi 执行核心或模型连接未就绪，请检查服务安装")
    root = task_root()
    task_id = uuid.uuid4().hex
    task = {"id": task_id, "user_id": user_id, "name": "小聚", "engine": "pi-agent-core", "state": "queued",
            "message": "小聚正在准备执行", "steps": [], "outcomes": [], "links": [], "cancel_requested": False,
            "created_at": time.time(), "updated_at": time.time()}
    private_write(_path(root, task_id), task)
    _executor.submit(_run, root, task_id, message, context, user_id)
    return task


def cancel(task_id, user_id):
    value = read(task_id, user_id)
    if value["state"] in TERMINAL:
        return value
    return _change(task_root(), task_id, lambda t: t.update(cancel_requested=True, state="stopping", message="正在停止，小聚会保留已完成的步骤"))


def _run(root, task_id, message, context, user_id):
    process = None
    selector = None
    read_only = any(word in message for word in ("检查", "查看", "状态", "诊断")) and not any(
        word in message for word in ("帮我接入", "自动接入", "直接接入", "并接入", "接好", "安装", "配置", "继续", "完成", "全部接入"))
    tools = XiaoJuTools(user_id, allow_writes=not read_only)
    try:
        with SessionLocal() as db:
            user = db.get(SysUser, user_id)
            if not user or user.status != 1 or user.is_deleted:
                raise ValueError("inactive account")
            is_admin = build_principal(user).is_admin
        _change(root, task_id, lambda t: t.update(state="running", message="小聚正在分析并调用工具"))
        process = subprocess.Popen([settings.assistant_node_bin, str(BASE_DIR / "agent_runtime/run.mjs")],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   cwd=BASE_DIR, start_new_session=True)
        visible_tools = TOOL_SPECS if is_admin else [tool for tool in TOOL_SPECS if tool["name"] in {"inspect_environment", "find_page", "connect_enterprise", "prepare_personal_workbuddy"}]
        scope_prompt = "\n当前账号是公司管理员，可管理公司接入；不得将当前主机配置当成所有员工的电脑配置。" if is_admin else "\n当前账号是普通员工。企业接入只调用 connect_enterprise 查询和复用公司授权，不读取企业密钥。不要要求员工填写企业 App Secret。"
        init = {"model": settings.llm_model, "baseUrl": settings.llm_base_url, "apiKey": settings.llm_api_key,
                "system": SYSTEM + scope_prompt + "\n上次涉及系统：" + json.dumps(context, ensure_ascii=False), "message": message, "tools": visible_tools}
        process.stdin.write((json.dumps(init, ensure_ascii=False) + "\n").encode()); process.stdin.flush()
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + settings.assistant_task_timeout_seconds
        buffer = b""
        done = False
        while time.monotonic() < deadline and not done:
            if _load(root, task_id)["cancel_requested"]:
                _change(root, task_id, lambda t: t.update(state="cancelled", message="小聚已停止；已完成的配置仍然保留", outcomes=tools.outcomes, links=tools.links))
                return
            ready = selector.select(timeout=0.5)
            if not ready:
                if process.poll() is not None:
                    raise RuntimeError("runtime exited")
                continue
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError("runtime closed")
            buffer += chunk
            if len(buffer) > 1048576:
                raise RuntimeError("runtime frame too large")
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                frame = json.loads(line)
                if frame["type"] == "tool_call":
                    if len(_load(root, task_id)["steps"]) >= 24:
                        raise RuntimeError("tool limit")
                    name = frame["name"]
                    step = {"tool": name, "label": TITLES.get(name, "执行接入操作"), "state": "running"}
                    _change(root, task_id, lambda t: t["steps"].append(step))
                    result = tools.execute(name, frame.get("args", {}))
                    def finalized(task):
                        task["steps"][-1].update(state="succeeded" if result.get("ok") else "pending", message=redact(str(result.get("message", "已完成检查"))))
                        task["outcomes"], task["links"] = tools.outcomes, tools.links
                    _change(root, task_id, finalized)
                    process.stdin.write((json.dumps({"type": "tool_result", "id": frame["id"], "result": result}, ensure_ascii=False) + "\n").encode()); process.stdin.flush()
                elif frame["type"] == "done":
                    if not read_only:
                        aliases = {"workbuddy": ("workbuddy", "work buddy"), "wecom": ("企业微信", "企微"), "lemoncloud": ("柠檬", "lemon")}
                        requested = [key for key, names in aliases.items() if any(name in message.lower() for name in names)] if any(x in message for x in ("接入", "接好", "连接", "完成", "配置", "安装")) else []
                        if not requested and "继续" in message:
                            requested = context
                        completed = {result["provider"] for result in tools.outcomes}
                        for provider in requested:
                            if provider not in completed:
                                tools._record({"provider": provider, "status": "failed", "ok": False,
                                               "message": "本次尚未获得该接入操作的执行结果，可以继续重试；未标记为已接通"})
                    states = [r["status"] for r in tools.outcomes]
                    state = ("succeeded" if not states or all(s == "succeeded" for s in states) else
                             "waiting_admin" if all(s == "waiting_admin" for s in states) else
                             "waiting_input" if all(s == "waiting_input" for s in states) else
                             "failed" if all(s == "failed" for s in states) else "partial")
                    # 接入结果由工具事实生成，避免模型把无关 CLI 输出编造成失败原因。
                    summary = "\n\n".join(f"{connectors_name(r['provider'])}：{r['message']}" for r in tools.outcomes) if tools.outcomes else redact(frame.get("message", "执行结束"))[:5000]
                    _change(root, task_id, lambda t: t.update(state=state, message=summary, outcomes=tools.outcomes, links=tools.links))
                    done = True
                elif frame["type"] == "error":
                    raise RuntimeError("runtime failure")
        if not done:
            raise RuntimeError("deadline")
    except Exception:
        _change(root, task_id, lambda t: t.update(state="partial" if tools.outcomes else "failed",
                message="小聚本次执行未全部完成。已执行的步骤保留在下方，可以继续重试。", outcomes=tools.outcomes, links=tools.links))
    finally:
        if selector:
            selector.close()
        if process:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=3)
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()


def connectors_name(provider):
    return {"workbuddy": "WorkBuddy", "wecom": "企业微信", "lemoncloud": "柠檬云"}.get(provider, "接入操作")
