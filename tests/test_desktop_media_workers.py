"""Actual Qt workers with generated local media; no provider calls."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from core import probe_media
from desktop_media_workers import MediaAnalyzeThread, ProxyThread

FFMPEG = shutil.which('ffmpeg')


@unittest.skipUnless(FFMPEG, 'FFmpeg required')
class MediaWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.temp = tempfile.TemporaryDirectory(prefix='lingjian-worker-media-')
        cls.root = Path(cls.temp.name)
        cls.source = cls.root / 'source.mp4'
        subprocess.run([FFMPEG, '-v', 'error', '-y', '-f', 'lavfi', '-i',
                        'testsrc2=s=320x180:r=24:d=0.5', '-c:v', 'libx264',
                        '-pix_fmt', 'yuv420p', str(cls.source)], capture_output=True, check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_worker(self, worker):
        worker.start()
        self.assertTrue(worker.wait(15000), 'Local fixture worker did not finish')
        self.app.processEvents()

    def test_bad_file_does_not_block_next_valid_file_and_thumbnail(self):
        missing = str(self.root / 'missing.mp4')
        worker = MediaAnalyzeThread([missing, str(self.source)], self.root / 'thumbs', FFMPEG)
        ready, failed = QSignalSpy(worker.item), QSignalSpy(worker.failed)
        self.run_worker(worker)
        self.assertEqual(ready.count(), 1)
        self.assertEqual(failed.count(), 1)
        self.assertEqual(failed.at(0)[0], missing)
        meta = ready.at(0)[0]
        self.assertEqual(meta['path'], str(self.source))
        self.assertGreater(meta['duration'], 0)
        self.assertTrue(Path(meta['thumbnail']).is_file())

    def test_cache_permission_failure_reports_every_file(self):
        worker = MediaAnalyzeThread(['a.mp4', 'b.mp4'], self.root / 'thumbs', FFMPEG)
        failed = QSignalSpy(worker.failed)
        with patch('desktop_media_workers.Path.mkdir', side_effect=PermissionError('cache denied')):
            self.run_worker(worker)
        self.assertEqual(failed.count(), 2)
        self.assertIn('cache denied', failed.at(1)[1])

    def test_missing_ffmpeg_reports_error_without_escaping_qthread(self):
        worker = MediaAnalyzeThread([str(self.source)], self.root / 'thumbs',
                                    str(self.root / 'not-ffmpeg'))
        failed = QSignalSpy(worker.failed)
        self.run_worker(worker)
        self.assertEqual(failed.count(), 1)

    def test_proxy_is_readable_and_preserves_original_source(self):
        before = self.source.read_bytes()
        output = self.root / 'proxies' / 'preview.proxy.mp4'
        worker = ProxyThread(str(self.source), str(output), FFMPEG)
        self.run_worker(worker)
        self.assertTrue(worker.job_result.ok, worker.job_result.message)
        self.assertIn('h264', probe_media(FFMPEG, str(output))['codec'])
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(list(output.parent.glob('*.part.mp4')), [])

    def test_failed_proxy_preserves_existing_cache_and_cleans_temporary(self):
        output = self.root / 'old.proxy.mp4'
        output.write_bytes(b'previous proxy')
        worker = ProxyThread(str(self.root / 'missing.mp4'), str(output), FFMPEG)
        self.run_worker(worker)
        self.assertEqual(worker.job_result.status, 'failed')
        self.assertEqual(output.read_bytes(), b'previous proxy')
        self.assertEqual(list(self.root.glob('*.part.mp4')), [])

    def test_proxy_folder_failure_returns_a_failure_result(self):
        worker = ProxyThread(str(self.source), str(self.root / 'proxy.mp4'), FFMPEG)
        with patch('desktop_media_workers.Path.mkdir', side_effect=PermissionError('folder denied')):
            self.run_worker(worker)
        self.assertEqual(worker.job_result.status, 'failed')
        self.assertIn('folder denied', worker.job_result.message)


if __name__ == '__main__':
    unittest.main()
