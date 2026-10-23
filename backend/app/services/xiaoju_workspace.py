"""Pi 原生工具运行于无网络、无密钥的独立 Docker 容器。"""
import json
import os
import shutil
import stat
import subprocess
import time
import uuid
from pathlib import Path, PurePosixPath

from app.core.config import settings
from app.core.response import bad_request, forbidden, not_found
from app.services import xiaoju_sessions as sessions

TOOLS = {"read", "write", "edit", "bash", "grep", "find", "ls"}
MAX_FILE = 50 * 1024 * 1024
MAX_WORKSPACE = 200 * 1024 * 1024


class WorkspaceLimit(ValueError):
    pass


def over_limit(path):
    stack, total, count = [(path, 0)], 0, 0
    while stack:
        directory, depth = stack.pop()
        if depth > 64:
            return True
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    count += 1
                    if count > 10000:
                        return True
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    if stat.S_ISREG(info.st_mode):
                        total += info.st_size
                    elif stat.S_ISDIR(info.st_mode):
                        stack.append((Path(entry.path), depth + 1))
                    if total > MAX_WORKSPACE:
                        return True
        except FileNotFoundError:
            continue
        except PermissionError:
            return True
    return False


def root():
    return settings.assistant_workspace_root or settings.storage_dir.parent / "xiaoju" / "workspaces"


def folder(user_id, session_id):
    return root() / str(int(user_id)) / sessions.identifier(session_id)


def available():
    return settings.assistant_sandbox_enabled and bool(shutil.which("docker"))


def prepare(user_id, session_id):
    path = folder(user_id, session_id)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.geteuid() == 0:
        os.chown(path, 10001, 10001)
    return path


def import_attachment(user_id, session_id, attachment_id):
    from app.services import xiaoju_business_state as state
    record, source = state.attachment(attachment_id, user_id)
    target = prepare(user_id, session_id) / "uploads"
    target.mkdir(exist_ok=True)
    name = attachment_id + "_" + Path(record["filename"]).name
    dest = target / name
    if dest.is_symlink() or target.is_symlink():
        raise forbidden("工作文件目录无效")
    from app.services.xiaoju_tools import private_bytes
    private_bytes(dest, source.read_bytes())
    if os.geteuid() == 0:
        os.chown(target, 10001, 10001)
        os.chown(dest, 10001, 10001)
    return "/workspace/uploads/" + name


def _mount(user_id, session_id):
    relative = f"{int(user_id)}/{sessions.identifier(session_id)}"
    if settings.assistant_workspace_volume:
        return f"type=volume,src={settings.assistant_workspace_volume},dst=/workspace,volume-subpath={relative}"
    return f"type=bind,src={folder(user_id, session_id).resolve()},dst=/workspace"


def execute(user_id, session_id, tool, args, task_id=""):
    sessions.load(user_id, session_id)  # 每次调用重新核对权限，含密码/连接变更。
    if not available():
        return {"content": [{"type": "text", "text": "独立执行环境尚未启用，请联系管理员完成部署。"}], "isError": True}
    if tool not in TOOLS or not isinstance(args, dict):
        raise bad_request("未知的工作文件工具")
    prepare(user_id, session_id)
    container = "xiaoju-" + uuid.uuid4().hex
    uid, gid = (10001, 10001) if os.geteuid() == 0 else (os.getuid(), os.getgid())
    command = ["docker", "run", "--rm", "--name", container, "--network", "none", "--read-only",
               "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", f"{uid}:{gid}",
               "--cpus", "1", "--memory", "384m", "--memory-swap", "384m", "--pids-limit", "64",
               "--ulimit", f"fsize={MAX_FILE}:{MAX_FILE}", "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m,mode=1777",
               "--mount", _mount(user_id, session_id), "--workdir", "/workspace", "--env", "HOME=/tmp",
               "--env", "PI_OFFLINE=1", "--label", "jucheng.xiaoju=true", "--label", f"jucheng.task={task_id}",
               "-i", settings.assistant_sandbox_image, "node", "/runtime/workspace.mjs"]
    # 容器文件系统中没有 API 配置、数据库凭证、Docker socket 或其他员工工作文件。
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + settings.assistant_sandbox_timeout_seconds
        initial = json.dumps({"tool": tool, "args": args})
        while True:
            try:
                output, _ = process.communicate(input=initial, timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                initial = None
                if over_limit(folder(user_id, session_id)):
                    raise WorkspaceLimit()
                if task_id:
                    from app.services.xiaoju_service import _load, task_root
                    if _load(task_root(), task_id).get("cancel_requested"):
                        raise subprocess.TimeoutExpired(command, 0)
                if time.monotonic() > deadline:
                    raise subprocess.TimeoutExpired(command, settings.assistant_sandbox_timeout_seconds)
        if process.returncode:
            return {"content": [{"type": "text", "text": "工作文件工具未完成，请核对文件或脚本后重试。"}], "isError": True}
        if len(output) > 8 * 1024 * 1024:
            raise ValueError("tool response too large")
        value = json.loads(output)
        if over_limit(folder(user_id, session_id)):
            raise WorkspaceLimit()
        return value
    except WorkspaceLimit:
        return {"content": [{"type": "text", "text": "工作文件超过 200MB、10000 个文件或目录层级限制，已停止脚本；请清理后重试。"}], "isError": True}
    except subprocess.TimeoutExpired:
        return {"content": [{"type": "text", "text": "脚本超过执行时限，已停止；已保存的工作文件仍在。"}], "isError": True}
    except (OSError, ValueError):
        return {"content": [{"type": "text", "text": "独立执行环境暂时不可用，请稍后重试。"}], "isError": True}
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=10)
        if 'process' in locals():
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()


def list_files(user_id, session_id):
    sessions.load(user_id, session_id)
    path = folder(user_id, session_id)
    files = []
    if not path.exists():
        return files
    stack = [(path, 0)]
    while stack:
        directory, depth = stack.pop()
        if depth > 64:
            continue
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.name.startswith("."):
                        continue
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    target = Path(entry.path)
                    if stat.S_ISDIR(info.st_mode):
                        if len(stack) < 1000:
                            stack.append((target, depth + 1))
                    elif stat.S_ISREG(info.st_mode):
                        files.append({"path": target.relative_to(path).as_posix(), "size": info.st_size})
                    if len(files) >= 500:
                        return files
        except (FileNotFoundError, PermissionError):
            continue
    return sorted(files, key=lambda f: f["path"])


def read_file(user_id, session_id, name):
    sessions.load(user_id, session_id)
    parts = PurePosixPath(name).parts
    if not parts or len(parts) > 64 or len(name) > 4096 or name.startswith("/") or any(p in {"..", "."} or p.startswith(".") for p in parts):
        raise forbidden("只能下载当前对话的工作文件")
    # openat + O_NOFOLLOW 逐级打开，避免脚本创建目录/文件软链接绕过账号范围。
    descriptors = []
    try:
        fd = os.open(folder(user_id, session_id), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(fd)
        for part in parts[:-1]:
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            descriptors.append(fd)
        handle = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        descriptors.append(handle)
        info = os.fstat(handle)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE:
            raise bad_request("文件不可下载或超过 50MB")
        with os.fdopen(os.dup(handle), "rb") as stream:
            return stream.read(MAX_FILE + 1), parts[-1]
    except OSError:
        raise not_found("工作文件") from None
    finally:
        for fd in reversed(descriptors):
            os.close(fd)
