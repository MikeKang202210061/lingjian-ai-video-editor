"""Serialize media reads and proxies, retaining workers until finished."""
import os
from collections import deque
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from core import AUDIO_EXT, VIDEO_EXT
from desktop_media_workers import MediaAnalyzeThread, ProxyThread
from render_jobs import RenderResult


def media_key(path):
    return os.path.normcase(str(Path(path).expanduser().resolve()))


class ImportController(QObject):
    def __init__(self, window, ffmpeg):
        super().__init__(window)
        self.window, self.ffmpeg = window, ffmpeg
        self.queued = deque()
        self.queued_keys = set()
        self.active = set()
        self.received = set()
        self.thread = self.proxy_worker = None
        self.issues = deque(maxlen=50)
        self.issue_count = 0
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setAccessibleName('素材数量和导入状态')
        self.details = QPushButton('查看导入问题')
        self.details.setAccessibleName('查看素材读取和代理问题记录')
        self.details.clicked.connect(self.show_issues)
        layout = window.media.parentWidget().layout()
        position = layout.indexOf(window.media)
        layout.insertWidget(position, self.status)
        layout.insertWidget(position + 1, self.details)
        window.search.setAccessibleName('搜索视频素材')
        self.refresh()

    @property
    def busy(self):
        return bool(self.thread is not None or self.queued or self.proxy_worker is not None
                    or self.window.proxy_queue)

    def reset(self):
        if self.busy:
            raise RuntimeError('Cannot reset an active import queue')
        self.active.clear()
        self.received.clear()
        self.queued_keys.clear()
        self.issues.clear()
        self.issue_count = 0
        self.refresh()

    def record_issue(self, path, message):
        self.issue_count += 1
        self.issues.append(f'{path}\n{message}')
        self.refresh()

    def enqueue(self, paths):
        known = {media_key(path) for path in self.window.metas}
        audio = None
        skipped = 0
        for raw in paths:
            try:
                path = str(Path(raw).expanduser().resolve())
                suffix = Path(path).suffix.lower()
                if suffix in AUDIO_EXT:
                    audio = path
                    continue
                if suffix not in VIDEO_EXT:
                    self.record_issue(str(raw), '不支持的素材格式；请选择视频或音频文件。')
                    continue
                key = media_key(path)
                if key in known or key in self.active or key in self.queued_keys:
                    skipped += 1
                    continue
                self.queued.append(path)
                self.queued_keys.add(key)
            except (OSError, ValueError, RuntimeError) as exc:
                self.record_issue(str(raw), str(exc))
        if audio is not None:
            self.window.project.bgm = audio
            self.window.bgm.setText(Path(audio).name)
            self.window.update_document_state()
        self.start_next()
        self.refresh()
        if skipped:
            self.window.statusBar().showMessage(f'已跳过 {skipped} 个重复或正在读取的视频。', 5000)

    def start_next(self):
        if self.thread is not None or not self.queued:
            return
        paths = list(self.queued)
        self.queued.clear()
        self.queued_keys.clear()
        self.active = {media_key(path) for path in paths}
        self.received.clear()
        try:
            thread = MediaAnalyzeThread(paths, self.window.cache / 'thumbs', self.ffmpeg, self)
            self.thread = self.window.analyze_thread = thread
            thread.item.connect(self.media_ready)
            thread.failed.connect(self.media_failed)
            thread.finished.connect(self.finished)
            thread.start()
        except Exception as exc:
            thread = self.thread
            self.thread = self.window.analyze_thread = None
            if thread is not None:
                thread.deleteLater()
            for path in paths:
                self.record_issue(path, '无法启动素材读取：' + str(exc))
            self.active.clear()
        self.refresh()

    def media_ready(self, meta):
        self.received.add(media_key(meta['path']))
        try:
            self.window.media_ready(meta)
        except Exception as exc:
            self.record_issue(meta['path'], '无法更新素材：' + str(exc))
        self.refresh()

    def media_failed(self, path, message):
        self.received.add(media_key(path))
        self.record_issue(path, message)

    def finished(self):
        thread = self.thread
        if thread is None:
            return
        for path in self.active - self.received:
            self.record_issue(path, '读取任务结束，但未返回该文件的结果；可以重新导入。')
        self.thread = self.window.analyze_thread = None
        self.active.clear()
        self.received.clear()
        thread.deleteLater()
        self.start_next()
        if self.thread is None:
            self.window.notice.setText(f'素材读取完成 · 共 {len(self.window.metas)} 个视频。'
                                       '可双击素材预览，或继续导入。')
        self.start_next_proxy()
        self.refresh()

    def start_next_proxy(self):
        if self.proxy_worker is not None or not self.window.proxy_queue:
            return
        source, output = self.window.proxy_queue.pop(0)
        try:
            thread = ProxyThread(source, output, self.ffmpeg, self)
            self.proxy_worker = self.window.proxy_thread = thread
            thread.finished.connect(self.proxy_finished)
            thread.start()
        except Exception as exc:
            thread = self.proxy_worker
            self.proxy_worker = self.window.proxy_thread = None
            if thread is not None:
                thread.deleteLater()
            self.record_issue(source, '无法启动流畅代理：' + str(exc))
            self.window.proxy_done(source, output, False, str(exc))
            # A missing worker facility must not recurse over a large queue.
            for pending, target in self.window.proxy_queue:
                self.record_issue(pending, '代理任务未启动；仍可使用原素材。')
                self.window.proxy_done(pending, target, False, '代理任务未启动')
            self.window.proxy_queue.clear()
        self.refresh()

    def proxy_finished(self):
        thread = self.proxy_worker
        if thread is None:
            return
        result = thread.job_result or RenderResult('failed', '代理任务没有返回结果。', thread.output)
        try:
            self.window.proxy_done(thread.source, thread.output, result.ok, result.message)
            if not result.ok:
                self.record_issue(thread.source, '流畅代理失败；仍可使用原素材。\n' + result.message)
        except Exception as exc:
            self.record_issue(thread.source, '更新代理状态失败：' + str(exc))
        finally:
            self.proxy_worker = self.window.proxy_thread = None
            thread.deleteLater()
            self.start_next_proxy()
            self.refresh()

    def refresh(self):
        window = self.window
        total = window.media.count()
        visible = (sum(not window.media.item(i).isHidden() for i in range(total))
                   if window.search.text() else total)
        pending = len(self.active - self.received) + len(self.queued)
        text = f'显示 {visible} / {total} 个视频'
        if self.thread is not None or self.queued:
            text += f' · 待读取 {pending}'
        proxies = len(window.proxy_queue) + int(self.proxy_worker is not None)
        if proxies:
            text += f' · 代理 {proxies}'
        if self.issue_count:
            text += f' · 问题 {self.issue_count}'
        self.status.setText(text)
        self.details.setText(f'查看导入问题（{self.issue_count}）')
        self.details.setVisible(bool(self.issue_count))
        self.status.setToolTip('重复的视频路径会跳过；再次选择素材会排队。'
                               '音频作为背景音乐，不列入视频数量。')

    def show_issues(self):
        dialog = QDialog(self.window)
        dialog.setWindowTitle('素材读取和代理问题记录')
        dialog.resize(620, 380)
        layout = QVBoxLayout(dialog)
        note = QLabel(f'累计 {self.issue_count} 项问题，保留最近 {len(self.issues)} 项。'
                      '可复制详情；修复后重新导入失败的视频。')
        note.setWordWrap(True)
        layout.addWidget(note)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setAccessibleName('可复制的素材问题详情')
        text.setPlainText('\n\n'.join(self.issues))
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()
