"""Deploy a hash-checked execution patch, retaining production venv and data.

The full current suite is run against both actual production source and proposed
source. Existing identity failures are printed, never reported as passing; any new
failure or any new execution-recovery test failure blocks this limited hotfix.
The normal deployment workflow retains its full-suite gate.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

parser = argparse.ArgumentParser()
parser.add_argument('payload')
args = parser.parse_args()
payload = json.loads(Path(args.payload).read_text())
root = Path('/opt/hyperevm-radar')
service = 'hyperevm-radar.service'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def progress():
    with sqlite3.connect('file:' + str(root / 'data/radar.db') + '?mode=ro', uri=True) as conn:
        return conn.execute('SELECT next_block FROM radar_scan_progress WHERE chain_id=999').fetchone()[0]


def health():
    keys = ('ActiveState', 'SubState', 'MainPID', 'NRestarts')
    value = command('systemctl', 'show', service, *[f'--property={key}' for key in keys])
    return dict(line.split('=', 1) for line in value.splitlines())


for name, expected in payload['baseline'].items():
    if digest(root / name) != expected:
        raise RuntimeError('Production source changed: ' + name)
assert health()['ActiveState'] == 'active'
venv = root / '.venv/bin/python'
venv_before = (venv.stat().st_dev, venv.stat().st_ino)

with tempfile.TemporaryDirectory(prefix='hyper-execution-check-') as tmp:
    tmp = Path(tmp)
    before = tmp / 'before'
    shutil.copytree(root, before, ignore=shutil.ignore_patterns('.venv', 'data', '.env', '__pycache__', '*.pyc'))
    for name, content in payload['tests'].items():
        path = before / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    after = tmp / 'after'
    shutil.copytree(before, after)
    for name, content in payload['files'].items():
        path = after / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    runner = tmp / 'check.py'
    runner.write_text(payload['runner'])
    reports = []
    for label, source in [('baseline', before), ('proposed', after)]:
        argv = [sys.executable, str(runner), '--source', str(source)]
        if label == 'baseline':
            argv.append('--baseline')
        output = subprocess.check_output(argv, cwd=source, text=True, timeout=120)
        report = json.loads(next(line[len('TEST_REPORT '):] for line in output.splitlines() if line.startswith('TEST_REPORT ')))
        reports.append(report)
        print('FULL_SUITE_' + label.upper(), json.dumps({k: v for k, v in report.items() if k != 'details'}), flush=True)
    assert not reports[0]['errors'], 'Baseline test error requires investigation'
    assert not reports[1]['errors'], 'Proposed test error'
    assert set(reports[1]['failures']).issubset(reports[0]['failures']), 'New regression'
    assert reports[1]['count'] == reports[0]['count'] + 11, 'Missing execution tests'
    assert not any('test_execution_recovery.' in test or 'test_identity_execution_budget.' in test for test in reports[1]['failures'])

backup = Path('/opt/hyperevm-radar.execution-backup.' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
backup.mkdir(mode=0o700)
for name in payload['files']:
    if (root / name).exists():
        path = backup / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / name, path)
(backup / 'test-report.json').write_text(json.dumps(reports, indent=2))
with sqlite3.connect(root / 'data/radar.db') as source, sqlite3.connect(backup / 'radar.db') as destination:
    source.backup(destination)
cursor_before = progress()
print('BEFORE_DEPLOY', json.dumps({'cursor': cursor_before, 'service': health()}), flush=True)
stopped = False
try:
    command('systemctl', 'stop', service)
    stopped = True
    for name, content in payload['files'].items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.execution-new')
        temporary.write_text(content)
        if path.exists():
            metadata = path.stat()
            os.chmod(temporary, metadata.st_mode)
            os.chown(temporary, metadata.st_uid, metadata.st_gid)
        else:
            os.chmod(temporary, 0o644)
        temporary.replace(path)
    subprocess.run([sys.executable, '-c', 'from execution_state import init_execution_db; init_execution_db(); init_execution_db()'], cwd=root, check=True)
    # Recheck unresolved legacy investigations once after the execution repair.
    # Keep attempt history; new execution failures have their own bounded retry.
    with sqlite3.connect(root / 'data/radar.db') as conn:
        old=conn.execute("SELECT subject_key,result FROM platform_identity_investigations WHERE verification_status!='VERIFIED'").fetchall()
        keys=[(row[0],) for row in old if not json.loads(row[1]).get('execution_status')]
        conn.executemany('UPDATE platform_identity_investigations SET next_retry_at=0 WHERE subject_key=?',keys)
        print('LEGACY_INVESTIGATIONS_REQUEUED',len(keys),flush=True)
    command('systemctl', 'start', service)
    stopped = False
    samples = []
    for _ in range(3):
        time.sleep(20)
        sample = {'at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'cursor': progress(), 'service': health()}
        samples.append(sample)
        print('AFTER_DEPLOY', json.dumps(sample), flush=True)
        assert sample['service']['ActiveState'] == 'active' and sample['service']['SubState'] == 'running'
        assert sample['service']['NRestarts'] == '0', 'Service restarted unexpectedly'
    assert samples[-1]['cursor'] > cursor_before, 'Scanner did not advance'
    assert samples[-1]['cursor'] > samples[0]['cursor'], 'Scanner stopped advancing'
    assert len({sample['service']['MainPID'] for sample in samples}) == 1
    assert (venv.stat().st_dev, venv.stat().st_ino) == venv_before, 'Production venv changed'
    for name, content in payload['files'].items():
        assert digest(root / name) == hashlib.sha256(content.encode()).hexdigest()
    with sqlite3.connect('file:' + str(root / 'data/radar.db') + '?mode=ro', uri=True) as conn:
        for table, field in [('telegram_outbox', 'status'), ('contract_processing', 'state'), ('pool_observations', 'validation_status')]:
            print('EXECUTION_STATE', table, json.dumps(conn.execute(f'SELECT {field},count(*) FROM {table} GROUP BY {field}').fetchall()), flush=True)
    print('DEPLOY_EXECUTION_SUCCESS', json.dumps({'backup': str(backup), 'venv_preserved': True, 'cursor_before': cursor_before, 'cursor_after': samples[-1]['cursor'], 'remaining_identity_test_failures': len(reports[1]['failures'])}), flush=True)
except BaseException:
    command('systemctl', 'stop', service)
    for name in payload['files']:
        if (backup / name).exists():
            shutil.copy2(backup / name, root / name)
        else:
            (root / name).unlink(missing_ok=True)
    # Additive tables are retained; never roll back newly observed production data.
    command('systemctl', 'start', service)
    print('DEPLOY_ROLLED_BACK', json.dumps(health()), flush=True)
    raise
