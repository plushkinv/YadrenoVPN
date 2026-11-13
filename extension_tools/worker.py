"""Restricted coordinator and fresh per-case unittest processes."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest

from .guard import verify


def write(name, data):
    Path('/work/files', name).write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def load_tests(filename, index):
    name = f'_extension_check_{index}'
    spec = importlib.util.spec_from_file_location(name, Path('/project') / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return unittest.defaultTestLoader.loadTestsFromModule(module)


def flatten(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from flatten(test)
        else:
            yield test


def counts(report):
    report['executed'] = sum(case['executed'] for case in report['cases'])
    report['successful'] = sum(case['successful'] for case in report['cases'])
    report['unsuccessful'] = max(0, report['executed'] - report['successful'])


def own_descendants():
    """Adopt orphaned scenario processes so one case cannot outlive its fixture."""
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), 'Cannot contain per-case descendants')


def stop_descendants():
    deadline = time.monotonic() + 3
    while True:
        children = Path(f'/proc/self/task/{os.getpid()}/children').read_text().split()
        if not children:
            return
        for pid in children:
            try:
                os.kill(int(pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
        while True:
            try:
                if os.waitpid(-1, os.WNOHANG)[0] == 0:
                    break
            except ChildProcessError:
                break
        if time.monotonic() > deadline:
            raise RuntimeError('Previous case descendants did not stop')
        time.sleep(.01)


class Result(unittest.TextTestResult):
    def __init__(self, *args):
        super().__init__(*args)
        self.problems = []
        self.successful = 0

    def problem(self, test, error, *, assertion=False):
        from .testing import EnvironmentUnavailable, RegistrationFailed, diagnostic
        value = error[1]
        fixture = getattr(test, 'fixture', None)
        stage = fixture.stage if fixture else 'scenario'
        setup_error = stage in ('database', 'fixtures', 'http_startup')
        incomplete = isinstance(value, (EnvironmentUnavailable, ImportError)) or setup_error
        source = ('runner' if setup_error else 'environment' if incomplete else 'extension' if isinstance(value, RegistrationFailed)
                  else 'scenario' if assertion or stage in ('scenario', 'prepare_extension') else 'unknown')
        item = diagnostic(value, source=source, stage=stage)
        item['result'] = 'incomplete' if incomplete else 'failed'
        self.problems.append(item)

    def addError(self, test, error):
        self.problem(test, error)
        super().addError(test, error)

    def addFailure(self, test, error):
        self.problem(test, error, assertion=True)
        super().addFailure(test, error)

    def addSubTest(self, test, subtest, error):
        if error is not None:
            self.problem(test, error, assertion=isinstance(error[1], AssertionError))
        super().addSubTest(test, subtest, error)

    def addSuccess(self, test):
        self.successful += 1
        super().addSuccess(test)


def run_case(inputs, index, test_id):
    from .testing import ExtensionTestCase, Fixture, EnvironmentUnavailable, diagnostic
    started = time.monotonic()
    fixture = None
    result = None
    if index == -1:
        class Registration(ExtensionTestCase):
            async def runTest(self):
                pass
        test = Registration()
    else:
        test = next(item for item in flatten(load_tests(inputs['tests'][index], index)) if item.id() == test_id)
        if not isinstance(test, ExtensionTestCase):
            # Plain unittest is supported for code assertions. HTTP helpers belong
            # to ExtensionTestCase and run in its owned async loop.
            fixture = Fixture()
            test.fixture = fixture
            async def prepare():
                try:
                    await fixture.prepare()
                    await fixture.load()
                finally:
                    await fixture.close()
            try:
                asyncio.run(prepare())
            except BaseException:
                result = unittest.TextTestRunner(verbosity=2, resultclass=Result)._makeResult()
                result.addError(test, sys.exc_info())
            else:
                original_call = test._callTestMethod
                def tracked(method):
                    return fixture.run_method(original_call, method)
                test._callTestMethod = tracked
    if result is None:
        result = unittest.TextTestRunner(verbosity=2, resultclass=Result).run(test)
    fixture = getattr(test, 'fixture', fixture)
    evidence = fixture.evidence if fixture else {}
    incomplete = (bool(result.skipped or result.expectedFailures or result.unexpectedSuccesses)
                  or any(p['result'] == 'incomplete' for p in result.problems)
                  or bool(evidence.get('environment_gaps')))
    outcome = 'incomplete' if incomplete else 'failed' if not result.wasSuccessful() else 'passed'
    for gap in evidence.get('environment_gaps', []):
        problem = diagnostic(EnvironmentUnavailable(gap['reason'] + ': ' + str(gap['host']) + ':' + str(gap['port'])),
                             source='environment', stage='network')
        problem['result'] = 'incomplete'
        result.problems.append(problem)
    if fixture and (fixture.state / 'synthetic.db').exists():
        import sqlite3
        name = 'synthetic-' + str(os.getpid()) + '.db'
        with sqlite3.connect(fixture.state / 'synthetic.db') as source, sqlite3.connect('/work/files/' + name) as target:
            source.backup(target)
        evidence['database'] = name
    return {'id': test_id, 'result': outcome, 'duration_seconds': time.monotonic() - started,
            'executed': result.testsRun if index >= 0 and evidence.get('scenario_started') else 0,
            'successful': result.successful if index >= 0 else 0,
            'problems': result.problems, 'skipped': len(result.skipped), 'evidence': evidence}


def main():
    verify()  # Before importing any test or extension.
    from .testing import diagnostic
    inputs = json.loads(Path('/project/check-input.json').read_text())
    if len(sys.argv) > 1:
        index, test_id, output = int(sys.argv[1]), sys.argv[2], sys.argv[3]
        try:
            report = run_case(inputs, index, test_id)
        except BaseException as error:
            report = {'id': test_id, 'result': 'incomplete', 'executed': 0, 'successful': 0,
                      'problems': [diagnostic(error, source='unknown', stage='case_setup')]}
        write(output, report)
        return
    started = time.monotonic()
    report = {'result': 'incomplete', 'found': 0, 'executed': 0, 'successful': 0,
              'unsuccessful': 0, 'not_performed': [], 'cases': [], 'problems': [],
              'python': sys.version, 'behaviour_checked': False}
    write('report.json', report)
    stage = 'case_isolation'
    try:
        own_descendants()
        stage = 'discovery'
        cases = []
        for index, filename in enumerate(inputs['tests']):
            cases += [(index, test.id()) for test in flatten(load_tests(filename, index))]
        stage = 'case_cleanup'
        stop_descendants()
        report['found'] = len(cases)
        if inputs['tests'] and not cases:
            report['not_performed'].append('No tests found in the supplied scenario files')
            return
        if not inputs['tests']:
            cases = [(-1, 'registration_only')]
            report['not_performed'].append('Operation behaviour: no --tests supplied (registration only)')
        for number, (index, test_id) in enumerate(cases):
            stage = 'case_execution'
            filename = f'case-{number}.json'
            write('report.json', report)
            result = subprocess.run([sys.executable, '-B', '-S', '-m', 'extension_tools.worker',
                                     str(index), test_id, filename], stdin=subprocess.DEVNULL)
            stage = 'case_cleanup'
            stop_descendants()
            if result.returncode or not Path('/work/files', filename).is_file():
                report['cases'].append({'id': test_id, 'result': 'incomplete',
                    'executed': 0, 'successful': 0, 'exit_code': result.returncode})
            else:
                report['cases'].append(json.loads(Path('/work/files', filename).read_text()))
            counts(report)
            write('report.json', report)
        outcomes = [case['result'] for case in report['cases']]
        report['result'] = 'incomplete' if 'incomplete' in outcomes else 'failed' if 'failed' in outcomes else 'passed'
        report['behaviour_checked'] = bool(inputs['tests']) and report['executed'] == report['found'] and 'incomplete' not in outcomes
        for case in report['cases']:
            if case['result'] == 'incomplete' or inputs['tests'] and not case['executed']:
                report['not_performed'].append(case['id'])
    except BaseException as error:
        report['problems'].append(diagnostic(error, source='scenario' if stage == 'discovery' else 'runner', stage=stage))
        report['not_performed'].append('Test discovery or execution did not finish')
    finally:
        report['duration_seconds'] = time.monotonic() - started
        write('report.json', report)


if __name__ == '__main__':
    main()
