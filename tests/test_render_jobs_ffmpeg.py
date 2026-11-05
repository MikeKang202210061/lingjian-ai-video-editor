"""Small real H.264/AAC jobs; never call a cloud API."""
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from core import Clip, Project, build_render_command, probe_media, validate_rendered_mp4
from render_jobs import render_atomic

FFMPEG = shutil.which('ffmpeg')


@unittest.skipUnless(FFMPEG, 'FFmpeg required')
class FFmpegRenderJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='lingjian-ffmpeg-render-')
        cls.root = Path(cls.temp.name)
        cls.source = cls.root / 'source.mp4'
        subprocess.run([FFMPEG, '-v', 'error', '-y', '-f', 'lavfi', '-i',
                        'testsrc2=s=320x180:r=24:d=1', '-f', 'lavfi', '-i',
                        'sine=frequency=440:duration=1', '-c:v', 'libx264', '-pix_fmt',
                        'yuv420p', '-c:a', 'aac', '-shortest', str(cls.source)],
                       capture_output=True, check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.output = self.root / (self._testMethodName + '.mp4')
        self.output.write_bytes(b'previous output')

    def run_render(self, command, cancel=None):
        return render_atomic(command, str(self.output), 1, cancel=cancel,
                             validator=lambda path: validate_rendered_mp4(FFMPEG, path))

    def assert_preserved(self):
        self.assertEqual(self.output.read_bytes(), b'previous output')
        self.assertEqual(list(self.root.glob('*.part.mp4')), [])

    def test_editor_command_produces_validated_video(self):
        project = Project(clips=[Clip(str(self.source), 0, 1)])
        command = build_render_command(FFMPEG, project, str(self.output), 320, 180, 'standard')
        result = self.run_render(command)
        self.assertTrue(result.ok, result.message)
        self.assertEqual((result.metadata['width'], result.metadata['height']), (320, 180))
        self.assertGreater(result.metadata['duration'], .8)
        self.assertTrue(result.metadata['has_audio'])
        self.assertGreater(self.output.stat().st_size, 4096)
        self.assertEqual(list(self.root.glob('*.part.mp4')), [])

    def test_invalid_media_keeps_previous_output(self):
        result = self.run_render([FFMPEG, '-y', '-i', str(self.root / 'missing.mp4'), str(self.output)])
        self.assertEqual(result.status, 'failed')
        self.assert_preserved()

    def test_short_effect_preview_can_be_smaller_than_export_size_threshold(self):
        command = [FFMPEG, '-v', 'error', '-y', '-f', 'lavfi', '-i',
                   'color=c=black:s=320x180:r=24:d=0.2', '-c:v', 'libx264',
                   '-pix_fmt', 'yuv420p', str(self.output)]
        result = render_atomic(command, str(self.output), .2,
                               validator=lambda path: probe_media(FFMPEG, path))
        self.assertTrue(result.ok, result.message)
        self.assertGreater(self.output.stat().st_size, 0)
        self.assertLess(self.output.stat().st_size, 4096)
        self.assertIn('h264', result.metadata['codec'])

    def test_running_ffmpeg_can_be_cancelled_without_replacing_output(self):
        cancel = threading.Event()
        timer = threading.Timer(.4, cancel.set)
        timer.start()
        try:
            result = self.run_render([FFMPEG, '-y', '-re', '-f', 'lavfi', '-i',
                                      'testsrc2=s=320x180:r=24:d=20', '-c:v', 'libx264',
                                      '-pix_fmt', 'yuv420p', str(self.output)], cancel)
        finally:
            timer.cancel()
            timer.join()
        self.assertEqual(result.status, 'cancelled', result.message)
        self.assert_preserved()


if __name__ == '__main__':
    unittest.main()
