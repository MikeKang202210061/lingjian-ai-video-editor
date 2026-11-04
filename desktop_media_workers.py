"""Media I/O workers; widgets and queue ownership stay on the GUI thread."""
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from core import build_proxy_command, probe_media, thumbnail
from render_jobs import RenderResult, render_atomic


class MediaAnalyzeThread(QThread):
    item = Signal(object)
    failed = Signal(str, str)

    def __init__(self, paths, cache, ffmpeg, parent=None):
        super().__init__(parent)
        self.paths = list(paths)
        self.cache = Path(cache)
        self.ffmpeg = ffmpeg

    def run(self):
        for path in self.paths:
            try:
                self.cache.mkdir(parents=True, exist_ok=True)
                meta = probe_media(self.ffmpeg, path)
                thumb = str(self.cache / (Path(path).stem + '-' + str(abs(hash(path))) + '.jpg'))
                if not Path(thumb).exists():
                    thumbnail(self.ffmpeg, path, thumb, min(2, meta['duration'] * .25))
                meta['thumbnail'] = thumb
                self.item.emit(meta)
            except Exception as exc:
                self.failed.emit(path, str(exc))


class ProxyThread(QThread):
    def __init__(self, source, output, ffmpeg, parent=None):
        super().__init__(parent)
        self.source, self.output, self.ffmpeg = source, output, ffmpeg
        self.job_result = None

    def run(self):
        try:
            Path(self.output).parent.mkdir(parents=True, exist_ok=True)
            command = build_proxy_command(self.ffmpeg, self.source, self.output)
            self.job_result = render_atomic(command, self.output, 1,
                                            validator=lambda path: probe_media(self.ffmpeg, path))
        except Exception as exc:
            self.job_result = RenderResult('failed', str(exc), self.output)
