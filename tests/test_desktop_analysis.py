"""Analysis re-entry and request/result consistency; no live provider calls."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QSettings, QStandardPaths
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from app import CloudAIThread, Main, STYLE
from core import Clip


class DesktopAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix='lingjian-analysis-settings-')
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, cls.folder.name)
        QStandardPaths.setTestModeEnabled(True)
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle('Fusion')
        cls.app.setStyleSheet(STYLE)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def setUp(self):
        QSettings('LingJianAI', 'Editor').clear()
        self.window = Main()
        self.window.auto_timer.stop()
        self.window.session.watch_timer.stop()
        self.window.alert = Mock()
        self.window.review_ai_plan = Mock()

    def tearDown(self):
        self.window.scene_thread = self.window.cloud_thread = None
        self.app.removeEventFilter(self.window.focus_guard)
        self.window.hide()
        self.window.deleteLater()
        self.app.processEvents()

    def assert_active_task_is_preserved(self, attribute):
        worker = SimpleNamespace(isRunning=lambda: True)
        setattr(self.window, attribute, worker)
        with patch('app.SceneThread') as offline, patch('app.CloudAIThread') as cloud, \
                patch.object(self.window, 'api_config', side_effect=AssertionError('must not request config')):
            self.window.deep_ai()
            self.window.cloud_ai()
            self.window.quick_ai()
        offline.assert_not_called()
        cloud.assert_not_called()
        self.window.review_ai_plan.assert_not_called()
        self.window.alert.assert_not_called()
        self.assertIs(getattr(self.window, attribute), worker)
        self.assertIn('已有分析任务', self.window.ai_status.text())

    def test_offline_task_blocks_all_other_analysis_starts(self):
        self.assert_active_task_is_preserved('scene_thread')

    def test_cloud_task_blocks_all_other_analysis_starts(self):
        self.assert_active_task_is_preserved('cloud_thread')

    def test_finished_task_does_not_block_a_new_offline_request(self):
        window = self.window
        window.scene_thread = SimpleNamespace(isRunning=lambda: False)
        window.project.clips = [Clip('video.mp4', 0, 2)]
        window.metas['video.mp4'] = {'path': 'video.mp4'}
        with patch('app.SceneThread') as factory:
            window.deep_ai()
        factory.assert_called_once()
        factory.return_value.start.assert_called_once()

    def test_offline_result_keeps_original_request_when_controls_change(self):
        self.window.target.setValue(120)
        self.window.prompt.setPlainText('new request')
        result = {'sequence': [], 'analyses': [], 'target': 30, 'prompt': 'original request'}
        with patch('app.build_edit_plan', return_value={'plan': 'offline'}) as build:
            self.window.deep_done(result)
        self.assertEqual(build.call_args.args[2:], (30, 'original request', 'offline'))
        self.window.review_ai_plan.assert_called_once_with({'plan': 'offline'})

    def test_cloud_result_keeps_original_request_when_controls_change(self):
        self.window.target.setValue(120)
        self.window.prompt.setPlainText('new request')
        result = {'sequence': [], 'analyses': [], 'target': 30, 'prompt': 'original request'}
        with patch('app.build_edit_plan', return_value={'plan': 'cloud'}) as build:
            self.window.cloud_done(result)
        self.assertEqual(build.call_args.args[2:], (30, 'original request', 'cloud'))
        self.window.review_ai_plan.assert_called_once_with({'plan': 'cloud'})

    def test_legacy_result_without_request_metadata_remains_supported(self):
        self.window.target.setValue(45)
        prompt = self.window.edit_prompt()
        with patch('app.build_edit_plan', return_value={}) as build:
            self.window.deep_done({'sequence': [], 'analyses': []})
        self.assertEqual(build.call_args.args[2:], (45, prompt, 'offline'))

    def test_cloud_worker_returns_request_context_with_mocked_provider(self):
        worker = CloudAIThread([{'path': 'fixture.mp4', 'name': 'fixture.mp4', 'duration': 2}], object(),
                               Path(self.folder.name), 'captured prompt', 30)
        result = QSignalSpy(worker.done)
        with patch('app.analyze_video', return_value=[]), patch('app.plan_sequence', return_value=[]):
            worker.start()
            self.assertTrue(worker.wait(5000))
        self.app.processEvents()
        self.assertEqual(result.count(), 1)
        self.assertEqual(result.at(0)[0]['target'], 30)
        self.assertEqual(result.at(0)[0]['prompt'], 'captured prompt')


if __name__ == '__main__':
    unittest.main()
