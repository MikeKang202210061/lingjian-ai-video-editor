"""Run with QT_QPA_PLATFORM=offscreen; requires the desktop PySide6 dependency."""
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QSettings, QStandardPaths, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea

from app import Main, STYLE
from core import Clip


class DesktopWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix='lingjian-gui-tests-')
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
        self.window.show()
        self.window.activateWindow()
        self.window.resize(1366, 768)
        self.app.processEvents()

    def tearDown(self):
        # Do not invoke closeEvent/autosave on generated test projects.
        self.app.removeEventFilter(self.window.focus_guard)
        self.window.hide()
        self.window.deleteLater()
        self.app.processEvents()

    def test_compact_layout_keeps_inspector_visible(self):
        window = self.window
        self.assertLessEqual(window.width(), 1366)
        self.assertLessEqual(window.height(), 768)
        self.assertEqual(window.main_workspace.count(), 3)
        self.assertTrue(window.inspector_host.isVisible())
        self.assertEqual(window.inspector.count(), 8)
        for index in range(8):
            window.inspector_selector.setCurrentIndex(index)
            self.app.processEvents()
            self.assertEqual(window.inspector.currentIndex(), index)
            self.assertIsInstance(window.inspector.widget(index), QScrollArea)
            self.assertTrue(window.inspector.widget(index).isVisible())

    def test_monitor_mode_switch_reuses_both_players(self):
        window = self.window
        workspace = window.monitor_workspace
        for _ in range(3):
            window.workspace_selector.setCurrentIndex(1)
            self.assertEqual(workspace.splitter.count(), 2)
            self.assertIs(workspace.splitter.widget(0), workspace.source)
            window.workspace_selector.setCurrentIndex(0)
            self.assertEqual(workspace.tabs.count(), 2)
            self.assertIs(workspace.tabs.widget(1), workspace.program)

    def test_small_workspace_panels_do_not_require_hidden_horizontal_scrolling(self):
        window = self.window
        window.resize(1120, 680)
        for index in range(8):
            window.inspector_selector.setCurrentIndex(index)
            self.app.processEvents()
            scroll = window.inspector.widget(index)
            self.assertLessEqual(scroll.widget().width(), scroll.viewport().width(), index)

    def test_typing_and_delete_do_not_edit_timeline(self):
        window = self.window
        window.project.clips = [Clip('fixture.mp4', 0, 2)]
        window.current_clip = 0
        window.inspector_selector.setCurrentIndex(1)
        window.caption.setFocus()
        self.app.processEvents()
        QTest.keyClicks(window.caption, 'banana I O J K L')
        self.assertEqual(window.caption.text(), 'banana I O J K L')
        self.assertEqual(window.timeline.tool, 'select')
        QTest.keyClick(window.caption, Qt.Key_Delete)
        QTest.keyClick(window.caption, Qt.Key_D, Qt.ControlModifier)
        self.assertEqual(len(window.project.clips), 1)

    def test_prompt_typing_and_timeline_shortcut_have_separate_focus(self):
        window = self.window
        window.inspector_selector.setCurrentIndex(4)
        window.prompt.clear()
        window.prompt.setFocus()
        self.app.processEvents()
        QTest.keyClicks(window.prompt, 'B A N I O')
        self.assertEqual(window.prompt.toPlainText(), 'B A N I O')
        self.assertEqual(window.timeline.tool, 'select')
        window.timeline.setFocus()
        self.app.processEvents()
        QTest.keyClick(window.timeline, Qt.Key_B)
        self.assertEqual(window.timeline.tool, 'blade')

    def test_failed_save_does_not_change_active_project_path(self):
        window = self.window
        window.project_path = 'previous.ljproject'
        with patch.object(window.project, 'save', side_effect=OSError('disk full')), \
                patch.object(window, 'alert') as alert:
            self.assertFalse(window._save_project_to('new.ljproject'))
            alert.assert_called_once()
        self.assertEqual(window.project_path, 'previous.ljproject')


if __name__ == '__main__':
    unittest.main()
