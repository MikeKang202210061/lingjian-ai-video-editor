"""Bounded, scene-aware contact sheets with decoded presentation timestamps.

Only derived JPEGs leave this module. FFmpeg scans a 64x36 proxy locally;
Pillow (already an application dependency) lays out the selected frames.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
import warnings
from pathlib import Path

VERSION = 1
OVERVIEW_FRAMES = 24
FRAMES_PER_SHEET = 8
SCAN_FPS = 4


def media_key(source: str, duration: float) -> str:
    path = Path(source).resolve()
    stat = path.stat()
    identity = [str(path), stat.st_size, stat.st_mtime_ns, duration, VERSION]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24]


def _run(ffmpeg: str, args: list[str], timeout: float = 180):
    result = subprocess.run(
        [ffmpeg, '-nostdin', '-hide_banner', *args], capture_output=True,
        text=True, encoding='utf-8', errors='replace', timeout=timeout,
        creationflags=0x08000000 if os.name == 'nt' else 0,
    )
    if result.returncode:
        raise RuntimeError('FFmpeg visual analysis failed: ' + result.stderr[-1200:])
    return result


def scan_changes(ffmpeg: str, source: str) -> list[tuple[float, float]]:
    """Mean Y/U/V differences, 0..255, at four samples per second."""
    vf = (f'setpts=PTS-STARTPTS,fps={SCAN_FPS},scale=64:36,format=yuv444p,'
          'signalstats,metadata=mode=print:file=-')
    result = _run(ffmpeg, ['-v', 'error', '-i', source, '-map', '0:v:0',
                           '-vf', vf, '-an', '-f', 'null', '-'])
    samples = []
    for block in re.split(r'(?=frame:\s*\d+\s+pts:)', result.stdout):
        stamp = re.search(r'pts_time:([0-9.eE+-]+)', block)
        diffs = [re.search(r'lavfi\.signalstats\.' + channel + r'DIF=([0-9.eE+-]+)', block)
                 for channel in 'YUV']
        if stamp and all(diffs):
            samples.append((float(stamp[1]), sum(float(m[1]) for m in diffs) / 3))
    if not samples:
        raise RuntimeError('FFmpeg did not return frame-change measurements')
    return samples


def select_samples(duration: float, changes: list[tuple[float, float]],
                   max_frames: int = OVERVIEW_FRAMES) -> list[dict]:
    """Keep timeline coverage and allocate the rest across changing regions."""
    if not math.isfinite(duration) or duration <= 0 or not 2 <= max_frames <= 72:
        raise ValueError('Invalid duration or frame budget')
    last = max(0.0, duration - min(.5, duration / 4))
    anchors = min(8, max_frames, max(2, math.ceil(duration / 2)))
    selected = [{'time': last * i / (anchors - 1), 'kind': 'coverage', 'score': 0.0}
                for i in range(anchors)]
    events = []
    accumulated = 0.0
    previous = 0.0
    for stamp, score in sorted(changes):
        if not (math.isfinite(stamp) and math.isfinite(score) and 0 <= stamp <= last):
            continue
        accumulated += max(0, score) * min(1.0, max(0, stamp - previous))
        previous = stamp
        if score >= 12 or accumulated >= 10:
            events.append({'time': stamp, 'kind': 'cut' if score >= 12 else 'motion',
                           'score': max(score, accumulated)})
            accumulated = 0.0
    # Round-robin across temporal bins prevents a busy opening from using the budget.
    bins = [[] for _ in range(anchors)]
    for event in events:
        bins[min(anchors - 1, int(event['time'] / duration * anchors))].append(event)
    for group in bins:
        group.sort(key=lambda item: (-item['score'], item['time']))
    separation = min(.5, duration / (max_frames * 2))
    for rank in range(max((len(group) for group in bins), default=0)):
        for group in bins:
            if rank >= len(group):
                continue
            event = group[rank]
            near = [i for i, old in enumerate(selected)
                    if abs(old['time'] - event['time']) < separation]
            if near:
                if selected[near[0]]['kind'] == 'coverage':
                    selected[near[0]] = event
            elif len(selected) < max_frames:
                selected.append(event)
    return sorted(selected, key=lambda item: item['time'])


def _capture(ffmpeg: str, source: str, sample: dict, output: Path,
             duration: float) -> dict:
    # Preserve the decoded PTS and subtract the input's start offset. Adding a
    # seek time to reset PTS loses precision when FFmpeg rounds the seek ticks.
    requested = sample['time']
    for seek in dict.fromkeys([requested, max(0, requested - .5), max(0, requested - 2)]):
        output.unlink(missing_ok=True)
        result = _run(ffmpeg, ['-v', 'info', '-y', '-copyts', '-ss', f'{seek:.9f}', '-i', source,
                               '-map', '0:v:0', '-vf',
                               'scale=320:180:force_original_aspect_ratio=decrease,'
                               'pad=320:180:(ow-iw)/2:(oh-ih)/2,showinfo',
                               '-frames:v', '1', '-update', '1', str(output)], timeout=60)
        stamps = re.findall(r'\bn:\s*\d+\s+pts:\s*(-?\d+)\s+pts_time:([0-9.eE+-]+)', result.stderr)
        time_base = re.search(r'config in time_base:\s*(\d+)/(\d+)', result.stderr)
        origin = re.search(r'Duration:[^\r\n]*?start:\s*(-?[0-9.]+)', result.stderr)
        if output.exists() and output.stat().st_size and stamps and origin:
            pts = (int(stamps[0][0]) * int(time_base[1]) / int(time_base[2])
                   if time_base else float(stamps[0][1]))
            actual = pts - float(origin[1])
            if math.isfinite(actual) and -.001 <= actual <= duration + .001:
                return {**sample, 'requested_time': requested, 'time': round(max(0, actual), 6)}
    raise RuntimeError(f'Cannot decode a frame near {requested:.3f}s')


def _cached(cache: Path, duration: float) -> list[dict] | None:
    try:
        manifest = json.loads((cache / 'manifest.json').read_text(encoding='utf-8'))
        if manifest['version'] != VERSION or manifest['duration'] != duration:
            return None
        sheets = manifest['sheets']
        if not sheets or len(sheets) > 9:
            return None
        out = []
        for sheet in sheets:
            name = sheet['path']
            frames = sheet['frames']
            if (Path(name).name != name or not frames or len(frames) > FRAMES_PER_SHEET
                    or not (cache / name).is_file() or not (cache / name).stat().st_size
                    or any(not math.isfinite(f['time']) or not 0 <= f['time'] <= duration
                           for f in frames)):
                return None
            out.append({**sheet, 'path': str(cache / name)})
        return out
    except (OSError, ValueError, KeyError, TypeError):
        return None


def build_contact_sheets(ffmpeg: str, source: str, folder: str, duration: float,
                         *, samples: list[dict] | None = None) -> list[dict]:
    """Overview: <=24 frames / 3 sheets. Refinement: <=8 frames / 1 sheet.

    Each result carries its real decoded times in exactly the same order as
    the visible labels. A sidecar manifest makes that mapping inspectable.
    """
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('Invalid video duration')
    fingerprint = media_key(source, duration)
    phase = 'overview' if samples is None else hashlib.sha256(
        json.dumps(samples, sort_keys=True).encode()).hexdigest()[:16]
    cache = Path(folder) / f'visual-v{VERSION}-{fingerprint}-{phase}'
    cached = _cached(cache, duration)
    if cached:
        return cached
    if samples is None:
        try:
            changes = scan_changes(ffmpeg, source)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            warnings.warn(f'Visual change scan unavailable; using timeline coverage: {exc}',
                          RuntimeWarning)
            changes = []
        samples = select_samples(duration, changes)
    if not samples or len(samples) > OVERVIEW_FRAMES:
        raise ValueError('Invalid visual sampling budget')
    if any(not math.isfinite(s['time']) or not 0 <= s['time'] < duration for s in samples):
        raise ValueError('Sample outside video bounds')
    from PIL import Image, ImageDraw

    cache.mkdir(parents=True, exist_ok=True)
    sheets = []
    with tempfile.TemporaryDirectory(prefix='build-', dir=cache) as scratch:
        scratch = Path(scratch)
        decoded = []
        for index, sample in enumerate(samples):
            frame = scratch / f'frame-{index:02d}.png'
            metadata = _capture(ffmpeg, source, sample, frame, duration)
            # Several nearby requests can resolve to the same low-FPS frame.
            if any(abs(old['time'] - metadata['time']) < .00001 for old, _ in decoded):
                continue
            decoded.append((metadata, frame))
        for offset in range(0, len(decoded), FRAMES_PER_SHEET):
            batch = decoded[offset:offset + FRAMES_PER_SHEET]
            columns = min(4, len(batch))
            rows = math.ceil(len(batch) / columns)
            canvas = Image.new('RGB', (columns * 240, rows * 156), '#101820')
            draw = ImageDraw.Draw(canvas)
            for index, (metadata, frame) in enumerate(batch):
                x, y = (index % columns) * 240, (index // columns) * 156
                with Image.open(frame) as image:
                    canvas.paste(image.resize((240, 135)), (x, y))
                draw.text((x + 4, y + 138), f"{index + 1} | {metadata['time']:.3f}s",
                          fill='white')
            name = f'sheet-{len(sheets) + 1:02d}.jpg'
            canvas.save(scratch / name, 'JPEG', quality=85)
            sheets.append({'path': name, 'frames': [metadata for metadata, _ in batch]})
        if not sheets or media_key(source, duration) != fingerprint:
            raise RuntimeError('Source media changed during visual analysis; please retry')
        for sheet in sheets:
            os.replace(scratch / sheet['path'], cache / sheet['path'])
        manifest = {'version': VERSION, 'duration': duration, 'sheets': sheets}
        temp_manifest = scratch / 'manifest.json'
        temp_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temp_manifest, cache / 'manifest.json')
    return [{**sheet, 'path': str(cache / sheet['path'])} for sheet in sheets]
