"""Run selected extension scenarios without activating a working installation."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import signal
import sys
import time
import uuid

from . import VERSION
from .capabilities import capabilities, invocation

HELP = '''Optional checks of selected extensions; use a shell timeout of 240 seconds.
Create AND run a short test in ONE shell call using this whole block.
Adapt demo/ping and assertions to your extension; this example requires a ping
query returning {"ok": true}. No separate file-write call or Python setup is needed.

set -e
cd -- <installation-directory>
mkdir -p tests/.tmp
cat > tests/.tmp/check_demo.py <<'PY'
from extension_tools import ExtensionTestCase
class Check(ExtensionTestCase):
    async def test_ping(self):
        response = await self.query('demo', 'ping', {})
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body['result'], {'ok': True})
PY
<example-command>

Results go to a new tests/.tmp/ directory automatically; --output is unnecessary.
Related calls and assertions can share one test method. To rerun an existing file,
execute just the last command; the file is ordinary unittest, not a new test DSL.

Without --tests only registration is checked; this does not check behaviour.
Each test_* method gets a fresh process, migrated synthetic DB and real loader/HTTP.
Write your assertions; the example does not define your extension's requirements.
The checker loads selected extensions. Do not import them again in the scenario
for registration. query/command call existing registered module operations only;
they do not expose Telegram callbacks or arbitrary functions as HTTP operations.

ExtensionTestCase helpers:
  await self.query(module_id, operation, inputs=None, *, actor='owner')
  await self.command(module_id, operation, inputs=None, *, actor='owner', idempotency_key=...)
Both return Response(status, body), including real HTTP errors. Only command()
requires an explicit string idempotency_key; repeat it with the same inputs for retry.
actor: 'owner', 'other', 'site_only' (no Telegram). self.actors[actor] gives the
synthetic internal account ID: use it in fixture records, never guess numeric IDs.
Override async prepare_extension(self), not asyncSetUp: it runs BEFORE loading.
  self.set_settings(module_id, values)                 # declared settings mapping
  self.storage(module_id).set(key, value) / .get(key)   # this test's storage
  service = await self.http_service(aiohttp.web.Application())
The helper starts/stops the local service; service.url is its base URL and
service.requests its request journal. Set its URL with set_settings before loading.
Services share the scenario event loop: use cooperative async IO; blocking clients
can starve them. This is not an independent external-process timing test.
  self.require_environment(condition, reason)          # false makes run incomplete
Ordinary unittest/IsolatedAsyncioTestCase also run; these helpers need ExtensionTestCase.

stdout: compact JSON; detailed report, source hashes and bounded stdout/stderr in
the returned artifact directory. Exit 0=passed, 1=failed, 2=incomplete.
Zero discovered tests in supplied --tests files is incomplete.
Skipped/expected-failure tests are incomplete, not a successful functional check.
An assertion mismatch does not prove which of the code or expectation is wrong.
Requires root-managed Linux/systemd >=249 and installed Python dependencies.
External networking is disabled. Never copies installation data/config, activates
extensions or restarts the bot. Telegram, scheduler and frontend are not exercised.
Limits: 180 s, 640 MiB, 1 CPU, 64 tasks, 128 MiB scratch, 4 MiB diagnostic output.
Passing certifies only the stated scenarios; this optional check is not an
activation/publication requirement. Reuse existing extensions inspection; no repeat
is needed before every run. If extension_check is absent or available=false, keep
the prior verification workflow; do not install the checker or repair its environment.
KB version alone does not establish availability. This help is optional installed-
version reference; reading it and the KB is not mandatory. The KB usage address is
agent_extension_platform.md / extension_check_api; if absent, this help is sufficient
for the checker and does not replace the wider customization KB.
'''


class Arguments(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def main(argv=None):
    started = time.monotonic()
    usage = invocation()
    parser = Arguments(description=HELP.replace('<example-command>', usage['example_command'])
                       .replace('<installation-directory>', shlex.quote(str(Path(__file__).resolve().parents[1]))),
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--extension', action='append', default=[], help='Explicit Python extension; repeat for dependencies')
    parser.add_argument('--tests', action='append', default=[], help='unittest Python file; repeat as needed')
    parser.add_argument('--output', help='New artifact directory below this installation/tests/.tmp')
    parser.add_argument('--capabilities', action='store_true', help='Read-only availability and installed command')
    report = {'run_id': uuid.uuid4().hex, 'version': VERSION, 'python': sys.version,
              'result': 'incomplete', 'summary': '', 'found': 0, 'executed': 0, 'successful': 0,
              'unsuccessful': 0, 'behaviour_checked': False, 'not_performed': [], 'problems': [],
              'checked_stages': [], 'code_version': None, 'source_hashes': {}}
    output = None
    try:
        args = parser.parse_args(argv)
        capability = capabilities()
        if args.capabilities:
            print(json.dumps(capability, ensure_ascii=False))
            return 0
        root = Path(__file__).resolve().parents[1]
        if not args.extension:
            raise ValueError('At least one --extension is required')
        extensions, tests = [Path(p).absolute() for p in args.extension], [Path(p).absolute() for p in args.tests]
        if any(not re.fullmatch(r'[a-z][a-z0-9_]*\.py', p.name) for p in extensions):
            raise ValueError('Extension names must match [a-z][a-z0-9_]*.py')
        if len({p.name for p in extensions}) != len(extensions):
            raise ValueError('Selected extensions must have distinct file names')
        if any(p.suffix != '.py' or p.name == 'config.py' or not p.is_file() for p in [*extensions, *tests]):
            raise ValueError('All selected inputs must be existing Python files')
        if not capability['available']:
            report['not_performed'].append(capability['reason'])
            return finish(report, started)
        from .sandbox import PythonSandbox, regular_bytes
        for path in [*extensions, *tests]:
            regular_bytes(path)
        candidate = Path(args.output).absolute() if args.output else root / 'tests/.tmp' / ('extension-check-' + report['run_id'])
        from web_tools.build import admin_directory
        candidate = admin_directory(candidate)
        if not candidate.is_relative_to(root / 'tests/.tmp') or candidate.exists():
            raise ValueError('--output must be a new directory inside installation/tests/.tmp')
        candidate.mkdir(parents=True, mode=0o700)
        output = candidate
        with PythonSandbox(root, extensions, tests, output) as sandbox:
            report['code_version'] = hashlib.sha256(json.dumps(sandbox.manifest, sort_keys=True).encode()).hexdigest()
            report['source_hashes'] = {name: entry['sha256'] for name, entry in sandbox.manifest.items()
                                      if name.startswith(('custom_extensions/', 'scenarios/'))}
            report['preparation_seconds'] = time.monotonic() - started
            try:
                result = sandbox.execute()
                (output / 'stdout.log').write_bytes(result.stdout)
                (output / 'stderr.log').write_bytes(result.stderr)
                if result.returncode:
                    report['not_performed'].append('Isolated process exited with code ' + str(result.returncode))
                else:
                    report['process_exit_code'] = 0
            except (Exception, KeyboardInterrupt) as error:
                for name in ('output', 'stderr'):
                    data = getattr(error, name, None)
                    (output / ('stdout.log' if name == 'output' else 'stderr.log')).write_bytes(data or b'')
                report['not_performed'].append(str(error)[:1000] or 'Check cancelled')
            sandbox.export()  # The inherited executor has verified cgroup shutdown.
        detail_path = output / 'report.json'
        if detail_path.exists():
            detail = json.loads(detail_path.read_text(encoding='utf-8'))
            for name in ('found', 'executed', 'successful', 'unsuccessful', 'behaviour_checked'):
                report[name] = detail.get(name, report[name])
            if report.get('process_exit_code') == 0:
                report['result'] = detail['result']
            report['not_performed'] += detail.get('not_performed', [])
            report['checked_stages'] = sorted({stage['stage'] for case in detail.get('cases', [])
                                               for stage in case.get('evidence', {}).get('stages', [])})
            report['problems'] = (detail.get('problems', []) +
                [p for case in detail.get('cases', []) if case['result'] != 'passed'
                 for p in (case.get('evidence', {}).get('diagnostics', []) + case.get('problems', []))])[:5]
            for problem in report['problems']:
                problem.pop('traceback', None)
                problem['message'] = problem.get('message', '')[:500]
        report['artifacts'] = {'directory': str(output), 'report': str(detail_path) if detail_path.exists() else None,
                               'sources': str(output / 'sources.json'),
                               'stdout': str(output / 'stdout.log'), 'stderr': str(output / 'stderr.log'),
                               'resources': str(output / 'resources.json')}
    except (Exception, KeyboardInterrupt) as error:
        report['not_performed'].append(str(error)[:1000])
        if output is None:
            report['usage'] = {key: usage[key] for key in ('example_command', 'help_command')}
    return finish(report, started, output)


def finish(report, started, output=None):
    if report['result'] == 'passed':
        report['summary'] = ('Supplied scenarios passed; only their stated behaviour was checked.'
                             if report['behaviour_checked'] else
                             'Registration checked; behaviour was not checked. Supply --tests to check behaviour.')
    else:
        report['summary'] = ('Checks failed; inspect problems and the detailed report.'
                             if report['result'] == 'failed' else
                             'Checks incomplete; this is not a passing behaviour check.')
    report['duration_seconds'] = time.monotonic() - started
    if output is not None and output.exists():
        (output / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    return {'passed': 0, 'failed': 1, 'incomplete': 2}[report['result']]


if __name__ == '__main__':
    def cancelled(signum, frame):
        raise KeyboardInterrupt('Check cancelled by signal ' + str(signum))
    signal.signal(signal.SIGTERM, cancelled)
    sys.exit(main())
