"""Generated media through real import, project reopen and GUI export workers."""
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QSettings, QStandardPaths, QUrl
from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from app import Main, STYLE
from core import Project, validate_rendered_mp4
from project_session import RecoveryStore

FFMPEG = shutil.which('ffmpeg')


@unittest.skipUnless(FFMPEG, 'FFmpeg required')
class DesktopWorkflowTests(unittest.TestCase):
    def test_real_import_save_reopen_and_export_preserve_source(self):
        with tempfile.TemporaryDirectory(prefix='lingjian-desktop-workflow-') as folder:
            root = Path(folder)
            source = root / 'source.mp4'
            subprocess.run([FFMPEG, '-v', 'error', '-y', '-f', 'lavfi', '-i',
                            'testsrc2=s=320x180:r=24:d=1', '-f', 'lavfi', '-i',
                            'sine=frequency=440:duration=1', '-c:v', 'libx264', '-pix_fmt',
                            'yuv420p', '-c:a', 'aac', '-shortest', str(source)],
                           capture_output=True, check=True)
            source_bytes = source.read_bytes()
            QSettings.setDefaultFormat(QSettings.IniFormat)
            QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(root / 'settings'))
            QStandardPaths.setTestModeEnabled(True)
            app = QApplication.instance() or QApplication([])
            app.setStyle('Fusion')
            app.setStyleSheet(STYLE)
            QSettings('LingJianAI', 'Editor').clear()
            window = Main()
            window.auto_timer.stop()
            window.session.watch_timer.stop()
            window.cache = root / 'cache'
            window.session.recovery = RecoveryStore(root / 'recovery')
            window.alert = Mock()

            def wait_until(predicate, timeout=30):
                deadline = time.monotonic() + timeout
                while not predicate() and time.monotonic() < deadline:
                    QTest.qWait(20)
                self.assertTrue(predicate(), 'Desktop workflow did not finish in time')

            try:
                window.resize(1120, 680)
                window.show()
                window.start_import([str(source)])
                wait_until(lambda: not window.import_controller.busy)
                self.assertEqual(window.media.count(), 1)
                self.assertEqual(window.import_controller.issue_count, 0)
                window.add_media_path(str(source), 0)
                window.title.setText('完整流程验收')
                project_path = root / 'project.ljproject'
                self.assertTrue(window.session.save_to(project_path))
                self.assertEqual(Project.load(project_path).title, '完整流程验收')
                self.assertTrue(window.session.open(project_path))
                wait_until(lambda: not window.import_controller.busy)
                self.assertEqual(len(window.project.clips), 1)
                self.assertFalse(window.isWindowModified())
                # Small test-only preset keeps this integration check quick.
                window.preset.addItem('Test 320×180', (320, 180, 'standard'))
                window.preset.setCurrentIndex(window.preset.count() - 1)
                output = root / 'output.mp4'
                output.write_bytes(b'previous output')
                with patch('desktop_export.QFileDialog.getSaveFileName', return_value=(str(output), '')), \
                        patch('desktop_export.QMessageBox.question', return_value=QMessageBox.Yes):
                    self.assertTrue(window.export())
                wait_until(lambda: not window.export_controller.busy)
                self.assertEqual(window.render_progress.value(), 100)
                meta = validate_rendered_mp4(FFMPEG, str(output))
                self.assertEqual((meta['width'], meta['height']), (320, 180))
                self.assertTrue(meta['has_audio'])
                self.assertEqual(source.read_bytes(), source_bytes)
                self.assertEqual(list(root.glob('*.part.mp4')), [])
                event = QCloseEvent()
                window.closeEvent(event)
                self.assertTrue(event.isAccepted())
            finally:
                # Never destroy a live QThread if an earlier assertion fails.
                if window.export_controller.busy:
                    window.export_controller.cancel()
                for name in ('analyze_thread', 'proxy_thread', 'render_thread'):
                    worker = getattr(window, name)
                    if worker is not None:
                        worker.wait(30000)
                app.processEvents()
                window.player.stop()
                window.source_player.stop()
                window.player.setSource(QUrl())
                window.source_player.setSource(QUrl())
                app.removeEventFilter(window.focus_guard)
                window.hide()
                window.deleteLater()
                app.processEvents()


if __name__ == '__main__':
    unittest.main()
