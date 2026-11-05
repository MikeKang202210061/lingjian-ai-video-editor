"""Exercise export ownership and visible states with a controllable Qt worker."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QObject, QSettings, QStandardPaths, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

from app import Main, STYLE
from core import Clip, Project
from desktop_render import RenderThread
from render_jobs import RenderResult


class FakeRenderThread(QObject):
    progress = Signal(int)
    finished = Signal()

    def __init__(self, command, duration, parent=None):
        super().__init__(parent)
        self.command = command
        self.duration = duration
        self.job_result = None
        self.running = False
        self.cancelled = False

    def start(self):
        self.running = True

    def isRunning(self):
        return self.running

    def cancel(self):
        self.cancelled = True

    def finish(self, result):
        self.running = False
        self.job_result = result
        self.finished.emit()


class DesktopExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = tempfile.TemporaryDirectory(prefix='lingjian-export-settings-')
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, cls.config.name)
        QStandardPaths.setTestModeEnabled(True)
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle('Fusion')
        cls.app.setStyleSheet(STYLE)

    @classmethod
    def tearDownClass(cls):
        cls.config.cleanup()

    def setUp(self):
        QSettings('LingJianAI', 'Editor').clear()
        self.temp = tempfile.TemporaryDirectory(prefix='lingjian-export-test-')
        self.root = Path(self.temp.name)
        self.output = str(self.root / 'export.mp4')
        self.window = Main()
        self.window.auto_timer.stop()
        self.window.session.watch_timer.stop()
        self.controller = self.window.export_controller
        self.window.project = Project(clips=[Clip(str(self.root / 'input.mp4'), 0, 2)])
        self.window.timeline.set_project(self.window.project)
        self.window.preflight = Mock(return_value={'warnings': []})
        self.window.alert = Mock()
        self.dialog = patch('desktop_export.QFileDialog.getSaveFileName', return_value=(self.output, ''))
        self.worker = patch('desktop_export.RenderThread', FakeRenderThread)
        self.builder = patch('desktop_export.build_render_command',
                             side_effect=lambda ff, project, path, *_: [ff, '-i', project.clips[0].path, path])
        self.dialog_mock = self.dialog.start()
        self.worker.start()
        self.builder_mock = self.builder.start()

    def tearDown(self):
        if self.controller.busy:
            self.controller.thread.finish(RenderResult('cancelled', '取消', self.output))
        self.builder.stop()
        self.worker.stop()
        self.dialog.stop()
        self.app.removeEventFilter(self.window.focus_guard)
        self.window.hide()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_repeated_export_keeps_worker_and_does_not_prompt_twice(self):
        self.assertTrue(self.window.export())
        original = self.window.render_thread
        self.assertFalse(self.window.export())
        self.assertIs(self.window.render_thread, original)
        self.assertEqual(self.dialog_mock.call_count, 1)
        self.assertFalse(self.window.export_top.isEnabled())
        self.assertFalse(self.window.export_button.isEnabled())
        self.assertEqual(self.window.export_button.text(), '导出中…')
        self.assertFalse(self.controller.cancel_button.isHidden())
        self.assertEqual(self.window.inspector.currentIndex(), 6)

    def test_cancel_holds_transaction_until_finished_then_allows_retry(self):
        self.window.export()
        thread = self.controller.thread
        self.controller.cancel_button.click()
        self.assertTrue(thread.cancelled)
        self.assertFalse(self.controller.cancel_button.isEnabled())
        self.assertFalse(self.window.export())
        thread.finish(RenderResult('cancelled', '导出已取消', self.output))
        self.assertFalse(self.controller.busy)
        self.assertIsNone(self.window.render_thread)
        self.assertTrue(self.window.export_top.isEnabled())
        self.assertEqual(self.window.export_button.text(), '导出 MP4')
        self.assertTrue(self.controller.cancel_button.isHidden())
        self.assertIn('已取消', self.window.render_status.text())
        self.window.alert.assert_not_called()
        self.assertTrue(self.window.export())

    def test_success_uses_worker_validation_and_releases_controls(self):
        self.window.export()
        self.controller.thread.finish(RenderResult('completed', 'ok', self.output,
                                                  {'width': 320, 'height': 180, 'duration': 2.0}))
        self.assertEqual(self.window.render_progress.value(), 100)
        self.assertTrue(self.window.export_button.isEnabled())
        self.assertIn('已校验', self.window.render_status.text())
        self.assertIn('320×180', self.window.alert.call_args.args[0])

    def test_failure_releases_controls_and_reports_diagnostic(self):
        self.window.export()
        self.controller.thread.finish(RenderResult('failed', 'encoder broke', self.output))
        self.assertEqual(self.window.render_progress.value(), 0)
        self.assertTrue(self.window.export_button.isEnabled())
        self.assertTrue(self.controller.cancel_button.isHidden())
        self.window.alert.assert_called_once_with('导出失败：\nencoder broke', True)

    def test_cancel_file_dialog_does_not_start_or_disable_controls(self):
        self.dialog_mock.return_value = ('', '')
        self.assertFalse(self.window.export())
        self.assertFalse(self.controller.busy)
        self.assertTrue(self.window.export_button.isEnabled())
        self.builder_mock.assert_not_called()

    def test_input_collision_rejected_before_worker_start(self):
        self.dialog_mock.return_value = (self.window.project.clips[0].path, '')
        self.assertFalse(self.window.export())
        self.assertFalse(self.controller.busy)
        self.assertIn('覆盖输入素材', self.window.alert.call_args.args[0])

    def test_start_failure_restores_controls(self):
        with patch.object(FakeRenderThread, 'start', side_effect=RuntimeError('cannot start')):
            self.assertFalse(self.window.export())
        self.assertFalse(self.controller.busy)
        self.assertIsNone(self.window.render_thread)
        self.assertTrue(self.window.export_button.isEnabled())
        self.assertTrue(self.controller.cancel_button.isHidden())

    def test_export_uses_snapshot_and_honors_timeline_mute(self):
        self.window.timeline.audio_muted = True
        self.window.export()
        snapshot = self.builder_mock.call_args.args[1]
        self.assertIsNot(snapshot, self.window.project)
        self.assertEqual(snapshot.clips[0].volume, 0)
        self.assertEqual(self.window.project.clips[0].volume, 1)
        self.window.project.clips[0].end = 5
        self.assertEqual(snapshot.duration, 2)

    def test_running_export_blocks_close_and_project_switch(self):
        self.window.export()
        self.assertIn('render_thread', self.window.session.running_tasks())
        event = QCloseEvent()
        self.window.closeEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertFalse(self.window.session.can_replace_document())

    def test_cancel_control_is_reachable_in_small_scrollable_inspector(self):
        self.window.resize(1120, 680)
        self.window.show()
        self.window.export()
        self.app.processEvents()
        scroll = self.window.inspector.widget(6)
        scroll.ensureWidgetVisible(self.controller.cancel_button)
        self.app.processEvents()
        button = self.controller.cancel_button
        position = button.mapTo(scroll.viewport(), button.rect().center())
        self.assertTrue(button.isVisible())
        self.assertTrue(scroll.viewport().rect().contains(position))

    def test_qthread_reports_unexpected_failure_to_preview_clients(self):
        worker = RenderThread(['ffmpeg', self.output], 2)
        received = []
        worker.done.connect(lambda ok, message: received.append((ok, message)))
        with patch('desktop_render.render_atomic', side_effect=RuntimeError('unexpected')):
            worker.start()
            self.assertTrue(worker.wait(5000))
        self.app.processEvents()
        self.assertEqual(received, [(False, 'unexpected')])
        self.assertEqual(worker.job_result.status, 'failed')


if __name__ == '__main__':
    unittest.main()
