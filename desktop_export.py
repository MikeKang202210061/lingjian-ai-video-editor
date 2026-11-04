"""One export transaction per window, held until its QThread finishes."""
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QFileDialog, QMessageBox, QPushButton

from core import Project, build_render_command
from desktop_render import RenderThread
from render_jobs import RenderResult, ensure_output_is_safe


class ExportController(QObject):
    def __init__(self, window, ffmpeg):
        super().__init__(window)
        self.window = window
        self.ffmpeg = ffmpeg
        self.thread = None
        self.cancel_button = QPushButton('取消导出')
        self.cancel_button.setAccessibleName('取消当前导出')
        self.cancel_button.setToolTip('停止渲染并清理临时文件，保留此前的输出视频。')
        self.cancel_button.clicked.connect(self.cancel)
        layout = window.render_progress.parentWidget().layout()
        layout.insertWidget(layout.indexOf(window.render_progress) + 1, self.cancel_button)
        self.cancel_button.hide()

    @property
    def busy(self):
        # A completed-but-not-yet-delivered thread still owns this transaction.
        return self.thread is not None

    def start(self):
        window = self.window
        resolve = getattr(window, 'resolve_controller', None)
        if resolve is not None and resolve.rendering:
            window.statusBar().showMessage('达芬奇导出仍在运行，请等待完成或取消。')
            return False
        if self.busy:
            window.inspector.setCurrentIndex(6)
            window.statusBar().showMessage('已有导出任务，请等待完成或取消当前导出。')
            return False
        path, _ = QFileDialog.getSaveFileName(window, '导出 MP4',
                                             window.title.text() + '.mp4', 'MP4 视频 (*.mp4)')
        if not path:
            return False
        if Path(path).suffix.lower() != '.mp4':
            path += '.mp4'
        try:
            report = window.preflight(path)
            if report['warnings'] and QMessageBox.question(
                    window, '导出前提醒', '机械检查发现以下非阻止项：\n\n- ' +
                    '\n- '.join(report['warnings']) + '\n\n仍要继续导出吗？') != QMessageBox.Yes:
                return False
            width, height, quality = window.preset.currentData()
            window.project.bgm_volume = window.bgm_volume.value()
            snapshot = Project.from_dict(window.project.to_dict())
            if window.timeline.audio_muted:
                for clip in snapshot.clips:
                    clip.volume = 0
            command = build_render_command(self.ffmpeg, snapshot, path, width, height, quality)
            ensure_output_is_safe(command, path)
            thread = RenderThread(command, snapshot.duration, self)
            self.thread = window.render_thread = thread
            thread.progress.connect(window.render_progress.setValue)
            thread.finished.connect(self.finished)
            window.render_progress.show()
            window.render_progress.setValue(0)
            self.cancel_button.setText('取消导出')
            self.cancel_button.setEnabled(True)
            self.cancel_button.show()
            self._enable_export(False)
            window.inspector.setCurrentIndex(6)
            window.render_status.setText('正在导出启动时的时间线快照。失败或取消会保留旧输出。')
            thread.start()
            return True
        except Exception as exc:
            thread = self.thread
            self.thread = window.render_thread = None
            if thread is not None:
                thread.deleteLater()
            self._enable_export(True)
            self.cancel_button.hide()
            window.alert('无法开始导出：' + str(exc), True)
            return False

    def _enable_export(self, enabled):
        self.window.export_top.setEnabled(enabled)
        self.window.export_top.setText('↑  导出' if enabled else '导出中…')
        self.window.export_button.setEnabled(enabled)
        self.window.export_button.setText('导出 MP4' if enabled else '导出中…')

    def cancel(self):
        if not self.busy:
            return
        self.thread.cancel()
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText('正在取消…')
        self.window.render_status.setText('正在停止导出并清理临时文件，请稍候…')

    def finished(self):
        thread = self.thread
        if thread is None:
            return
        result = thread.job_result or RenderResult('failed', '导出线程没有返回结果。', '')
        self.thread = self.window.render_thread = None
        thread.deleteLater()
        self._enable_export(True)
        self.cancel_button.hide()
        window = self.window
        if result.ok:
            window.render_progress.setValue(100)
            meta = result.metadata
            window.render_status.setText('导出完成并已校验：' + result.output)
            window.alert('导出完成：\n' + result.output + '\n\n' +
                         f"{meta.get('width', '?')}×{meta.get('height', '?')} · " +
                         f"{meta.get('duration', 0):.1f} 秒 · H.264/AAC")
        elif result.status == 'cancelled':
            window.render_progress.setValue(0)
            window.render_status.setText(result.message)
            window.statusBar().showMessage(result.message)
        else:
            window.render_progress.setValue(0)
            window.render_status.setText('导出失败；旧输出未改变，可修改后重试。')
            window.alert('导出失败：\n' + result.message, True)
