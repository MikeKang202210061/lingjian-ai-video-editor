"""Cancellable render processes and validated publication, independent of Qt."""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class RenderResult:
    status: str
    message: str
    output: str
    metadata: dict = field(default_factory=dict)

    @property
    def ok(self):
        return self.status == 'completed'


def ensure_output_is_safe(command: list[str], output: str) -> None:
    target = Path(output).resolve()
    if not command or Path(command[-1]).resolve() != target:
        raise ValueError('Render command does not end with the expected output path')
    for index, arg in enumerate(command[:-1]):
        if arg != '-i' or index + 1 >= len(command):
            continue
        source = Path(command[index + 1])
        same = source.resolve() == target
        if not same and source.exists() and target.exists():
            same = os.path.samefile(source, target)
        if same:
            raise ValueError('导出位置不能覆盖输入素材，请选择其他文件名。')


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def render_atomic(command: list[str], output: str, duration: float, *,
                  validator: Callable[[str], dict], cancel: threading.Event | None = None,
                  progress: Callable[[int], None] | None = None) -> RenderResult:
    """Keep an existing output intact until encoding AND validation succeed.

    The render command must have exactly one output, as its final argument.
    stderr is continuously drained while the worker polls cancellation, including
    silent processes. No GUI calls or QThread termination are used here.
    """
    cancel = cancel if cancel is not None else threading.Event()
    temporary = None
    process = None
    reader = None
    tail = deque(maxlen=30)
    reader_errors = []
    latest = -1
    result = None

    def outcome(status, message, metadata=None):
        nonlocal result
        result = RenderResult(status, message, output, metadata or {})
        return result

    def notify(value):
        nonlocal latest
        value = max(latest, min(100, value))
        if value == latest:
            return
        latest = value
        if progress:
            try:
                progress(value)
            except Exception:
                pass  # A display callback must never stop pipe drainage.

    try:
        ensure_output_is_safe(command, output)
        if cancel.is_set():
            return outcome('cancelled', '导出已取消，旧输出文件未改变。')
        target = Path(output)
        descriptor, name = tempfile.mkstemp(prefix='.' + target.stem + '-',
                                             suffix='.part.mp4', dir=target.parent)
        os.close(descriptor)
        temporary = Path(name)
        actual = [*command[:-1], str(temporary)]
        process = subprocess.Popen(
            actual, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
            creationflags=0x08000000 if os.name == 'nt' else 0,
        )

        def drain():
            try:
                for line in process.stderr:
                    tail.append(line[-2000:])
                    match = re.search(r'time=(\d+):(\d+):(\d+(?:\.\d+)?)', line)
                    if match:
                        seconds = int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])
                        notify(min(99, max(0, int(seconds / max(.1, duration) * 100))))
            except Exception as exc:
                reader_errors.append(exc)

        reader = threading.Thread(target=drain, name='lingjian-render-log', daemon=True)
        reader.start()
        while process.poll() is None:
            if cancel.wait(.1):
                _stop(process)
                break
            if reader_errors:
                _stop(process)
                raise RuntimeError('Cannot read render process output: ' + str(reader_errors[0]))
        process.wait()
        reader.join(timeout=5)
        if cancel.is_set():
            return outcome('cancelled', '导出已取消，旧输出文件未改变。')
        if process.returncode:
            return outcome('failed', ''.join(tail)[-1600:] or
                           f'Render process exited with code {process.returncode}')
        if reader_errors or reader.is_alive():
            raise RuntimeError('Render process log did not finish cleanly')
        metadata = validator(str(temporary))
        if cancel.is_set():
            return outcome('cancelled', '导出已取消，旧输出文件未改变。')
        os.replace(temporary, target)
        notify(100)
        return outcome('completed', '导出完成并通过校验。', metadata)
    except Exception as exc:
        return outcome('failed', str(exc))
    finally:
        cleanup_errors = []
        try:
            if process is not None:
                if process.poll() is None:
                    _stop(process)
                if reader is not None:
                    reader.join(timeout=5)
                if process.stderr is not None and (reader is None or not reader.is_alive()):
                    process.stderr.close()
        except Exception as exc:
            cleanup_errors.append(str(exc))
        try:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        except OSError as exc:
            cleanup_errors.append(f'{temporary}: {exc}')
        if cleanup_errors and result is not None:
            result.message += '\n临时任务清理未完成：' + '; '.join(cleanup_errors)
