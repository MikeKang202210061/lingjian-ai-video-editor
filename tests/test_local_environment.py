"""Local setup must fail without modifying unrelated directories or global pip."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts.local_environment import check_python, collect_report, environment_python, setup


class LocalEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lingjian-local-tests-')
        self.root = Path(self.temp.name)
        (self.root / 'app.py').touch()
        (self.root / 'requirements.txt').write_text('PySide6>=6.7,<7\n', encoding='utf-8')
        (self.root / 'skills').mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_unsupported_python_or_architecture_rejected(self):
        for version, bits in [((3, 10), 64), ((3, 14), 64), ((3, 12), 32)]:
            with self.subTest(version=version, bits=bits), self.assertRaises(ValueError):
                check_python(version, bits)
        check_python((3, 12), 64)

    def test_missing_source_never_runs_an_installer(self):
        (self.root / 'app.py').unlink()
        runner = Mock()
        with self.assertRaises(ValueError):
            setup(self.root, runner=runner, system='Windows')
        runner.assert_not_called()

    def test_existing_non_environment_is_preserved(self):
        folder = self.root / '.venv'
        folder.mkdir()
        sentinel = folder / 'user-file.txt'
        sentinel.write_text('preserve', encoding='utf-8')
        runner = Mock()
        with self.assertRaises(ValueError):
            setup(self.root, runner=runner, system='Windows')
        runner.assert_not_called()
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'preserve')

    def test_symlink_environment_does_not_escape_project(self):
        outside = self.root / 'outside'
        outside.mkdir()
        try:
            (self.root / '.venv').symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest('Symlink creation unavailable: ' + str(exc))
        with self.assertRaises(ValueError):
            environment_python(self.root)

    def existing_environment(self):
        python = self.root / '.venv/Scripts/python.exe'
        python.parent.mkdir(parents=True)
        python.touch()
        (python.parent.parent / 'pyvenv.cfg').write_text('home = test\n', encoding='utf-8')
        return python

    def test_interpreter_check_failure_prevents_pip_changes(self):
        self.existing_environment()
        runner = Mock(side_effect=subprocess.CalledProcessError(1, ['interpreter-check']))
        with self.assertRaises(subprocess.CalledProcessError):
            setup(self.root, runner=runner, system='Windows')
        self.assertEqual(runner.call_count, 1)
        self.assertNotIn('pip', runner.call_args.args[0])

    def test_only_project_interpreter_receives_install_and_failure_stops(self):
        python = self.existing_environment()
        runner = Mock(side_effect=[None, subprocess.CalledProcessError(1, ['pip'])])
        with self.assertRaises(subprocess.CalledProcessError):
            setup(self.root, runner=runner, system='Windows')
        self.assertEqual(runner.call_count, 2)
        for call in runner.call_args_list:
            self.assertEqual(call.args[0][0], str(python))
        self.assertEqual(runner.call_args_list[1].args[0][1:5], ['-m', 'pip', 'install', '-r'])

    def test_probe_is_opt_in_and_reads_actual_product_name(self):
        probe_result = {'status': 'connected', 'product': 'DaVinci Resolve', 'version': 'test', 'ready': False}
        with patch('scripts.local_environment.platform.system', return_value='Windows'), \
                patch('scripts.local_environment.subprocess.run', return_value=SimpleNamespace(stdout='QT_READY\n')), \
                patch('resolve_client.run_bridge', return_value=probe_result) as bridge:
            report = collect_report(self.root)
            bridge.assert_not_called()
            self.assertEqual(report['resolve']['status'], 'not_run')
            report = collect_report(self.root, probe_resolve=True)
        self.assertEqual(report['resolve']['product'], 'DaVinci Resolve')
        self.assertEqual(bridge.call_args.args[2], {'version': 1, 'operation': 'probe'})
        self.assertFalse(report['ok'])  # Missing resources are never reported ready.

    def test_native_dependency_failure_is_reported(self):
        with patch('scripts.local_environment.subprocess.run', side_effect=subprocess.TimeoutExpired(['qt'], 20)):
            report = collect_report(self.root)
        self.assertFalse(report['qt_startup'])
        self.assertTrue(any('Qt/native' in x for x in report['issues']))


if __name__ == '__main__':
    unittest.main()
