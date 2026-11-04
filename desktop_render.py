"""Qt bridge shared by export and effect-preview rendering."""
import threading

from PySide6.QtCore import QThread, Signal

from core import validate_rendered_mp4
from render_jobs import RenderResult, render_atomic


class RenderThread(QThread):
    progress = Signal(int)
    done = Signal(bool, str)

    def __init__(self, command, duration, parent=None, *, validator=validate_rendered_mp4):
        super().__init__(parent)
        self.command = list(command)
        self.duration = duration
        self.validator = validator
        self.cancel_event = threading.Event()
        self.job_result = None

    def cancel(self):
        self.cancel_event.set()

    def run(self):
        try:
            self.job_result = render_atomic(
                self.command, self.command[-1], self.duration,
                validator=lambda path: self.validator(self.command[0], path),
                cancel=self.cancel_event, progress=self.progress.emit)
        except Exception as exc:
            # Keep the Qt boundary total, including unexpected cleanup errors.
            self.job_result = RenderResult('failed', str(exc), self.command[-1] if self.command else '')
        self.done.emit(self.job_result.ok, self.job_result.message)
