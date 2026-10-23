"""Run the shipped full SDK against a local deterministic SSE provider (no model credentials)."""
import json
import os
import selectors
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from app.core.config import BASE_DIR


@pytest.fixture
def provider():
    requests, replies = [], []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(payload)
            reply = replies.pop(0) if replies else {'text':'fixture-complete'}
            delta = {'role':'assistant','content':reply.get('text','')}
            if 'tool' in reply:
                delta['tool_calls'] = [{'index':0,'id':'fixture-call','type':'function','function':{'name':reply['tool'],'arguments':json.dumps(reply.get('args',{}))}}]
            body = 'data: ' + json.dumps({'id':'fixture','object':'chat.completion.chunk','created':1,'model':'deepseek-flash','choices':[{'index':0,'delta':delta,'finish_reason':None}]}) + '\n\n'
            body += 'data: ' + json.dumps({'id':'fixture','object':'chat.completion.chunk','created':1,'model':'deepseek-flash','choices':[{'index':0,'delta':{},'finish_reason':'tool_calls' if 'tool' in reply else 'stop'}],'usage':{'prompt_tokens':100,'completion_tokens':10,'total_tokens':110}}) + '\n\ndata: [DONE]\n\n'
            data = body.encode()
            self.send_response(200); self.send_header('Content-Type','text/event-stream'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
    server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    yield f'http://127.0.0.1:{server.server_port}', requests, replies
    server.shutdown(); server.server_close(); thread.join(timeout=2)


def run_pi(tmp_path, provider, host_result=None, **options):
    if not (BASE_DIR/'agent_runtime/node_modules/@earendil-works/pi-coding-agent/package.json').exists():
        pytest.skip('Run npm ci in backend/agent_runtime first')
    payload = {'sessionDir':str(tmp_path),'model':'deepseek-flash','baseUrl':provider[0],'apiKey':'fixture-key-not-a-real-credential','system':'Complete the fixture request.','message':'历史专用标记','tools':[], 'workspaceEnabled':True, 'thinking':'off','maxOutputTokens':1024, **options}
    process = subprocess.Popen(['node',str(BASE_DIR/'agent_runtime/run.mjs')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env={'PATH':os.environ['PATH'],'HOME':str(tmp_path),'PI_OFFLINE':'1','XIAOJU_RUNTIME_DEBUG':'1'})
    frames, calls = [], []
    process.stdin.write(json.dumps(payload)+'\n'); process.stdin.flush()
    selector = selectors.DefaultSelector(); selector.register(process.stdout,selectors.EVENT_READ)
    deadline = time.monotonic()+30
    try:
        while time.monotonic()<deadline:
            if not selector.select(0.5):
                if process.poll() is not None: break
                continue
            line = process.stdout.readline()
            if not line: break
            frame = json.loads(line); frames.append(frame)
            if frame['type']=='tool_call':
                calls.append(frame)
                result = {'ok':True} if frame['name']=='check_session_scope' else (host_result or {'content':[{'type':'text','text':'sandbox-result'}]})
                process.stdin.write(json.dumps({'type':'tool_result','id':frame['id'],'result':result})+'\n'); process.stdin.flush()
            if frame['type'] in {'done','error'}: break
        process.wait(timeout=5)
        assert process.returncode==0, process.stderr.read()
        assert frames[-1]['type']=='done', frames
        return frames, calls
    finally:
        selector.close()
        if process.poll() is None: process.kill(); process.wait()
        process.stdin.close(); process.stdout.close(); process.stderr.close()


def test_native_tools_are_proxied_and_resources_are_scoped(tmp_path, provider):
    provider[2].extend([{'tool':'read','args':{'path':'/etc/passwd'}},{'text':'sandbox-read-complete'}])
    frames, calls = run_pi(tmp_path,provider)
    ready = next(f for f in frames if f['type']=='ready')
    assert {s['name'] for s in ready['skills']} == {'company-business','work-files'}
    assert {'read','write','edit','bash','grep','ls','find','codemode','tool_search'} <= set(ready['tools'])
    proxied = [f for f in calls if f['name']!='check_session_scope']
    assert len(proxied)==1 and proxied[0]['name']=='workspace_tool'
    assert proxied[0]['args']=={'tool':'read','args':{'path':'/etc/passwd'}}
    assert frames[-1]['message']=='sandbox-read-complete'
    stored = (tmp_path/'session.jsonl').read_text()
    assert 'fixture-key-not-a-real-credential' not in stored and 'sandbox-result' in stored


def test_full_history_survives_new_runtime_process(tmp_path,provider):
    provider[2].extend([{'text':'first-response'},{'text':'restored-response'}])
    run_pi(tmp_path,provider)
    run_pi(tmp_path,provider,message='继续原来的对话')
    request = json.dumps(provider[1][-1],ensure_ascii=False)
    assert '历史专用标记' in request and 'first-response' in request and '继续原来的对话' in request
    assert (tmp_path/'session.jsonl').stat().st_mode & 0o777 == 0o600


def test_confirmation_terminates_before_another_model_call(tmp_path,provider):
    spec={'name':'execute_business_operation','description':'Fixture business tool.','parameters':{'type':'object','properties':{}}}
    provider[2].append({'tool':'execute_business_operation'})
    frames,calls=run_pi(tmp_path,provider,host_result={'ok':True,'requires_confirmation':True},tools=[spec])
    assert len(provider[1])==1
    assert any(f['name']=='execute_business_operation' for f in calls)
    assert not frames[-1]['budgetExceeded']


def test_iteration_budget_returns_partial_signal(tmp_path,provider):
    provider[2].append({'tool':'ls','args':{}})
    frames,_=run_pi(tmp_path,provider,maxTurns=1)
    assert frames[-1]['budgetExceeded'] and len(provider[1])==1

def test_sdk_compaction_persists_summary(tmp_path,provider):
    history = [{'role':'user','content':'past-marker ' * 25000},{'role':'assistant','content':'past response'},
               {'role':'user','content':'recent-marker ' * 25000},{'role':'assistant','content':'recent response'}]
    provider[2].append({'text':'fixture-compaction-summary'})
    frames,_=run_pi(tmp_path,provider,history=history,action='compact')
    assert '已压缩' in frames[-1]['message']
    records=[json.loads(line) for line in (tmp_path/'session.jsonl').read_text().splitlines()]
    assert any(r['type']=='compaction' and 'fixture-compaction-summary' in r['summary'] for r in records)
