"""完整 Pi 会话、跨账号访问、权限失效、工作文件与流式恢复。"""
import stat
import pytest
from sqlalchemy import select
from app.core.config import settings
from app.core.response import BizError
from app.models import SysUser
from app.services import xiaoju_sessions as sessions, xiaoju_service as service, xiaoju_workspace as workspace
from tests.conftest import data

@pytest.fixture
def account(db):
    return db.scalar(select(SysUser.id).where(SysUser.wecom_userid == 'admin'))

@pytest.fixture
def idle(monkeypatch):
    monkeypatch.setattr(service, 'runtime_info', lambda: {'available': True})
    monkeypatch.setattr(service._executor, 'submit', lambda *a: None)

def test_sessions_are_server_side_and_owned(client, login, idle):
    conversation = data(client.post('/api/v1/assistant/sessions', headers=login('admin'), json={}))
    sid = conversation['id']
    task = data(client.post('/api/v1/assistant/tasks', headers=login('admin'), json={'message': '记住本次统计范围', 'session_id': sid}))
    restored = data(client.get('/api/v1/assistant/sessions/' + sid, headers=login('admin')))
    assert restored['messages'][-1]['text'] == '记住本次统计范围'
    assert restored['active_task_id'] == task['id']
    assert client.get('/api/v1/assistant/sessions/' + sid, headers=login('zengli')).status_code == 404
    assert client.post('/api/v1/assistant/tasks', headers=login('zengli'), json={'message': '继续', 'session_id': sid}).status_code == 404
    assert stat.S_IMODE((sessions.folder(task['user_id'], sid) / 'meta.json').stat().st_mode) == 0o600

def test_same_session_rejects_concurrent_tasks(client, login, idle):
    first = data(client.post('/api/v1/assistant/tasks', headers=login('admin'), json={'message': '准备一份汇总'}))
    second = client.post('/api/v1/assistant/tasks', headers=login('admin'), json={'message': '另一个请求', 'session_id': first['session_id']})
    assert second.status_code == 409
    assert client.patch('/api/v1/assistant/sessions/' + first['session_id'], headers=login('admin'), json={'thinking':'off'}).status_code == 409
    service._change(service.task_root(), first['id'], lambda t: t.update(state='succeeded', message='已完成'))
    sessions.finish(first['user_id'], first['session_id'], first['id'], '已完成')
    next_task = data(client.post('/api/v1/assistant/tasks', headers=login('admin'), json={'message':'继续', 'session_id':first['session_id']}))
    assert next_task['session_id'] == first['session_id']

def test_changed_permissions_invalidate_model_history(account, db):
    conversation = sessions.create(account)
    user = db.get(SysUser, account)
    previous = user.password_hash
    try:
        user.password_hash = previous + 'changed'; db.commit()
        with pytest.raises(BizError): sessions.load(account, conversation['id'])
        assert conversation['id'] not in {item['id'] for item in sessions.list_for_user(account)}
    finally:
        user.password_hash = previous; db.commit()

def test_fork_keeps_context_and_separates_workflow(account):
    source = sessions.create(account)
    source['messages'] = [{'role':'user','text':'原对话范围'}]
    sessions.save(source)
    branch = sessions.fork(account, source['id'])
    assert branch['id'] != source['id'] and branch['messages'] == source['messages']
    assert branch['active_task_id'] is None and branch['last_task_id'] is None

def test_model_and_thinking_allowlist(account):
    with pytest.raises(BizError): sessions.create(account, model='unconfigured-model')
    with pytest.raises(BizError): sessions.create(account, thinking='unlimited')
    assert sessions.create(account, thinking='high')['thinking'] == 'high'

