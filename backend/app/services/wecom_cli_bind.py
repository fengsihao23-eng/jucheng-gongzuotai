"""企业微信个人连接自助绑定。

每位员工在小聚里点一次「绑定我的企业微信」，服务端起一次官方 CLI 的扫码会话：
二维码落在该账号自己的目录，扫码确认后把 CLI 写入的凭证整体落位并生成 binding.json。
凭证按账号隔离，其他账号无法读取；绑定失败或过期会自动清理，不留半份配置。
"""
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import time
import uuid
from pathlib import Path

from app.core.response import bad_request, conflict, not_found
from app.services import wecom_cli_service as cli
from app.services.xiaoju_tools import private_write

SESSION_TTL = 300          # 二维码有效期（秒）
QR_TIMEOUT = 30            # 等待 CLI 产出二维码的上限
LINK_RE = re.compile(r"https://work\.weixin\.qq\.com/[^\s\"'<>]+")


def _root(user_id: int) -> Path:
    return cli.home(user_id) / ".bind"


def _session_path(user_id: int) -> Path:
    return _root(user_id) / "session.json"


def _read(user_id: int) -> dict | None:
    try:
        record = json.loads(_session_path(user_id).read_text())
    except (OSError, ValueError, TypeError):
        return None
    return record if isinstance(record, dict) else None


def _write(user_id: int, record: dict) -> None:
    private_write(_session_path(user_id), record)


def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    return True


def _kill(pid) -> None:
    try:
        os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
    except (OSError, ValueError, TypeError):
        try:
            os.kill(int(pid), signal.SIGTERM)
        except (OSError, ValueError, TypeError):
            pass


def _env(config_dir: Path) -> dict:
    env = {key: value for key, value in os.environ.items() if not key.startswith("WECOM_CLI_")}
    env["WECOM_CLI_CONFIG_DIR"] = str(config_dir)
    return env


def _view(record: dict) -> dict:
    expires = record.get("started_at", 0) + SESSION_TTL
    state = record.get("state") or "pending"
    if state == "pending" and time.time() > expires:
        state = "expired"
    return {"session_id": record.get("id"), "state": state, "expires_at": expires,
            "link": record.get("link") or "", "message": record.get("message") or "",
            "operations": record.get("operations") or 0, "services": record.get("services") or 0}


def _cleanup(user_id: int) -> None:
    shutil.rmtree(_root(user_id), ignore_errors=True)


def _authorized(config_dir: Path) -> bool:
    try:
        result = subprocess.run([str(cli.launcher()), "auth", "show", "--status"], capture_output=True,
                                text=True, timeout=20, env=_env(config_dir))
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "authorized" in (result.stdout or "").lower()


def _services(config_dir: Path) -> list:
    result = subprocess.run([str(cli.launcher()), "schema", "list"], capture_output=True, text=True,
                            timeout=40, env=_env(config_dir))
    data = json.loads(result.stdout or "null")
    if not isinstance(data, list):
        raise ValueError("服务目录格式异常")
    return data


def _finalize(user_id: int, record: dict) -> dict:
    """把扫码会话里的凭证整体落位，生成 binding.json。"""
    bind = _root(user_id)
    config = bind / "config"
    if not (config / "credentials.enc").is_file():
        raise bad_request("企业微信尚未完成授权，请重新扫码")
    services = _services(config)
    target = cli.home(user_id) / "config"
    if target.exists():
        shutil.move(str(target), str(cli.home(user_id) / f"config.bak-{int(time.time())}"))
    shutil.move(str(config), str(target))
    document = {"user_id": user_id, "binding_id": uuid.uuid4().hex, "services": services,
                "created_at": time.time(), "source": "self-service"}
    private_write(cli.home(user_id) / "binding.json", document)
    record.update(state="ready", message="企业微信个人连接已绑定", operations=len(cli.eligible_methods(document)),
                  services=len(services))
    shutil.rmtree(bind, ignore_errors=True)
    return {"session_id": record.get("id"), "state": "ready", "expires_at": 0, "link": "",
            "message": record["message"], "operations": record["operations"], "services": record["services"]}


