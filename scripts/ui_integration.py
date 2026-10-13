"""B2–B3 Windows fixture preview; reuse the existing process ownership helpers.

No private config, network adapters or synthetic queued/running samples. The
optional independent worker uses the existing demo services and persistent lock.
Only PID + kernel creation time records created here may be stopped.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.ui_fixture import audit
from learnradar.demo_launcher import _handle, owned_alive, stop_owned, save
from learnradar.tasks import TaskQueue, WorkerLock, TaskError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'stop', 'status', 'worker'), nargs='?', default='start')
    parser.add_argument('--session', default='ui-integration-b2-b3')
    parser.add_argument('--variant', choices=('normal', 'script-fail', 'model-fail', 'no-js', 'no-webgl'), default='normal')
    parser.add_argument('--count', type=int, default=100)
    parser.add_argument('--worker', action='store_true', help='start the actual offline fixture worker')
    parser.add_argument('--only-worker', action='store_true', help='stop only this launcher\'s worker')
    parser.add_argument('--scenario', choices=('success', 'rejected', 'model-failed', 'mail-failed', 'mail-unknown'), default='success')
    parser.add_argument('--delay', type=float, default=.5)
    args = parser.parse_args(argv)
    if not args.session.replace('-', '').isalnum() or not 0 <= args.count <= 100:
        parser.error('use a simple fixture session and count 0..100')
    directory = ROOT / 'data' / args.session / args.variant
    sys.addaudithook(audit)
    if args.action == 'worker':
        from learnradar.config import load_settings
        from learnradar.demo import demo_settings
        from learnradar.worker import run_worker
        queue = TaskQueue(directory, demo=True)
        settings = demo_settings(load_settings(local_overrides=False), directory)
        return run_worker(settings, queue, poll_seconds=.1, demo_delay=max(0, args.delay), scenario=args.scenario,
                          on_ready=lambda: save(directory / 'integration-launcher' / 'worker-ready.json', {'pid': os.getpid(), 'fixture': True}))
    if os.name != 'nt':
        parser.error('this lifecycle launcher requires Windows; use ui_fixture.py and demo task_cli worker separately')
    folder = directory / 'integration-launcher'
    lock = WorkerLock(directory)
    lock.path = directory / 'locks' / 'ui-integration-launcher.lock'
    if not lock.acquire():
        raise TaskError('another integration launcher is active')
    created = []
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'state.json'
        state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {
            'fixture': True, 'directory': str(directory), 'processes': [], 'url': ''}
        if state.get('fixture') is not True or state.get('directory') != str(directory):
            raise TaskError('fixture lifecycle state does not match this directory')
        records = state.get('processes')
        if not isinstance(records, list) or any(not isinstance(p, dict) or p.get('role') not in ('web', 'worker')
                or not isinstance(p.get('pid'), int) or p['pid'] <= 0 or not str(p.get('birth', '')).isdigit() for p in records):
            raise TaskError('invalid ownership records; no processes touched')
        alive = {p['role']: p for p in records if owned_alive(p)}
        if args.action == 'stop':
            targets = ('worker',) if args.only_worker else ('worker', 'web')
            for role in targets:
                if role in alive:
                    stop_owned(alive.pop(role))
            state['processes'] = list(alive.values()); save(path, state)
            print('fixture processes stopped; database, reports and budget retained')
            return 0
        if args.action == 'status':
            print(json.dumps({'fixture': True, 'url': state['url'], 'running': sorted(alive)}, ensure_ascii=False))
            return 0
        state['processes'] = list(alive.values()); save(path, state)
        for role in ('web', 'worker') if args.worker else ('web',):
            if role in alive:
                continue
            ready = folder / 'worker-ready.json'
            if role == 'worker':
                ready.unlink(missing_ok=True)
            command = [sys.executable, str(ROOT / 'scripts' / ('ui_fixture.py' if role == 'web' else 'ui_integration.py'))]
            if role == 'web':
                command += ['--port', '0', '--count', str(args.count), '--no-state-samples']
            else:
                command += ['worker', '--scenario', args.scenario, '--delay', str(args.delay)]
            command += ['--session', args.session, '--variant', args.variant]
            log = folder / f'{role}-{uuid.uuid4().hex}.log'
            with log.open('w', encoding='utf-8') as stream:
                process = subprocess.Popen(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                    env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'NO_PROXY': '127.0.0.1,localhost'},
                    creationflags=subprocess.CREATE_NO_WINDOW)
            created.append(process)
            value = _handle(process.pid)
            if not value:
                raise TaskError(f'{role} exited at startup; see {log}')
            kernel, handle, birth = value; kernel.CloseHandle(handle)
            state['processes'].append({'role': role, 'pid': process.pid, 'birth': birth, 'log': str(log)})
            save(path, state)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise TaskError(f'{role} exited; see {log}')
                if role == 'worker':
                    if ready.exists() and json.loads(ready.read_text(encoding='utf-8'))['pid'] == process.pid:
                        break
                else:
                    public = directory / 'public-state.json'
                    if public.exists():
                        info = json.loads(public.read_text(encoding='utf-8'))
                        if info['pid'] == process.pid:
                            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                            try:
                                with opener.open(info['url'] + '/health', timeout=2) as response:
                                    if response.status == 200:
                                        state['url'] = info['url']; save(path, state); break
                            except OSError:
                                pass
                time.sleep(.1)
            else:
                raise TaskError(f'{role} readiness timed out; see {log}')
        from learnradar.auth import FIXTURE_USERNAME, FIXTURE_PASSWORD
        (directory / 'fixture-login.txt').write_text(
            f'fixture only; local loopback; no real external services\nusername: {FIXTURE_USERNAME}\npassword: {FIXTURE_PASSWORD}\n', encoding='utf-8')
        print(state['url'])
        return 0
    except Exception:
        for process in created:
            if process.poll() is None:
                process.terminate(); process.wait(timeout=10)
        raise
    finally:
        lock.release()


if __name__ == '__main__':
    raise SystemExit(main())
