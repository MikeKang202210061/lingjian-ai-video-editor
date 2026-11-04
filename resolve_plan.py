"""Restricted JSON contract for an optional Resolve executor. No generated code."""
import math
import os
import re
from pathlib import Path

PROTOCOL = 1


def number(value, name, low, high):
    if isinstance(value, bool):
        raise ValueError(name + ' must be a number')
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(name + ' is out of range')
    return value


def validate_request(data):
    if (not isinstance(data, dict) or type(data.get('version')) is not int
            or data.get('version') != PROTOCOL):
        raise ValueError('Unsupported Resolve request version')
    operation = data.get('operation')
    if operation == 'probe':
        if set(data) != {'version', 'operation'}:
            raise ValueError('Unexpected probe fields')
        return data
    allowed = {'version', 'operation', 'job_id', 'title', 'clips', 'width', 'height',
               'output', 'job_dir', 'acknowledge_basic_only'}
    if operation != 'render_basic' or set(data) != allowed:
        raise ValueError('Only probe and render_basic are supported; no scripts or arbitrary commands')
    if data['acknowledge_basic_only'] is not True:
        raise ValueError('Basic-only transfer must be explicitly acknowledged')
    if not re.fullmatch(r'[0-9a-f]{32}', str(data['job_id'])):
        raise ValueError('Invalid job identity')
    if not isinstance(data['title'], str) or len(data['title']) > 200:
        raise ValueError('Invalid title')
    for key in ('width', 'height'):
        value = number(data[key], key, 128, 4096)
        if value != int(value) or int(value) % 2:
            raise ValueError('Output dimensions must be even integers')
    output = Path(data['output'])
    job_dir = Path(data['job_dir'])
    if not output.is_absolute() or output.suffix.lower() != '.mp4':
        raise ValueError('Output must be an absolute MP4 path')
    if (not job_dir.is_absolute() or job_dir.is_symlink()
            or job_dir.parent.resolve() != output.parent.resolve()
            or job_dir.name != '.lingjian-resolve-' + data['job_id']):
        raise ValueError('Invalid adjacent job directory')
    clips = data['clips']
    if not isinstance(clips, list) or not 1 <= len(clips) <= 500:
        raise ValueError('Resolve basic mode supports 1–500 clips')
    total = 0
    for clip in clips:
        if not isinstance(clip, dict) or set(clip) != {'path', 'start', 'end'}:
            raise ValueError('Invalid clip instruction')
        source = Path(clip['path'])
        if not source.is_absolute() or not source.is_file():
            raise ValueError('Missing source: ' + str(source))
        start = number(clip['start'], 'start', 0, 86400)
        end = number(clip['end'], 'end', 0, 86400)
        if end <= start:
            raise ValueError('Clip end must follow start')
        total += end - start
        if (source.resolve() == output.resolve() or
                (output.exists() and os.path.samefile(source, output))):
            raise ValueError('导出位置不能覆盖输入素材。')
    if total > 7200:
        raise ValueError('Resolve basic mode is limited to two hours')
    return data


def transfer_warnings(project, audio_muted=False):
    """Never silently pretend unsupported editor features were transferred."""
    warnings = []
    if project.overlays:
        warnings.append('多轨叠加')
    if project.bgm:
        warnings.append('背景音乐及闪避')
    if project.sfx:
        warnings.append('独立音效')
    if audio_muted or any(clip.volume != 1 for clip in project.clips):
        warnings.append('静音及音量调整')
    if any(clip.caption or clip.title_effect for clip in project.clips):
        warnings.append('字幕及标题')
    if any(clip.transition not in ('none', 'cut', '') for clip in project.clips[1:]):
        warnings.append('转场')
    if any(clip.mask_shape != 'none' or clip.motion_effect != 'none' or clip.person_effect_path
           for clip in project.clips):
        warnings.append('蒙版、运镜或人物效果')
    return warnings


def project_request(project, output, width, height, job_id):
    output = Path(output).resolve()
    request = {'version': PROTOCOL, 'operation': 'render_basic', 'job_id': job_id,
               'title': str(project.title)[:200], 'width': width, 'height': height,
               'output': str(output),
               'job_dir': str(output.parent / ('.lingjian-resolve-' + job_id)),
               'acknowledge_basic_only': True,
               'clips': [{'path': str(Path(c.path).resolve()), 'start': c.start, 'end': c.end}
                         for c in project.clips]}
    return validate_request(request)
