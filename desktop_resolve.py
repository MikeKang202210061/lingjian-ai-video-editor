"""Experimental Resolve backend controls; the editor remains the primary UI."""
import sys
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QProgressBar, QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from resolve_client import run_bridge
from resolve_plan import project_request, transfer_warnings


class ResolveThread(QThread):
    def __init__(self, python, sdk, request, ffmpeg, parent=None):
        super().__init__(parent)
        self.python, self.sdk, self.request, self.ffmpeg = python, sdk, request, ffmpeg
        self.job_result = None
        self.cancel_requested = False

    def cancel(self):
        self.cancel_requested = True
        directory = self.request.get('job_dir')
        if directory and Path(directory).is_dir():
            (Path(directory) / 'cancel').touch()

    def run(self):
        if self.cancel_requested:
            self.job_result = {'status': 'cancelled', 'message': '启动前已取消。'}
            return
        try:
            self.job_result = run_bridge(self.python, self.sdk, self.request, self.ffmpeg,
                                         cancel_requested=lambda: self.cancel_requested)
        except Exception as exc:
            self.job_result = {'status': 'uncertain', 'message': str(exc)}


class ResolveController(QObject):
    def __init__(self, window, ffmpeg):
        super().__init__(window)
        self.window, self.ffmpeg = window, ffmpeg
        self.thread = None
        self.checked_configuration = None
        self.ready = False
        self.last_output = ''
        self.preview_dialog = None
        group = QGroupBox('达芬奇后端（实验）')
        group.setMinimumWidth(0)
        group.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout = QVBoxLayout(group)
        toggle = QCheckBox('展开达芬奇连接与导出')
        layout.addWidget(toggle)
        body = QWidget()
        form_layout = QVBoxLayout(body)
        layout.addWidget(body)
        toggle.toggled.connect(body.setVisible)
        body.hide()
        note = QLabel('需本机运行兼容的 Resolve Studio，并开启 Local 外部脚本。'
                      '此版本仅传递素材、入出点和顺序，使用新建达芬奇工程渲染；'
                      '字幕、转场、蒙版、叠加和混音尚未迁移。')
        note.setWordWrap(True)
        note.setMinimumWidth(0)
        note.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        form_layout.addWidget(note)
        default_python = '' if getattr(sys, 'frozen', False) else sys.executable
        self.python = QLineEdit(str(window.settings.value('resolve/python', default_python)))
        self.python.setAccessibleName('达芬奇兼容 Python 解释器路径')
        self.sdk = QLineEdit(str(window.settings.value('resolve/sdk', '')))
        self.sdk.setAccessibleName('达芬奇官方 SDK 目录')
        self.sdk.setPlaceholderText('留空：尝试标准安装位置')
        form = QFormLayout()
        self.browse_buttons = []
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        for label, field, callback in [('Python', self.python, self.choose_python),
                                       ('SDK 目录', self.sdk, self.choose_sdk)]:
            field.setMinimumWidth(0)
            field.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            field.setToolTip(field.text())
            field.textChanged.connect(field.setToolTip)
            row = QWidget()
            horizontal = QHBoxLayout(row)
            horizontal.setContentsMargins(0, 0, 0, 0)
            horizontal.addWidget(field, 1)
            button = QPushButton('选择')
            button.clicked.connect(callback)
            self.browse_buttons.append(button)
            horizontal.addWidget(button)
            form.addRow(label, row)
        form_layout.addLayout(form)
        self.probe_button = QPushButton('检测达芬奇连接')
        self.probe_button.clicked.connect(self.probe)
        self.render_button = QPushButton('达芬奇基础版导出 MP4')
        self.render_button.clicked.connect(self.render)
        self.cancel_button = QPushButton('取消达芬奇任务')
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.hide()
        self.preview_button = QPushButton('在灵剪内预览输出')
        self.preview_button.clicked.connect(self.preview)
        self.preview_button.hide()
        self.progress = QProgressBar()
        self.progress.hide()
        self.status = QLabel('尚未检测；普通本地导出仍可独立使用。')
        self.status.setWordWrap(True)
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        for widget in (self.probe_button, self.render_button, self.cancel_button, self.progress,
                       self.preview_button, self.status):
            widget.setMinimumWidth(0)
            if isinstance(widget, QPushButton):
                widget.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
                widget.setToolTip(widget.text())
            form_layout.addWidget(widget)
        target = window.render_status.parentWidget().layout()
        target.insertWidget(target.indexOf(window.render_status) + 1, group)
        self.group = group
        self.toggle = toggle

    @property
    def busy(self):
        return self.thread is not None

    @property
    def rendering(self):
        return self.busy and self.thread.request['operation'] == 'render_basic'

    def configuration(self):
        return self.python.text().strip(), self.sdk.text().strip()

    def choose_python(self):
        path, _ = QFileDialog.getOpenFileName(self.window, '选择兼容的 Python 解释器')
        if path:
            self.python.setText(path)

    def choose_sdk(self):
        path = QFileDialog.getExistingDirectory(self.window, '选择达芬奇 Developer/Scripting 目录')
        if path:
            self.sdk.setText(path)

    def start(self, request):
        if self.busy:
            self.status.setText('达芬奇任务仍在运行，请等待完成。')
            return False
        python, sdk = self.configuration()
        if not Path(python).is_file():
            self.status.setText('请选择本机兼容的 Python 解释器。')
            return False
        self.window.settings.setValue('resolve/python', python)
        self.window.settings.setValue('resolve/sdk', sdk)
        self.ready = False
        self.probe_button.setEnabled(False)
        self.render_button.setEnabled(False)
        self.python.setEnabled(False)
        self.sdk.setEnabled(False)
        for button in self.browse_buttons:
            button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.cancel_button.setVisible(request['operation'] == 'render_basic')
        self.cancel_button.setEnabled(True)
        try:
            thread = ResolveThread(python, sdk, request, self.ffmpeg, self)
            self.thread = self.window.resolve_thread = thread
            thread.finished.connect(self.finished)
            thread.start()
        except Exception as exc:
            thread = self.thread
            self.thread = self.window.resolve_thread = None
            if thread is not None:
                thread.deleteLater()
            self.restore_controls()
            self.status.setText('无法启动达芬奇任务：' + str(exc))
            return False
        return True

    def probe(self):
        if self.start({'version': 1, 'operation': 'probe'}):
            self.status.setText('正在检测本机达芬奇接口…')

    def render(self):
        if self.busy:
            return False
        if self.window.export_controller.busy:
            self.status.setText('本地导出仍在运行，请等待完成或取消。')
            return False
        if not self.ready or self.checked_configuration != self.configuration():
            self.status.setText('请先检测连接；达芬奇需停留在项目管理器。')
            return False
        window = self.window
        path, _ = QFileDialog.getSaveFileName(window, '达芬奇基础剪辑输出',
                                             window.title.text() + '-resolve.mp4', 'MP4 (*.mp4)')
        if not path:
            return False
        if Path(path).suffix.lower() != '.mp4':
            path += '.mp4'
        try:
            report = window.preflight(path)
            warnings = transfer_warnings(window.project, window.timeline.audio_muted)
            text = '将新建独立达芬奇工程，仅执行素材入出点、顺序拼接与原声导出。\n'
            if warnings:
                text += '\n本次不会迁移：' + '、'.join(warnings) + '。\n'
            if report['warnings']:
                text += '\n质量提醒：\n- ' + '\n- '.join(report['warnings']) + '\n'
            text += '\n若需保留全部现有效果，请使用本地导出。继续导出基础版吗？'
            if QMessageBox.question(window, '确认基础版能力范围', text,
                                    QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
                return False
            width, height, _ = window.preset.currentData()
            request = project_request(window.project, path, width, height, uuid.uuid4().hex)
            if self.start(request):
                self.status.setText('达芬奇正在执行基础剪辑与渲染，灵剪会校验后发布视频。')
                return True
        except Exception as exc:
            window.alert('无法开始达芬奇任务：' + str(exc), True)
        return False

    def cancel(self):
        if not self.busy:
            return
        try:
            self.thread.cancel()
            self.cancel_button.setEnabled(False)
            self.status.setText('已请求取消；需等待达芬奇接口返回，旧输出保持不变。')
        except OSError as exc:
            self.status.setText('取消请求失败：' + str(exc))

    def restore_controls(self):
        for widget in (self.probe_button, self.render_button, self.python, self.sdk, *self.browse_buttons):
            widget.setEnabled(True)
        self.progress.hide()
        self.cancel_button.hide()

    def preview(self):
        if not self.last_output or not Path(self.last_output).is_file():
            self.status.setText('输出文件不可用，请先成功导出。')
            return
        if self.preview_dialog is not None:
            self.preview_dialog.raise_()
            self.preview_dialog.activateWindow()
            return
        dialog = QDialog(self.window)
        dialog.setWindowTitle('灵剪 · 达芬奇输出预览')
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.resize(900, 580)
        layout = QVBoxLayout(dialog)
        video = QVideoWidget()
        layout.addWidget(video, 1)
        player = QMediaPlayer(dialog)
        audio = QAudioOutput(dialog)
        player.setAudioOutput(audio)
        player.setVideoOutput(video)
        slider = QSlider(Qt.Horizontal)
        slider.setAccessibleName('达芬奇输出播放位置')
        player.durationChanged.connect(lambda duration: slider.setRange(0, duration))
        player.positionChanged.connect(slider.setValue)
        slider.sliderMoved.connect(player.setPosition)
        layout.addWidget(slider)
        play = QPushButton('播放 / 暂停')
        play.clicked.connect(lambda: player.pause() if player.playbackState() == QMediaPlayer.PlayingState
                             else player.play())
        layout.addWidget(play)
        message = QLabel(self.last_output)
        message.setWordWrap(True)
        player.errorOccurred.connect(lambda error, text: message.setText('预览失败：' + text))
        layout.addWidget(message)
        def closed(_):
            player.stop()
            player.setSource(QUrl())
            self.preview_dialog = None
        dialog.finished.connect(closed)
        self.preview_dialog = dialog
        player.setSource(QUrl.fromLocalFile(self.last_output))
        dialog.show()
        player.play()

    def finished(self):
        thread = self.thread
        if thread is None:
            return
        result = thread.job_result or {'status': 'uncertain', 'message': '未收到达芬奇任务结果。'}
        self.thread = self.window.resolve_thread = None
        thread.deleteLater()
        self.restore_controls()
        status = result.get('status')
        if status == 'connected':
            self.ready = bool(result.get('ready'))
            self.checked_configuration = (thread.python, thread.sdk)
            self.status.setText(f"已连接 {result.get('product', '')} {result.get('version', '')}。" +
                                ('可以试用基础剪辑。' if self.ready else '请先保存并关闭达芬奇当前工程。'))
        elif status == 'completed':
            self.last_output = result['output']
            self.preview_button.show()
            self.status.setText('达芬奇基础版导出已校验：' + result['output'])
            self.window.alert(self.status.text())
        else:
            self.status.setText(result.get('message', '达芬奇任务失败。'))
            if status == 'uncertain':
                self.status.setText(self.status.text() + '\n请检查达芬奇任务后重新检测连接；不要直接重试。')
                if result.get('job_dir'):
                    self.status.setText(self.status.text() + '\n保留的任务目录：' + result['job_dir'])
        if result.get('cleanup_warning'):
            self.status.setText(self.status.text() + '\n' + result['cleanup_warning'])