def test_workspace_download_denies_links_and_traversal(account, tmp_path, monkeypatch, client, login):
    monkeypatch.setattr(settings, 'assistant_workspace_root', tmp_path / 'workspaces')
    conversation = sessions.create(account)
    path = workspace.prepare(account, conversation['id'])
    (path / '结果.txt').write_text('actual result')
    outside = tmp_path / 'secret.txt'; outside.write_text('private-not-downloadable')
    (path / 'link.txt').symlink_to(outside)
    (path / 'other').symlink_to(tmp_path, target_is_directory=True)
    content, filename = workspace.read_file(account, conversation['id'], '结果.txt')
    assert content == b'actual result' and filename == '结果.txt'
    for name in ['../secret.txt', '/etc/passwd', 'link.txt', 'other/secret.txt']:
        with pytest.raises(BizError): workspace.read_file(account, conversation['id'], name)
    assert 'link.txt' not in {f['path'] for f in workspace.list_files(account, conversation['id'])}
    url = '/api/v1/assistant/sessions/' + conversation['id'] + '/files?path=结果.txt'
    response = client.get(url, headers=login('admin'))
    assert response.status_code == 200 and response.content == b'actual result'
    assert response.headers['cache-control'] == 'no-store'
    assert client.get(url, headers=login('zengli')).status_code == 404

def test_stream_restores_terminal_task_with_owner_check(client, login, idle):
    task = data(client.post('/api/v1/assistant/tasks', headers=login('admin'), json={'message':'流式恢复验证'}))
    service._change(service.task_root(), task['id'], lambda t: t.update(state='succeeded', message='完成', partial_text='实际流式结果'))
    response = client.get('/api/v1/assistant/tasks/' + task['id'] + '/stream', headers=login('admin'))
    assert response.status_code == 200 and response.headers['content-type'].startswith('text/event-stream')
    assert 'event: task' in response.text and '实际流式结果' in response.text
    assert client.get('/api/v1/assistant/tasks/' + task['id'] + '/stream', headers=login('zengli')).status_code == 403

def test_steer_is_owned_and_rejects_secrets(client, login, idle):
    task = data(client.post('/api/v1/assistant/tasks', headers=login('admin'), json={'message':'准备工作'}))
    url = '/api/v1/assistant/tasks/' + task['id'] + '/steer'
    assert client.post(url, headers=login('zengli'), json={'message':'额外要求'}).status_code == 403
    assert client.post(url, headers=login('admin'), json={'message':'Secret=private-key-value'}).status_code == 400
    data(client.post(url, headers=login('admin'), json={'message':'只需中文结果'}))
    restored = data(client.get('/api/v1/assistant/sessions/' + task['session_id'], headers=login('admin')))
    assert restored['messages'][-1]['text'] == '只需中文结果'

def test_task_resume_cannot_cross_conversations(client, login, idle):
    first = data(client.post('/api/v1/assistant/tasks', headers=login('admin'), json={'message':'第一个任务'}))
    other = data(client.post('/api/v1/assistant/sessions', headers=login('admin'), json={}))
    response = client.post('/api/v1/assistant/tasks', headers=login('admin'), json={'message':'继续','session_id':other['id'],'resume_task_id':first['id']})
    assert response.status_code == 400

def test_queued_task_from_exited_worker_can_resume(client, login, idle):
    task = data(client.post('/api/v1/assistant/tasks', headers=login('admin'), json={'message':'重启前排队任务'}))
    missing_worker = 'a' * 32
    service._change(service.task_root(), task['id'], lambda t: t.update(runtime_worker=missing_worker))
    (service.task_root() / '.workers' / missing_worker).touch(mode=0o600)
    recovered = data(client.get('/api/v1/assistant/tasks/' + task['id'], headers=login('admin')))
    assert recovered['state'] == 'failed' and '已中断' in recovered['message']
    configured = data(client.patch('/api/v1/assistant/sessions/' + task['session_id'], headers=login('admin'), json={'thinking':'off'}))
    assert configured['active_task_id'] is None
    restarted = data(client.post('/api/v1/assistant/tasks', headers=login('admin'), json={'message':'继续','session_id':task['session_id']}))
    assert restarted['state'] == 'queued'

def test_fork_cannot_relabel_old_context_after_scope_change(account, monkeypatch):
    source = sessions.create(account)
    source['messages'] = [{'role':'assistant','text':'old-private-context'}]
    sessions.save(source)
    changed = {**source['scope'], 'actor':'permissions-changed'}
    calls = []
    def moving_scope(_uid):
        calls.append(True)
        return source['scope'] if len(calls) == 1 else changed
    monkeypatch.setattr(sessions, 'scope', moving_scope)
    with pytest.raises(BizError): sessions.fork(account, source['id'])
    assert sessions.list_for_user(account) == []
