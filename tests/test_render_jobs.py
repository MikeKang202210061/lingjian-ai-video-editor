"""Real subprocess lifecycle tests without Qt, FFmpeg or model services."""
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from render_jobs import ensure_output_is_safe, render_atomic


class RenderJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lingjian-render-')
        self.root = Path(self.temp.name)
        self.output = self.root / '旧成片.mp4'
        self.output.write_bytes(b'previous output')

    def tearDown(self):
        self.temp.cleanup()

    def command(self, script):
        return [sys.executable, '-c', script, str(self.output)]

    def render(self, script, **kwargs):
        return render_atomic(self.command(script), str(self.output), 2,
                             validator=kwargs.pop('validator', lambda _: {}), **kwargs)

    def assert_preserved(self):
        self.assertEqual(self.output.read_bytes(), b'previous output')
        self.assertEqual(list(self.root.glob('*.part.mp4')), [])

    def test_success_publishes_only_after_validation_and_then_reaches_100(self):
        progress = []

        def validate(path):
            self.assertEqual(self.output.read_bytes(), b'previous output')
            self.assertEqual(Path(path).read_bytes(), b'new video')
            self.assertNotIn(100, progress)
            return {'duration': 2}

        result = self.render("import pathlib,sys; pathlib.Path(sys.argv[-1]).write_bytes(b'new video'); "
                             "print('time=00:00:01.00',file=sys.stderr)",
                             validator=validate, progress=progress.append)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.metadata, {'duration': 2})
        self.assertEqual(progress, [50, 100])
        self.assertEqual(self.output.read_bytes(), b'new video')
        self.assertEqual(list(self.root.glob('*.part.mp4')), [])

    def test_process_failure_keeps_previous_output_and_returns_diagnostic(self):
        result = self.render("import pathlib,sys; pathlib.Path(sys.argv[-1]).write_bytes(b'partial'); "
                             "print('encoding failed',file=sys.stderr);sys.exit(3)")
        self.assertEqual(result.status, 'failed')
        self.assertIn('encoding failed', result.message)
        self.assert_preserved()

    def test_validation_failure_does_not_publish(self):
        def invalid(_):
            raise ValueError('invalid video')
        result = self.render("import pathlib,sys;pathlib.Path(sys.argv[-1]).write_bytes(b'bad')",
                             validator=invalid)
        self.assertEqual(result.status, 'failed')
        self.assertIn('invalid video', result.message)
        self.assert_preserved()

    def test_pre_cancel_does_not_spawn(self):
        cancel = threading.Event()
        cancel.set()
        with patch('render_jobs.subprocess.Popen', side_effect=AssertionError('must not spawn')):
            self.assertEqual(self.render('', cancel=cancel).status, 'cancelled')
        self.assert_preserved()

    def test_silent_running_process_can_be_cancelled(self):
        cancel = threading.Event()
        timer = threading.Timer(.2, cancel.set)
        timer.start()
        start = time.monotonic()
        try:
            result = self.render('import time;time.sleep(30)', cancel=cancel)
        finally:
            timer.cancel()
            timer.join()
        self.assertEqual(result.status, 'cancelled', result.message)
        self.assertLess(time.monotonic() - start, 6)
        self.assert_preserved()

    def test_cancel_during_validation_prevents_publication(self):
        cancel = threading.Event()
        def validate(_):
            cancel.set()
            return {}
        self.assertEqual(self.render('', cancel=cancel, validator=validate).status, 'cancelled')
        self.assert_preserved()

    def test_missing_executable_is_a_result_and_cleans_temporary_file(self):
        result = render_atomic([str(self.root / 'missing'), str(self.output)], str(self.output), 2,
                               validator=lambda _: {})
        self.assertEqual(result.status, 'failed')
        self.assert_preserved()

    def test_cannot_overwrite_input_or_hardlink_or_symlink(self):
        source = self.root / 'input.mp4'
        source.write_bytes(b'source')
        targets = [source, self.root / 'hardlink.mp4', self.root / 'symlink.mp4']
        os.link(source, targets[1])
        try:
            targets[2].symlink_to(source)
        except OSError:
            targets.pop()
        for target in targets:
            with self.subTest(target=target):
                with self.assertRaisesRegex(ValueError, '覆盖输入素材'):
                    ensure_output_is_safe(['ffmpeg', '-i', str(source), str(target)], str(target))
        self.assertEqual(source.read_bytes(), b'source')

    def test_large_stderr_and_broken_display_callback_do_not_block_process(self):
        def broken(_):
            raise RuntimeError('display gone')
        result = self.render("import sys;sys.stderr.write(('time=00:00:01.00 '+ 'x'*2000+chr(10))*1000)",
                             progress=broken)
        self.assertTrue(result.ok, result.message)

    def test_publication_failure_preserves_previous_output(self):
        with patch('render_jobs.os.replace', side_effect=PermissionError('output locked')):
            result = self.render('')
        self.assertEqual(result.status, 'failed')
        self.assertIn('output locked', result.message)
        self.assert_preserved()


if __name__ == '__main__':
    unittest.main()
