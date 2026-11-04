"""Desktop save/open/restore workflow, separate from timeline editing commands."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)

from core import Project
from project_session import DocumentState, RecoveryStore
from sfx_library import resolve_sfx


class EditorSession(QObject):
    def __init__(self, window, asset_root: Path):
        super().__init__(window)
        self.window = window
        self.asset_root = asset_root
        self.recovery = RecoveryStore(window.cache / 'recovery', window.autosave)
        self.recovered_from = None
        self.state = DocumentState(self.snapshot())
        self.status = QLabel()
        self.status.setAccessibleName('工程保存状态')
        window.statusBar().addPermanentWidget(self.status)
        window.title.textChanged.connect(self.refresh)
        window.bgm_volume.valueChanged.connect(self.refresh)

        self.banner = QFrame()
        self.banner.setObjectName('toolGlass')
        row = QHBoxLayout(self.banner)
        row.setContentsMargins(12, 6, 12, 6)
        self.recovery_note = QLabel()
        self.recovery_note.setWordWrap(True)
        row.addWidget(self.recovery_note, 1)
        button = QPushButton('查看并恢复…')
        button.clicked.connect(self.restore_autosave)
        row.addWidget(button)
        window.centralWidget().layout().insertWidget(2, self.banner)
        self.recovery_action = QAction('恢复自动保存…', window)
        self.recovery_action.setToolTip('恢复为未保存工程，再另存为；原工程不会自动覆盖。')
        self.recovery_action.triggered.connect(self.restore_autosave)
        file_menu = window.menuBar().actions()[0].menu()
        file_menu.addSeparator()
        file_menu.addAction(self.recovery_action)
        self.refresh_recovery()
        self.watch_timer = QTimer(self)
        self.watch_timer.timeout.connect(self.refresh)
        self.watch_timer.start(2000)
        self.refresh()

    def snapshot(self):
        window = self.window
        data = window.project.to_dict()
        data['title'] = window.title.text().strip() or '未命名作品'
        data['bgm_volume'] = window.bgm_volume.value()
        return data

    def refresh(self, *_):
        data = self.snapshot()
        dirty = self.state.is_dirty(data)
        self.window.setWindowTitle(f"{data['title']}[*] · 灵剪 AI")
        self.window.setWindowModified(dirty)
        self.status.setText('未保存修改' if dirty else ('已保存' if self.window.project_path else '新工程'))
        self.status.setToolTip(self.window.project_path or '另存为 .ljproject 工程后可继续编辑。')

    def refresh_recovery(self):
        count = len(self.recovery.entries())
        self.banner.setVisible(bool(count))
        self.recovery_note.setText(f'找到 {count} 份可恢复的自动保存副本。')
        self.recovery_action.setEnabled(bool(count))

    def running_tasks(self):
        names = ('proxy_thread', 'analyze_thread', 'scene_thread', 'cloud_thread',
                 'render_thread', 'effect_thread', 'person_thread', 'resolve_thread')
        tasks = [name for name in names if (task := getattr(self.window, name, None))
                 is not None and task.isRunning()]
        importer = getattr(self.window, 'import_controller', None)
        if importer is not None and importer.busy:
            tasks.append('media_import')
        resolve = getattr(self.window, 'resolve_controller', None)
        if resolve is not None and resolve.busy:
            tasks.append('resolve_backend')
        return tasks

    def can_replace_document(self):
        if not self.running_tasks():
            return True
        self.window.alert('后台任务仍在运行，请等待完成后再关闭窗口或切换工程。')
        return False

    def maybe_save(self):
        if not self.state.is_dirty(self.snapshot()):
            return True
        answer = QMessageBox.warning(
            self.window, '工程尚未保存', '当前工程有未保存的修改。要先保存吗？',
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        if answer == QMessageBox.Save:
            return self.save()
        return answer == QMessageBox.Discard

    def save(self):
        return self.save_to(self.window.project_path) if self.window.project_path else self.save_as()

    def save_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self.window, '工程另存为', self.snapshot()['title'] + '.ljproject', '灵剪工程 (*.ljproject)')
        if not path:
            return False
        if Path(path).suffix.lower() != '.ljproject':
            path += '.ljproject'
        return self.save_to(path)

    def save_to(self, path):
        window = self.window
        window.project.title = self.snapshot()['title']
        window.project.bgm_volume = window.bgm_volume.value()
        try:
            window.project.save(path)
        except Exception as exc:
            window.alert('工程保存失败，原文件已保留：' + str(exc), True)
            self.refresh()
            return False
        window.project_path = str(path)
        self.state.mark_clean(self.snapshot())
        self._cleanup_snapshots(protected=Path(path))
        self.refresh()
        window.statusBar().showMessage('工程已保存：' + str(path))
        return True

    def _cleanup_snapshots(self, protected=None):
        try:
            self.recovery.clear_current(protected=protected)
            if self.recovered_from:
                self.recovery.remove(self.recovered_from, protected=protected)
                self.recovered_from = None
        except OSError as exc:
            self.window.statusBar().showMessage('工程已保存；旧恢复副本清理失败：' + str(exc))
        self.refresh_recovery()

    def autosave(self):
        data = self.snapshot()
        if not self.state.is_dirty(data):
            return False
        try:
            changed = self.recovery.save(data, self.window.project_path)
        except Exception as exc:
            self.window.statusBar().showMessage('自动保存失败，请手动保存工程：' + str(exc))
            return False
        if changed:
            self.window.statusBar().showMessage('恢复副本已更新 · 原工程仍有未保存修改')
        return changed

    def open(self, path=None, *, recovered=False):
        window = self.window
        if not self.can_replace_document():
            return False
        if not path:
            path, _ = QFileDialog.getOpenFileName(window, '打开工程', '', '灵剪工程 (*.ljproject)')
        if not path:
            return False
        try:
            project = Project.load(path)
            for cue in project.sfx:
                bundled = resolve_sfx(self.asset_root, cue.effect_id)
                if cue.effect_id and Path(bundled).exists():
                    cue.path = bundled
            missing = [clip.path for clip in [*project.clips, *project.overlays]
                       if not Path(clip.path).is_file()]
            if missing:
                window.alert('找不到以下原素材：\n' + '\n'.join(missing[:8]), True)
                return False
        except Exception as exc:
            window.alert('工程打开失败：' + str(exc), True)
            return False
        # Validate before asking to discard anything; a failed open preserves state.
        same_file = bool(window.project_path) and Path(path).resolve() == Path(window.project_path).resolve()
        was_dirty = self.state.is_dirty(self.snapshot())
        if not self.maybe_save():
            return False
        # "Open this same file → Save" must display what was just saved, rather
        # than the stale copy loaded before the decision dialog.
        if same_file and was_dirty and not self.state.is_dirty(self.snapshot()):
            try:
                project = Project.load(path)
            except Exception as exc:
                window.alert('工程重新读取失败：' + str(exc), True)
                return False
        window.player.stop()
        window.source_player.stop()
        window.player.setSource(QUrl())
        window.source_player.setSource(QUrl())
        try:
            self.recovery.clear_current(protected=Path(window.project_path) if window.project_path else None)
        except OSError:
            pass  # Keeping a recovery copy is safer than blocking a valid open.
        window.project = project
        window.project_path = '' if recovered else str(path)
        window.title.setText(project.title)
        window.bgm.setText(Path(project.bgm).name if project.bgm else '')
        window.bgm_volume.setValue(project.bgm_volume)
        window.history.clear()
        window.redo_history.clear()
        window.metas.clear()
        window.media_items.clear()
        window.thumbs.clear()
        window.proxies.clear()
        window.proxy_queue.clear()
        window.media.clear()
        window.import_controller.reset()
        window.source_path = ''
        window.source_in = window.source_out = 0.0
        window.mark_label.setText('入点 --  出点 --')
        window.source_time.setText('00:00:00:00')
        window.time.setText('00:00.0 / 00:00.0')
        window.seek.setValue(0)
        window.preview_stack.setCurrentIndex(0)
        window.timeline.set_playhead(0)
        self.recovered_from = Path(path) if recovered else None
        self.state.mark_clean(self.snapshot())
        if recovered:
            self.state.force_dirty()
        window.restore_project('已恢复副本，请另存为工程' if recovered else '工程已打开')
        paths = list(dict.fromkeys(clip.path for clip in [*project.clips, *project.overlays]))
        if paths:
            window.start_import(paths)
        self.refresh_recovery()
        return True

    def restore_autosave(self):
        if not self.can_replace_document():
            return False
        entries = self.recovery.entries()
        if not entries:
            self.refresh_recovery()
            return False
        dialog = QDialog(self.window)
        dialog.setWindowTitle('恢复自动保存')
        dialog.resize(580, 360)
        layout = QVBoxLayout(dialog)
        note = QLabel('选择恢复副本。恢复后请另存为；原工程不会被自动覆盖。')
        note.setWordWrap(True)
        layout.addWidget(note)
        listing = QListWidget()
        for entry in entries:
            stamp = datetime.fromtimestamp(entry.saved_at).strftime('%Y-%m-%d %H:%M')
            item = QListWidgetItem(f'{entry.title}\n{stamp}')
            item.setToolTip(entry.source_project or str(entry.path))
            listing.addItem(item)
        listing.setCurrentRow(0)
        layout.addWidget(listing, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Open | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Open).setText('恢复为未保存工程')
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted and listing.currentRow() >= 0:
            return self.open(entries[listing.currentRow()].path, recovered=True)
        return False

    def close(self, event):
        if not self.can_replace_document() or not self.maybe_save():
            event.ignore()
            return
        # On an intentional discard, remove only this window's snapshot. Recovery
        # copies belonging to other sessions are never silently removed.
        try:
            self.recovery.clear_current(protected=Path(self.window.project_path) if self.window.project_path else None)
        except OSError:
            pass
        self.window.auto_timer.stop()
        self.watch_timer.stop()
        self.window.player.stop()
        self.window.source_player.stop()
        event.accept()
