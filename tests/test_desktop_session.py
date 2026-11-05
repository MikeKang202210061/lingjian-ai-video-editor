"""User decisions are simulated; no real media, modal interaction or cloud API."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QSettings, QStandardPaths
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from app import Main, STYLE
from core import Clip, Project
from project_session import RecoveryStore


class DesktopSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = tempfile.TemporaryDirectory(prefix='lingjian-session-settings-')
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
        self.temp = tempfile.TemporaryDirectory(prefix='lingjian-session-test-')
        self.root = Path(self.temp.name)
        self.window = Main()
        self.window.auto_timer.stop()
        self.window.session.watch_timer.stop()
        self.window.autosave = self.root / 'legacy.ljproject'
        self.session = self.window.session
        self.session.recovery = RecoveryStore(self.root / 'recovery', self.window.autosave)
        self.session.refresh_recovery()

    def tearDown(self):
        self.app.removeEventFilter(self.window.focus_guard)
        self.window.hide()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def make_dirty(self):
        self.window.title.setText('尚未保存的旅程')
        self.window.bgm_volume.setValue(.5)

    def test_autosave_keeps_document_dirty_and_preserves_ui_project_fields(self):
        self.make_dirty()
        self.assertTrue(self.window.isWindowModified())
        self.assertTrue(self.window.auto_save())
        backup = Project.load(self.session.recovery.current)
        self.assertEqual(backup.title, '尚未保存的旅程')
        self.assertEqual(backup.bgm_volume, .5)
        self.assertTrue(self.window.isWindowModified())
        self.assertEqual(self.window.project_path, '')

    def test_cancel_close_keeps_current_recovery(self):
        self.make_dirty()
        self.window.auto_save()
        event = QCloseEvent()
        with patch('desktop_session.QMessageBox.warning', return_value=QMessageBox.Cancel):
            self.window.closeEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertTrue(self.session.recovery.current.exists())

    def test_cancel_save_as_also_cancels_close(self):
        self.make_dirty()
        event = QCloseEvent()
        with patch('desktop_session.QMessageBox.warning', return_value=QMessageBox.Save), \
                patch('desktop_session.QFileDialog.getSaveFileName', return_value=('', '')):
            self.window.closeEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertTrue(self.window.isWindowModified())

    def test_successful_save_closes_and_cleans_only_its_own_recovery(self):
        other = RecoveryStore(self.root / 'recovery')
        other.save(Project('另一个工程').to_dict())
        self.make_dirty()
        self.window.auto_save()
        saved = self.root / 'saved.ljproject'
        event = QCloseEvent()
        with patch('desktop_session.QMessageBox.warning', return_value=QMessageBox.Save), \
                patch('desktop_session.QFileDialog.getSaveFileName', return_value=(str(saved), '')):
            self.window.closeEvent(event)
        self.assertTrue(event.isAccepted())
        self.assertEqual(Project.load(saved).title, '尚未保存的旅程')
        self.assertFalse(self.session.recovery.current.exists())
        self.assertTrue(other.current.exists())
        self.assertFalse(self.window.isWindowModified())

    def test_failed_save_does_not_close_or_remove_recovery(self):
        self.make_dirty()
        self.window.auto_save()
        self.window.project_path = str(self.root / 'existing.ljproject')
        event = QCloseEvent()
        with patch('desktop_session.QMessageBox.warning', return_value=QMessageBox.Save), \
                patch.object(self.window.project, 'save', side_effect=OSError('full')), \
                patch.object(self.window, 'alert'):
            self.window.closeEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertTrue(self.session.recovery.current.exists())
        self.assertTrue(self.window.isWindowModified())

    def test_discard_close_keeps_other_windows_recovery(self):
        other = RecoveryStore(self.root / 'recovery')
        other.save(Project('保留的副本').to_dict())
        self.make_dirty()
        self.window.auto_save()
        event = QCloseEvent()
        with patch('desktop_session.QMessageBox.warning', return_value=QMessageBox.Discard):
            self.window.closeEvent(event)
        self.assertTrue(event.isAccepted())
        self.assertFalse(self.session.recovery.current.exists())
        self.assertTrue(other.current.exists())

    def test_invalid_open_does_not_ask_to_discard_or_replace_current_project(self):
        self.make_dirty()
        self.window.auto_save()
        broken = self.root / 'broken.ljproject'
        broken.write_text('{broken')
        with patch('desktop_session.QMessageBox.warning') as prompt, patch.object(self.window, 'alert'):
            self.assertFalse(self.session.open(broken))
        prompt.assert_not_called()
        self.assertEqual(self.window.title.text(), '尚未保存的旅程')
        self.assertTrue(self.session.recovery.current.exists())

    def test_cancel_open_preserves_timeline_and_project_path(self):
        self.make_dirty()
        target = self.root / 'other.ljproject'
        Project('另一个工程').save(target)
        with patch('desktop_session.QMessageBox.warning', return_value=QMessageBox.Cancel):
            self.assertFalse(self.session.open(target))
        self.assertEqual(self.window.title.text(), '尚未保存的旅程')
        self.assertEqual(self.window.project_path, '')

    def test_recovery_uses_save_as_and_does_not_overwrite_original(self):
        original = self.root / 'original.ljproject'
        Project('原工程').save(original)
        old = RecoveryStore(self.root / 'recovery')
        old.save(Project('崩溃前的修改').to_dict(), str(original))
        self.assertTrue(self.session.open(old.current, recovered=True))
        self.assertEqual(self.window.project_path, '')
        self.assertTrue(self.window.isWindowModified())
        self.assertEqual(Project.load(original).title, '原工程')
        restored = self.root / 'restored.ljproject'
        self.assertTrue(self.session.save_to(restored))
        self.assertEqual(Project.load(restored).title, '崩溃前的修改')
        self.assertEqual(Project.load(original).title, '原工程')
        self.assertFalse(old.current.exists())

    def test_reopening_same_file_after_saving_uses_the_newly_saved_version(self):
        original = self.root / 'original.ljproject'
        self.assertTrue(self.session.save_to(original))
        self.make_dirty()
        with patch('desktop_session.QMessageBox.warning', return_value=QMessageBox.Save):
            self.assertTrue(self.session.open(original))
        self.assertEqual(self.window.project.title, '尚未保存的旅程')
        self.assertEqual(Project.load(original).title, '尚未保存的旅程')
        self.assertFalse(self.window.isWindowModified())

    def test_recovery_offer_is_visible_and_idle_autosave_preserves_older_sessions(self):
        old = RecoveryStore(self.root / 'recovery')
        old.save(Project('等待恢复').to_dict())
        self.session.refresh_recovery()
        self.assertFalse(self.session.banner.isHidden())
        self.assertTrue(self.session.recovery_action.isEnabled())
        self.assertFalse(self.window.auto_save())
        self.assertTrue(old.current.exists())

    def test_project_replacement_clears_old_media_and_monitor_state(self):
        self.window.media.addItem('上一工程的素材')
        self.window.metas['old.mp4'] = {'path': 'old.mp4'}
        self.window.source_path = 'old.mp4'
        self.window.source_in, self.window.source_out = 2.0, 8.0
        self.window.preview_stack.setCurrentIndex(1)
        self.window.timeline.set_playhead(42)
        target = self.root / 'next.ljproject'
        Project('新工程').save(target)
        self.assertTrue(self.session.open(target))
        self.assertEqual(self.window.media.count(), 0)
        self.assertFalse(self.window.metas)
        self.assertEqual(self.window.source_path, '')
        self.assertEqual(self.window.source_out, 0)
        self.assertEqual(self.window.preview_stack.currentIndex(), 0)
        self.assertEqual(self.window.timeline.playhead, 0)

    def test_undo_to_saved_content_clears_dirty_state(self):
        saved = self.root / 'saved.ljproject'
        self.assertTrue(self.session.save_to(saved))
        self.window.remember()
        self.window.project.clips.append(Clip('fixture.mp4', 0, 2))
        self.window.changed('测试修改')
        self.assertTrue(self.window.isWindowModified())
        self.window.undo()
        self.assertFalse(self.window.isWindowModified())
        self.window.redo()
        self.assertTrue(self.window.isWindowModified())

    def test_running_background_task_blocks_close_and_project_replacement(self):
        self.window.render_thread = SimpleNamespace(isRunning=lambda: True)
        event = QCloseEvent()
        with patch.object(self.window, 'alert'):
            self.window.closeEvent(event)
            self.assertFalse(self.session.open(self.root / 'anything.ljproject'))
        self.assertFalse(event.isAccepted())
        self.window.render_thread = None


if __name__ == '__main__':
    unittest.main()
