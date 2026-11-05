"""Resolve controls use a fake worker; no local Resolve session is contacted."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QObject, QSettings, QStandardPaths, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from app import Main, STYLE
from core import Clip, Project


class FakeResolveThread(QObject):
    finished = Signal()

    def __init__(self, python, sdk, request, ffmpeg, parent=None):
        super().__init__(parent)
        self.python, self.sdk, self.request = python, sdk, request
        self.job_result = None
        self.running = False
        self.cancel_requested = False

    def start(self):
        self.running = True

    def isRunning(self):
        return self.running

    def cancel(self):
        self.cancel_requested = True

    def finish(self, result):
        self.job_result = result
        self.running = False
        self.finished.emit()


class DesktopResolveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = tempfile.TemporaryDirectory(prefix='lingjian-resolve-settings-')
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, cls.settings.name)
        QStandardPaths.setTestModeEnabled(True)
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle('Fusion')
        cls.app.setStyleSheet(STYLE)

    @classmethod
    def tearDownClass(cls):
        cls.settings.cleanup()

    def setUp(self):
        QSettings('LingJianAI', 'Editor').clear()
        self.temp = tempfile.TemporaryDirectory(prefix='lingjian-resolve-ui-')
        self.root = Path(self.temp.name)
        source = self.root / 'source.mp4'
        source.write_bytes(b'test source')
        self.window = Main()
        self.window.auto_timer.stop()
        self.window.session.watch_timer.stop()
        self.window.alert = Mock()
        self.window.preflight = Mock(return_value={'warnings': []})
        self.window.project = Project(clips=[Clip(str(source), 0, 2)])
        self.controller = self.window.resolve_controller
        self.controller.python.setText(sys.executable)
        self.patch = patch('desktop_resolve.ResolveThread', FakeResolveThread)
        self.patch.start()

    def tearDown(self):
        if self.controller.busy:
            self.controller.thread.finish({'status': 'cancelled', 'message': 'test cancelled'})
        self.patch.stop()
        self.app.removeEventFilter(self.window.focus_guard)
        self.window.hide()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def connected(self, ready=True):
        self.controller.probe()
        self.controller.thread.finish({'status': 'connected', 'product': 'Test SDK',
                                       'version': 'fake', 'ready': ready})

    def start_render(self, choice=QMessageBox.Yes):
        output = str(self.root / 'output.mp4')
        with patch('desktop_resolve.QFileDialog.getSaveFileName', return_value=(output, '')), \
                patch('desktop_resolve.QMessageBox.question', return_value=choice) as question:
            result = self.controller.render()
        return result, question

    def test_no_automatic_sdk_contact_on_window_creation(self):
        self.assertFalse(self.controller.busy)
        self.assertFalse(self.controller.ready)
        self.assertFalse(self.controller.toggle.isChecked())

    def test_probe_retains_worker_and_reports_availability(self):
        self.controller.probe()
        worker = self.controller.thread
        self.controller.probe()
        self.assertIs(self.controller.thread, worker)
        self.assertFalse(self.controller.probe_button.isEnabled())
        self.assertTrue(all(not button.isEnabled() for button in self.controller.browse_buttons))
        worker.finish({'status': 'connected', 'product': 'Test SDK', 'version': 'fake', 'ready': True})
        self.assertTrue(self.controller.ready)
        self.assertFalse(self.controller.busy)
        self.assertIn('已连接', self.controller.status.text())

    def test_existing_open_project_is_not_ready_for_execution(self):
        self.connected(False)
        self.assertFalse(self.controller.render())
        self.assertFalse(self.controller.busy)

    def test_configuration_change_requires_new_probe(self):
        self.connected()
        self.controller.sdk.setText(str(self.root / 'other-sdk'))
        self.assertFalse(self.controller.render())
        self.assertIn('先检测', self.controller.status.text())

    def test_probe_uses_captured_configuration(self):
        self.controller.probe()
        worker = self.controller.thread
        captured = (worker.python, worker.sdk)
        self.controller.sdk.setText('changed programmatically')
        worker.finish({'status': 'connected', 'ready': True})
        self.assertEqual(self.controller.checked_configuration, captured)
        self.assertFalse(self.controller.render())

    def test_render_requires_confirmation_of_basic_only_transfer(self):
        self.connected()
        self.window.project.clips[0].caption = 'unsupported caption'
        result, question = self.start_render(QMessageBox.Cancel)
        self.assertFalse(result)
        self.assertFalse(self.controller.busy)
        self.assertIn('字幕及标题', question.call_args.args[2])

    def test_confirmed_render_sends_restricted_snapshot(self):
        self.connected()
        result, _ = self.start_render()
        self.assertTrue(result)
        request = self.controller.thread.request
        self.assertEqual(request['operation'], 'render_basic')
        self.assertEqual(set(request['clips'][0]), {'path', 'start', 'end'})
        self.window.project.clips[0].end = 5
        self.assertEqual(request['clips'][0]['end'], 2)
        self.assertFalse(self.window.export())  # Cross-backend duplicate render is blocked.

    def test_local_export_blocks_resolve_render(self):
        self.connected()
        with patch.object(type(self.window.export_controller), 'busy', new_callable=lambda: property(lambda _: True)):
            self.assertFalse(self.controller.render())
        self.assertIn('本地导出', self.controller.status.text())

    def test_cancel_waits_for_finished_and_releases_controls(self):
        self.connected()
        self.start_render()
        worker = self.controller.thread
        self.controller.cancel()
        self.assertTrue(worker.cancel_requested)
        self.assertTrue(self.controller.busy)
        self.assertFalse(self.controller.cancel_button.isEnabled())
        worker.finish({'status': 'cancelled', 'message': '已停止'})
        self.assertFalse(self.controller.busy)
        self.assertTrue(self.controller.probe_button.isEnabled())
        self.assertFalse(self.controller.ready)

    def test_pending_finished_delivery_blocks_close(self):
        self.controller.probe()
        self.controller.thread.running = False
        event = QCloseEvent()
        self.window.closeEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertIn('resolve_backend', self.window.session.running_tasks())

    def test_uncertain_result_does_not_allow_blind_retry(self):
        self.connected()
        self.start_render()
        self.controller.thread.finish({'status': 'uncertain', 'message': 'SDK timed out',
                                       'job_dir': str(self.root / 'job')})
        self.assertIn('不要直接重试', self.controller.status.text())
        self.assertIn(str(self.root / 'job'), self.controller.status.text())
        self.assertFalse(self.controller.render())

    def test_worker_start_failure_restores_controls(self):
        with patch.object(FakeResolveThread, 'start', side_effect=RuntimeError('cannot start')):
            self.controller.probe()
        self.assertFalse(self.controller.busy)
        self.assertIsNone(self.window.resolve_thread)
        self.assertTrue(self.controller.probe_button.isEnabled())

    def test_completed_output_can_open_in_an_editor_owned_preview(self):
        self.connected()
        self.start_render()
        output = self.root / 'output.mp4'
        output.write_bytes(b'test-only preview fixture')
        self.controller.thread.finish({'status': 'completed', 'output': str(output)})
        self.assertFalse(self.controller.preview_button.isHidden())
        with patch('desktop_resolve.QMediaPlayer') as player:
            self.controller.preview()
            self.assertTrue(self.controller.preview_dialog.isVisible())
            self.assertIs(self.controller.preview_dialog.parent(), self.window)
            self.controller.preview_dialog.reject()
            self.assertIsNone(self.controller.preview_dialog)
        player.return_value.setSource.assert_called()

    def test_panel_remains_reachable_in_small_window(self):
        self.window.resize(1120, 680)
        self.window.show()
        self.controller.toggle.setChecked(True)
        self.window.inspector_selector.setCurrentIndex(6)
        self.app.processEvents()
        scroll = self.window.inspector.widget(6)
        scroll.ensureWidgetVisible(self.controller.render_button)
        self.app.processEvents()
        button = self.controller.render_button
        self.assertTrue(button.isVisible())
        self.assertTrue(scroll.viewport().rect().contains(button.mapTo(scroll.viewport(), button.rect().center())))
        self.assertLessEqual(scroll.widget().width(), scroll.viewport().width())


if __name__ == '__main__':
    unittest.main()
