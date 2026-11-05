"""Import queue ownership and media-library feedback, without cloud services."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QObject, QSettings, QStandardPaths, QTimer, QUrl, Signal
from PySide6.QtWidgets import QApplication, QDialog, QPlainTextEdit

from app import Main, STYLE
from core import Project
from render_jobs import RenderResult


class FakeAnalyzeThread(QObject):
    item = Signal(object)
    failed = Signal(str, str)
    finished = Signal()

    def __init__(self, paths, cache, ffmpeg, parent=None):
        super().__init__(parent)
        self.paths = paths
        self.running = False

    def start(self):
        self.running = True

    def isRunning(self):
        return self.running

    def finish(self):
        self.running = False
        self.finished.emit()


class FakeProxyThread(QObject):
    finished = Signal()

    def __init__(self, source, output, ffmpeg, parent=None):
        super().__init__(parent)
        self.source, self.output = source, output
        self.running = False
        self.job_result = None

    def start(self):
        self.running = True

    def isRunning(self):
        return self.running

    def finish(self, ok=True):
        self.job_result = RenderResult('completed' if ok else 'failed', 'proxy diagnostic', self.output)
        self.running = False
        self.finished.emit()


class DesktopImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = tempfile.TemporaryDirectory(prefix='lingjian-import-settings-')
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
        self.temp = tempfile.TemporaryDirectory(prefix='lingjian-import-')
        self.root = Path(self.temp.name)
        self.window = Main()
        self.window.auto_timer.stop()
        self.window.session.watch_timer.stop()
        self.window.alert = Mock()
        self.window.cache = self.root / 'cache'
        self.controller = self.window.import_controller
        self.analyze_patch = patch('desktop_import.MediaAnalyzeThread', FakeAnalyzeThread)
        self.proxy_patch = patch('desktop_import.ProxyThread', FakeProxyThread)
        self.analyze_patch.start()
        self.proxy_patch.start()

    def tearDown(self):
        self.controller.queued.clear()
        self.window.proxy_queue.clear()
        if self.controller.thread is not None:
            self.controller.thread.finish()
        if self.controller.proxy_worker is not None:
            self.controller.proxy_worker.finish(False)
        self.analyze_patch.stop()
        self.proxy_patch.stop()
        self.app.removeEventFilter(self.window.focus_guard)
        self.window.hide()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def path(self, name):
        return str(self.root / name)

    def meta(self, name, proxy=False):
        path = self.path(name)
        if proxy:
            Path(path).write_bytes(b'source')
        return dict(path=path, name=name, duration=2, width=320, height=180,
                    codec='hevc' if proxy else 'h264', fps=30, has_audio=True,
                    thumbnail=self.path('missing.jpg'), needs_proxy=proxy)

    def deliver(self, name, proxy=False):
        self.controller.thread.item.emit(self.meta(name, proxy))

    def test_repeated_and_equivalent_paths_keep_the_running_worker(self):
        path = self.path('a.mp4')
        self.window.start_import([path, self.path('./a.mp4')])
        original = self.window.analyze_thread
        self.window.start_import([path])
        self.assertIs(original, self.window.analyze_thread)
        self.assertEqual(original.paths, [path])
        self.assertFalse(self.controller.queued)
        self.assertIn('已跳过 1 个重复', self.window.statusBar().currentMessage())

    def test_append_waits_for_finished_before_advancing(self):
        self.window.start_import([self.path('a.mp4')])
        first = self.window.analyze_thread
        self.window.start_import([self.path('b.mp4')])
        self.deliver('a.mp4')
        first.running = False  # Even after run returns, wait for finished delivery.
        self.assertIs(first, self.window.analyze_thread)
        self.assertTrue(self.controller.busy)
        self.assertFalse(self.window.session.can_replace_document())
        first.finish()
        self.assertIsNot(first, self.window.analyze_thread)
        self.assertEqual(self.window.analyze_thread.paths, [self.path('b.mp4')])
        self.deliver('b.mp4')
        self.window.analyze_thread.finish()
        self.assertFalse(self.controller.busy)
        self.assertEqual(self.window.media.count(), 2)

    def test_success_is_idempotent_and_new_items_obey_active_search(self):
        self.window.search.setText('match')
        self.window.start_import([self.path('match.mp4'), self.path('other.mp4')])
        self.deliver('match.mp4')
        self.deliver('match.mp4')
        self.deliver('other.mp4')
        self.window.analyze_thread.finish()
        self.assertEqual(self.window.media.count(), 2)
        self.assertTrue(self.window.media_items[self.path('other.mp4')].isHidden())
        self.assertIn('显示 1 / 2', self.controller.status.text())
        self.window.search.clear()
        self.assertIn('显示 2 / 2', self.controller.status.text())
        self.window.start_import([self.path('match.mp4')])
        self.assertIsNone(self.window.analyze_thread)

    def test_failures_are_aggregated_without_modal_and_can_be_retried(self):
        path = self.path('broken.mp4')
        self.window.start_import([path])
        self.window.analyze_thread.failed.emit(path, 'cannot read')
        self.window.analyze_thread.finish()
        self.assertEqual(self.controller.issue_count, 1)
        self.assertFalse(self.controller.details.isHidden())
        self.window.alert.assert_not_called()
        self.window.start_import([path])
        self.deliver('broken.mp4')
        self.window.analyze_thread.finish()
        self.assertEqual(self.window.media.count(), 1)

    def test_audio_only_updates_bgm_without_video_task(self):
        self.window.start_import([self.path('music.MP3')])
        self.assertEqual(self.window.project.bgm, self.path('music.MP3'))
        self.assertIsNone(self.window.analyze_thread)
        self.assertIn('0 / 0', self.controller.status.text())
        self.assertTrue(self.window.isWindowModified())

    def test_start_failure_releases_ownership_and_allows_retry(self):
        with patch.object(FakeAnalyzeThread, 'start', side_effect=RuntimeError('no worker')):
            self.window.start_import([self.path('a.mp4')])
        self.assertFalse(self.controller.busy)
        self.assertIsNone(self.window.analyze_thread)
        self.assertIn('no worker', self.controller.issues[0])
        self.window.start_import([self.path('a.mp4')])
        self.assertIsNotNone(self.window.analyze_thread)

    def test_finished_without_file_result_is_reported_and_retriable(self):
        self.window.start_import([self.path('a.mp4')])
        self.window.analyze_thread.finish()
        self.assertFalse(self.controller.busy)
        self.assertEqual(self.controller.issue_count, 1)
        self.window.start_import([self.path('a.mp4')])
        self.assertIsNotNone(self.window.analyze_thread)

    def test_proxy_result_does_not_release_thread_until_finished(self):
        self.window.start_import([self.path('a.mp4'), self.path('b.mp4')])
        self.deliver('a.mp4', True)
        self.deliver('b.mp4', True)
        self.window.analyze_thread.finish()
        first = self.window.proxy_thread
        first.job_result = RenderResult('completed', '', first.output)
        self.window.start_next_proxy()
        self.assertIs(first, self.window.proxy_thread)
        self.assertEqual(len(self.window.proxy_queue), 1)
        first.finish()
        self.assertIsNot(first, self.window.proxy_thread)
        self.window.proxy_thread.finish()
        self.assertFalse(self.controller.busy)
        self.assertEqual(len(self.window.proxies), 2)

    def test_failed_proxy_preserves_source_monitor_and_original_media(self):
        self.window.start_import([self.path('a.mp4')])
        self.deliver('a.mp4', True)
        self.window.analyze_thread.finish()
        self.window.source_path = self.path('a.mp4')
        original = QUrl.fromLocalFile(self.path('a.mp4'))
        self.window.source_player.setSource(original)
        self.window.proxy_thread.finish(False)
        self.assertEqual(self.window.source_player.source(), original)
        self.assertNotIn(self.path('a.mp4'), self.window.proxies)
        self.assertIn(self.path('a.mp4'), self.window.metas)
        self.assertEqual(self.controller.issue_count, 1)
        self.assertIn('代理生成失败', self.window.media.item(0).text())

    def test_proxy_status_change_updates_search_and_visible_count(self):
        self.window.search.setText('等待生成')
        self.window.start_import([self.path('a.mp4')])
        self.deliver('a.mp4', True)
        self.window.analyze_thread.finish()
        self.assertFalse(self.window.media.item(0).isHidden())
        self.window.proxy_thread.finish()
        self.assertTrue(self.window.media.item(0).isHidden())
        self.assertIn('显示 0 / 1', self.controller.status.text())

    def test_proxy_start_failure_clears_queue_and_marks_all_rows(self):
        self.window.start_import([self.path('a.mp4'), self.path('b.mp4')])
        self.deliver('a.mp4', True)
        self.deliver('b.mp4', True)
        with patch.object(FakeProxyThread, 'start', side_effect=RuntimeError('no proxy worker')):
            self.window.analyze_thread.finish()
        self.assertFalse(self.controller.busy)
        self.assertEqual(self.controller.issue_count, 2)
        for index in range(2):
            self.assertIn('代理生成失败', self.window.media.item(index).text())

    def test_opening_another_project_clears_previous_import_issues(self):
        self.controller.record_issue('old.mp4', 'old error')
        other = self.root / 'other.ljproject'
        Project('other').save(other)
        self.assertTrue(self.window.session.open(other))
        self.assertEqual(self.controller.issue_count, 0)
        self.assertTrue(self.controller.details.isHidden())
        self.assertIn('0 / 0', self.controller.status.text())

    def test_issue_history_is_bounded_and_details_are_copyable(self):
        for index in range(55):
            self.controller.record_issue(f'{index}.mp4', f'problem {index}')
        self.assertEqual(self.controller.issue_count, 55)
        self.assertEqual(len(self.controller.issues), 50)
        captured = []
        def inspect():
            dialog = QApplication.activeModalWidget()
            if not isinstance(dialog, QDialog):
                QTimer.singleShot(10, inspect)
                return
            text = dialog.findChild(QPlainTextEdit)
            captured.append((text.isReadOnly(), text.toPlainText()))
            dialog.reject()
        QTimer.singleShot(0, inspect)
        self.controller.show_issues()
        self.assertTrue(captured[0][0])
        self.assertIn('problem 54', captured[0][1])
        self.assertNotIn('problem 0\n', captured[0][1])

    def test_import_feedback_is_visible_in_small_window(self):
        self.controller.record_issue('bad.mp4', 'bad media')
        self.window.resize(1120, 680)
        self.window.show()
        self.app.processEvents()
        self.assertTrue(self.controller.status.isVisible())
        self.assertTrue(self.controller.details.isVisible())
        self.assertLessEqual(self.controller.status.width(), self.window.media.parentWidget().width())


if __name__ == '__main__':
    unittest.main()