def start(user_id: int) -> dict:
    launcher = cli.launcher()
    if not launcher.is_file():
        raise bad_request("服务器未安装企业微信 CLI，请联系管理员")
    cli.home(user_id).mkdir(parents=True, exist_ok=True, mode=0o700)
    with (cli.home(user_id) / ".bind.lock").open("a") as guard:
        os.chmod(guard.name, 0o600)
        fcntl.flock(guard, fcntl.LOCK_EX)
        current = _read(user_id)
        if current and current.get("state") == "pending" and _alive(current.get("pid")):
            if time.time() < current.get("started_at", 0) + SESSION_TTL:
                return _view(current)
            _kill(current.get("pid"))
        _cleanup(user_id)
        bind = _root(user_id)
        config, work = bind / "config", bind / "work"
        for folder in (bind, config, work):
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        log_path = work / "bind.log"
        with log_path.open("w") as log:
            try:
                process = subprocess.Popen(
                    [str(launcher), "auth", "init", "--noninteractive", "--no-browser", "--output-qrcode", "qr.png"],
                    cwd=work, env=_env(config), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True)
            except OSError:
                shutil.rmtree(bind, ignore_errors=True)
                raise bad_request("企业微信 CLI 无法启动，请检查服务器运行环境") from None
        deadline = time.time() + QR_TIMEOUT
        while time.time() < deadline:
            if (work / "qr.png").is_file() and (work / "qr.png").stat().st_size > 512:
                break
            if process.poll() is not None:
                break
            time.sleep(0.4)
        qr = work / "qr.png"
        if not qr.is_file() or qr.stat().st_size <= 512:
            _kill(process.pid)
            shutil.rmtree(bind, ignore_errors=True)
            raise bad_request("未能获取企业微信授权二维码，请稍后重试")
        try:
            match = LINK_RE.search(log_path.read_text(errors="ignore"))
            link = match.group(0) if match else ""
        except OSError:
            link = ""
        record = {"id": uuid.uuid4().hex, "pid": process.pid, "state": "pending", "started_at": time.time(),
                  "link": link, "user_id": user_id}
        _write(user_id, record)
        return _view(record)


def status(user_id: int, session_id: str) -> dict:
    record = _read(user_id)
    if not record or record.get("id") != session_id:
        # 落位后会话目录已清理；只要本人已有绑定，就按已就绪返回，避免轮询竞态报错。
        document = cli.binding(user_id)
        if document:
            return {"session_id": session_id, "state": "ready", "expires_at": 0, "link": "",
                    "message": "企业微信个人连接已绑定", "operations": len(cli.eligible_methods(document)),
                    "services": len(document.get("services", []))}
        raise not_found("企业微信绑定会话")
    if record.get("state") == "ready":
        return _view(record)
    bind = _root(user_id)
    if (bind / "config" / "credentials.enc").is_file() and _authorized(bind / "config"):
        return _finalize(user_id, record)
    if not _alive(record.get("pid")):
        record.update(state="failed", message="扫码已取消或授权未完成，请重新获取二维码")
        _write(user_id, record)
        _kill(record.get("pid"))
        return _view(record)
    if time.time() > record.get("started_at", 0) + SESSION_TTL:
        _kill(record.get("pid"))
        record.update(state="expired", message="二维码已过期，请重新获取")
        _write(user_id, record)
        return _view(record)
    return _view(record)


def qr_path(user_id: int, session_id: str) -> Path:
    record = _read(user_id)
    if not record or record.get("id") != session_id:
        raise not_found("企业微信绑定会话")
    path = _root(user_id) / "work" / "qr.png"
    if not path.is_file():
        raise not_found("二维码")
    return path


def cancel(user_id: int, session_id: str) -> dict:
    record = _read(user_id)
    if not record or record.get("id") != session_id:
        raise not_found("企业微信绑定会话")
    _kill(record.get("pid"))
    _cleanup(user_id)
    return {"session_id": session_id, "state": "cancelled"}


def unbind(user_id: int) -> dict:
    """解除本人绑定：删除凭证与目录，不影响其他账号。"""
    document = cli.binding(user_id)
    if not document:
        raise conflict("当前账号尚未绑定企业微信个人连接")
    _kill((_read(user_id) or {}).get("pid"))
    _cleanup(user_id)
    shutil.rmtree(cli.home(user_id), ignore_errors=True)
    return {"state": "unbound"}
