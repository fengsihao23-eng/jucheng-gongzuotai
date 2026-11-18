#!/usr/bin/env python3
"""Freeze, build, and publish a frontend against the running API revision.

prepare creates a reviewable snapshot without changing production. publish
accepts that exact snapshot, checks for concurrent source/server changes,
backs up the current frontend, and switches the entry only after uploading
every asset. No backend, database, business file, or credential is replaced.
"""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
INPUTS = ('src', 'public', 'scripts', 'index.html', 'vite.config.js', 'package.json', 'package-lock.json')


def manifest(root):
    files = {}
    for name in INPUTS:
        path = root / name
        candidates = path.rglob('*') if path.is_dir() else [path]
        for item in candidates:
            if item.is_file() and item.name != '.DS_Store':
                files[str(item.relative_to(root))] = hashlib.sha256(item.read_bytes()).hexdigest()
    return files


def artifact_hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def set_static_permissions(root):
    # The release workspace can be private; nginx still needs to read the published static files.
    root.chmod(0o755)
    for path in root.rglob('*'):
        path.chmod(0o755 if path.is_dir() else 0o644)


def ssh(server, script):
    return subprocess.check_output(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', server,
                                    'python3', '-'], input=script, text=True)


def server_state(server):
    return json.loads(ssh(server, """
import json,urllib.request
def read(url):
    with urllib.request.urlopen(url,timeout=15) as r:
        return json.load(r)
health=read('https://juchengcc.com/health')
build=read('https://juchengcc.com/build-info.json')
assert health['status']=='ok'
print(json.dumps({'health':health,'build':build}))
"""))


def prepare(args, output):
    state = server_state(args.server)
    revision = state['health']['revision']
    assert re.fullmatch('[a-f0-9]{40}', revision), 'API did not return a verifiable revision'
    stage = output / 'frontend'
    stage.mkdir(parents=True, exist_ok=False)
    inputs = manifest(ROOT / 'frontend')
    for name in inputs:
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / 'frontend' / name, target)
    (stage / 'node_modules').symlink_to(ROOT / 'frontend/node_modules', target_is_directory=True)
    env = {**os.environ, 'APP_BUILD_REVISION': revision}
    env.pop('VITE_OUT_DIR', None)
    with (output / 'build.log').open('w') as log:
        subprocess.run(['npm', 'run', 'build'], cwd=stage, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    assert manifest(ROOT / 'frontend') == inputs, 'Frontend source changed during the build; prepare again'
    assert manifest(stage) == inputs, 'Frozen build inputs changed'
    build = json.loads((stage / 'dist/build-info.json').read_text())
    assert build['revision'] == revision
    receipt = {'server': args.server, 'prepared_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
               'previous': state, 'build': build, 'inputs': inputs, 'artifacts': artifact_hashes(stage / 'dist')}
    (output / 'release.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({'prepared': True, 'build': build, 'directory': str(output)}, ensure_ascii=False))


def check(output):
    receipt = json.loads((output / 'release.json').read_text())
    stage = output / 'frontend'
    assert manifest(stage) == receipt['inputs'], 'Frozen sources changed after prepare'
    assert manifest(ROOT / 'frontend') == receipt['inputs'], 'Workspace frontend changed; prepare a new snapshot'
    actual = json.loads((stage / 'dist/build-info.json').read_text())
    assert actual == receipt['build'], 'Build metadata changed'
    assert artifact_hashes(stage / 'dist') == receipt['artifacts'], 'Built assets changed after verification'
    current = server_state(receipt['server'])
    assert current['health']['revision'] == receipt['previous']['health']['revision'], 'API changed; prepare again'
    assert current['build'] == receipt['previous']['build'], 'Another frontend was published; review and prepare again'
    return receipt


def publish(output):
    receipt = check(output)
    stage, server = output / 'frontend', receipt['server']
    stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d-%H%M%S')
    remote = '/opt/jucheng/deploy/releases/' + stamp + '-frontend-' + receipt['build']['source_sha256'][:12]
    backup = f"""
import pathlib,subprocess
r=pathlib.Path({remote!r})
r.mkdir(mode=0o700)
subprocess.run(['tar','-czf',str(r/'frontend-before.tar.gz'),'-C','/opt/jucheng/frontend','dist'],check=True)
with (r/'frontend-before.contents').open('w') as f:
    subprocess.run(['tar','-tzf',str(r/'frontend-before.tar.gz')],stdout=f,check=True)
assert (r/'frontend-before.tar.gz').stat().st_size>0
print('Frontend backup verified')
"""
    print(ssh(server, backup).strip())
    set_static_permissions(stage / 'dist')
    subprocess.run(['rsync', '-az', '-e', 'ssh -o BatchMode=yes', '--exclude', 'index.html', '--exclude', 'build-info.json',
                    str(stage / 'dist') + '/', server + ':/opt/jucheng/frontend/dist/'], check=True)
    subprocess.run(['scp', '-q', str(stage / 'dist/index.html'), str(stage / 'dist/build-info.json'),
                    str(output / 'release.json'), f'{server}:{remote}/'], check=True)
    switch = f"""
import fcntl,json,pathlib,shutil,urllib.request
d=pathlib.Path('/opt/jucheng/frontend/dist')
r=pathlib.Path({remote!r})
with open('/opt/jucheng/deploy/frontend-publish.lock','a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    with urllib.request.urlopen('https://juchengcc.com/health',timeout=15) as response:
        health=json.load(response)
    assert health['revision']=={receipt['previous']['health']['revision']!r}, 'API changed before publication'
    previous=json.loads((d/'build-info.json').read_text())
    assert previous=={receipt['previous']['build']!r}, 'Concurrent frontend publication; nothing switched'
    new=json.loads((r/'build-info.json').read_text())
    assert new=={receipt['build']!r}, 'Uploaded build does not match reviewed build'
    (r/'previous-build.json').write_text(json.dumps(previous))
    for name in ('build-info.json','index.html'):
        shutil.copy2(r/name,d/(name+'.next'))
        (d/(name+'.next')).chmod(0o644)
    for name in ('build-info.json','index.html'):
        (d/(name+'.next')).replace(d/name)
print(json.dumps({{'published':True,'build':new,'backup':str(r)}}))
"""
    result = json.loads(ssh(server, switch))
    actual = server_state(server)
    assert actual['build'] == receipt['build'], 'Published public entry does not match the reviewed build'
    assert actual['health']['revision'] == actual['build']['revision'], 'Frontend/API revision mismatch'
    (output / 'publication.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'check', 'publish'))
    parser.add_argument('--directory', required=True)
    parser.add_argument('--server', default='jucheng-ecs')
    args = parser.parse_args()
    output = Path(args.directory).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.command == 'prepare':
        prepare(args, output)
    elif args.command == 'check':
        receipt = check(output)
        print(json.dumps({'checked': True, 'build': receipt['build']}))
    else:
        publish(output)


if __name__ == '__main__':
    main()
