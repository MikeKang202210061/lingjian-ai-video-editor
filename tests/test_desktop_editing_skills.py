"""Real offscreen dialogs and worker-context handoff; providers are mocked."""
import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QSettings, QStandardPaths
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QDialog, QPlainTextEdit

from app import CloudAIThread, Main, STYLE
from core import Clip, Project
from editing_skills import load_skill
from tests.test_editing_skills import sample_plan


class DesktopEditingSkillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix='lingjian-skill-ui-')
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

    def tearDown(self):
        self.app.removeEventFilter(self.window.focus_guard)
        self.window.hide()
        self.window.deleteLater()
        self.app.processEvents()

    def choose(self, skill_id):
        self.window.editing_skill.setCurrentIndex(self.window.editing_skill.findData(skill_id))

    def test_selector_injects_selected_instructions_and_none_keeps_old_prompt(self):
        original = self.window.edit_prompt()
        self.choose('hetongxue-inspired')
        self.assertIn('科技实验讲解', self.window.edit_prompt())
        self.assertIn('不要为了快节奏打断必要因果', self.window.edit_prompt())
        self.choose('mediastorm-inspired')
        self.assertIn('保留现场原声', self.window.edit_prompt())
        self.choose('')
        self.assertEqual(self.window.edit_prompt(), original)

    def test_worker_keeps_captured_skill_and_prompt_when_selection_changes(self):
        self.choose('hetongxue-inspired')
        snapshot = self.window.selected_skill()
        worker = CloudAIThread([{'path': 'fixture.mp4', 'name': 'fixture', 'duration': 3}], object(),
                               Path(self.folder.name), self.window.edit_prompt(snapshot), 40, snapshot)
        spy = QSignalSpy(worker.done)
        snapshot['profile']['zoom'] = 9
        self.choose('mediastorm-inspired')
        with patch('app.analyze_video', return_value=[]) as analyze, patch('app.plan_sequence', return_value=[]) as planner:
            worker.run()
        self.assertEqual(spy.count(), 1)
        result = spy.at(0)[0]
        self.assertIn('科技实验讲解', analyze.call_args.args[5])
        self.assertEqual(planner.call_args.args[3], analyze.call_args.args[5])
        plan = self.window.result_plan(result, 'cloud')
        self.assertEqual(plan['editing_skill']['id'], 'hetongxue-inspired')
        self.assertEqual(plan['editing_skill']['profile']['zoom'], 1.06)

    def test_pack_error_blocks_worker_before_disabled_buttons_or_provider_calls(self):
        self.window.project.clips = [Clip('fixture.mp4', 0, 3)]
        self.window.metas['fixture.mp4'] = {'path': 'fixture.mp4'}
        with patch('app.load_skill', side_effect=ValueError('missing skill')), patch('app.SceneThread') as factory:
            self.window.deep_ai()
        factory.assert_not_called()
        self.assertTrue(self.window.deep_btn.isEnabled())
        self.window.alert.assert_called_once()

    def test_default_skill_opening_is_clean_and_explicit_opening_remains_available(self):
        plan = sample_plan()
        plan['editing_skill'] = load_skill('mediastorm-inspired')
        clean = self.window.compose_ai_plan(plan, 'smart')
        self.assertFalse(clean.get('overlay_tracks'))
        self.assertNotIn('creative_direction', clean)
        from edit_protocol import opening_preset_choices
        explicit = self.window.compose_ai_plan(plan, opening_preset_choices()[1][1])
        self.assertIn('creative_direction', explicit)
        self.assertEqual(explicit['editing_skill']['id'], 'mediastorm-inspired')

    def test_dialog_cancel_preserves_project_and_lists_limits(self):
        self.window.project = Project(clips=[Clip('old.mp4', 0, 2)])
        old = copy.deepcopy(self.window.project.to_dict())
        plan = sample_plan()
        plan['editing_skill'] = load_skill('hetongxue-inspired')
        def cancel(dialog):
            details = dialog.findChild(QPlainTextEdit).toPlainText()
            self.assertIn('科技实验讲解', details)
            self.assertIn('静态构图不等同动态运镜', details)
            return QDialog.Rejected
        with patch.object(QDialog, 'exec', cancel):
            self.assertFalse(self.window.review_ai_plan(plan))
        self.assertEqual(self.window.project.to_dict(), old)

    def test_apply_undo_redo_retains_recipe_and_audit_snapshot(self):
        self.window.project = Project(clips=[Clip('old.mp4', 0, 2)])
        old = copy.deepcopy(self.window.project.to_dict())
        plan = sample_plan()
        plan['editing_skill'] = load_skill('hetongxue-inspired')
        with patch.object(QDialog, 'exec', return_value=QDialog.Accepted), \
                patch.object(self.window, 'apply_plan_sfx'), patch.object(self.window, 'select_clip'):
            self.assertTrue(self.window.review_ai_plan(plan))
            applied = copy.deepcopy(self.window.project.to_dict())
            self.assertEqual(applied['edit_log'][-1]['skill'], 'hetongxue-inspired')
            self.assertTrue(applied['clips'][1]['resolve_treatment'])
            self.window.undo()
            self.assertEqual(self.window.project.to_dict(), old)
            self.window.redo()
            self.assertEqual(self.window.project.to_dict(), applied)


if __name__ == '__main__':
    unittest.main()
