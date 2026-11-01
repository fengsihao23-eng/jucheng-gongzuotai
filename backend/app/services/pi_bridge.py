"""Run a bounded, application-owned Pi workflow through the same runtime and model as 小聚."""
import json
import os
import selectors
import shutil
import subprocess
import time

from app.core.config import BASE_DIR, settings
from app.core.response import BizError


def available():
    return bool(settings.llm_api_key and settings.llm_base_url and settings.llm_model
                and shutil.which(settings.assistant_node_bin)
                and (BASE_DIR / 'agent_runtime/node_modules/@earendil-works/pi-coding-agent/package.json').is_file())


def run(*, directory, system, message, specs, execute, timeout=None):
    if not available():
        raise BizError(50301, '小聚的 Pi 内核或模型连接尚未就绪，原件已保存，可稍后重新整理')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    env = {k: os.environ[k] for k in ('PATH', 'LANG', 'TZ', 'SSL_CERT_FILE', 'SSL_CERT_DIR') if k in os.environ}
    env.update(HOME=str(directory), PI_OFFLINE='1', NODE_NO_WARNINGS='1')
    process = subprocess.Popen([settings.assistant_node_bin, str(BASE_DIR / 'agent_runtime/run.mjs')],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        cwd=BASE_DIR, start_new_session=True, env=env)
    selector = selectors.DefaultSelector()
    try:
        frame = {'profile': 'intake', 'model': settings.llm_model, 'thinking': 'off',
                 'baseUrl': settings.llm_base_url, 'apiKey': settings.llm_api_key,
                 'sessionDir': str(directory), 'workspaceEnabled': False, 'maxTurns': 80,
                 'maxOutputTokens': 8192, 'system': system, 'message': message, 'tools': specs}
        process.stdin.write((json.dumps(frame, ensure_ascii=False)+'\n').encode()); process.stdin.flush()
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline, buffer, calls = time.monotonic() + (timeout or settings.import_agent_timeout_seconds), b'', 0
        while time.monotonic() < deadline:
            if not selector.select(.5):
                if process.poll() is not None:
                    raise BizError(50301, '小聚整理任务已中断，原件和已准备的清单仍保留，请重新整理')
                continue
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                raise BizError(50301, '小聚未完成资料分析，请重新整理')
            buffer += chunk
            if len(buffer) > 8 * 1024 * 1024:
                raise BizError(42201, '资料分析结果过大，请拆分文件后重试')
            while b'\n' in buffer:
                line, buffer = buffer.split(b'\n', 1)
                event = json.loads(line)
                if event.get('type') == 'tool_call':
                    calls += 1
                    if calls > 240:
                        raise BizError(42201, '资料较多，未能完整整理，请拆分后重试；尚未导入业务数据')
                    try:
                        result = execute(event['name'], event.get('args') or {})
                    except BizError as exc:
                        result = {'ok': False, 'message': exc.message}
                    except (TypeError, ValueError, KeyError, IndexError):
                        result = {'ok':False,'message':'这一步的整理参数无法校验，请调整字段或按原件归档；未写入业务数据'}
                    process.stdin.write((json.dumps({'type':'tool_result','id':event['id'],'result':result}, ensure_ascii=False)+'\n').encode())
                    process.stdin.flush()
                elif event.get('type') == 'done':
                    return {'engine':'pi-coding-agent', 'model':settings.llm_model, 'calls':calls}
                elif event.get('type') == 'error':
                    raise BizError(50301, '小聚未完成资料分析，未写入业务数据，请稍后重新整理')
        raise BizError(50301, '小聚整理超时，原件已保留，尚未导入业务数据')
    finally:
        selector.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
        for pipe in (process.stdin, process.stdout):
            if pipe: pipe.close()
