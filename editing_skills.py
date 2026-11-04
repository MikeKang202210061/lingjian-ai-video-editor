"""Repository-owned editing skills: instructions, bounded recipes and audit snapshots.

These are LingJian resources, not installed ChatGPT skills. Never execute skill text.
"""
import copy
import hashlib
import json
import math
import re
import sys
from pathlib import Path

CATALOG = {
    'hetongxue-inspired': '科技实验讲解 · 参考何同学',
    'mediastorm-inspired': '影像探索纪实 · 参考影视飓风',
}
ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'skills'


def skill_choices():
    return [('不使用 Skill', '')] + [(label, key) for key, label in CATALOG.items()]


def bounded(value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('Skill 参数越界')
    return value


def validate_profile(profile):
    expected = {'version', 'accent_spacing', 'caption_effect', 'accent_color', 'motion',
                'accent_events', 'sound', 'music', 'music_volume', 'zoom', 'saturation'}
    if not isinstance(profile, dict) or set(profile) != expected or type(profile['version']) is not int or profile['version'] != 1:
        raise ValueError('不支持的 Skill 配方')
    bounded(profile['accent_spacing'], 6, 30)
    bounded(profile['music_volume'], 0, .25)
    bounded(profile['zoom'], 1, 1.12)
    bounded(profile['saturation'], .85, 1.15)
    if profile['caption_effect'] not in ('highlight', 'minimal') or profile['motion'] not in ('punch_in', 'slow_push'):
        raise ValueError('不支持的 Skill 特效')
    if not isinstance(profile['accent_color'], str) or not re.fullmatch(r'#[0-9A-Fa-f]{6}', profile['accent_color']):
        raise ValueError('Skill 颜色无效')
    if (not isinstance(profile['accent_events'], list) or not profile['accent_events']
            or any(x not in ('detail', 'reveal', 'resolution') for x in profile['accent_events'])):
        raise ValueError('Skill 事件无效')
    if profile['sound'] not in ('', 'magic_ding') or profile['music'] not in ('clean_focus', 'documentary_air'):
        raise ValueError('Skill 声音配方无效')
    return profile


def _digest(snapshot):
    payload = {k: snapshot[k] for k in ('id', 'name', 'instructions', 'profile')}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def validate_snapshot(snapshot):
    if not isinstance(snapshot, dict) or set(snapshot) != {'id', 'name', 'instructions', 'profile', 'sha256'}:
        raise ValueError('Skill 快照无效')
    if snapshot['id'] not in CATALOG or snapshot['name'] != CATALOG[snapshot['id']]:
        raise ValueError('未知 Skill')
    if not isinstance(snapshot['instructions'], str) or not 20 <= len(snapshot['instructions']) <= 16000:
        raise ValueError('Skill 指令无效')
    validate_profile(snapshot['profile'])
    if snapshot['sha256'] != _digest(snapshot):
        raise ValueError('Skill 快照校验失败')
    return snapshot


def load_skill(skill_id, root=None):
    if not skill_id:
        return {}
    if skill_id not in CATALOG:
        raise ValueError('未知剪辑 Skill')
    folder = (Path(root) if root is not None else ROOT) / skill_id
    try:
        markdown = (folder / 'SKILL.md').read_text(encoding='utf-8')
        raw = (folder / 'profile.json').read_text(encoding='utf-8')
        if len(markdown) > 16000 or len(raw) > 16000:
            raise ValueError('Skill 文件过大')
        header, instructions = markdown[4:].split('\n---\n', 1) if markdown.startswith('---\n') else ('', '')
        if not header.startswith('name: ' + skill_id + '\n') or '\ndescription: ' not in header:
            raise ValueError('Skill frontmatter 无效')
        snapshot = {'id': skill_id, 'name': CATALOG[skill_id], 'instructions': instructions.strip(),
                    'profile': json.loads(raw)}
        snapshot['sha256'] = _digest(snapshot)
        return copy.deepcopy(validate_snapshot(snapshot))
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError('无法加载剪辑 Skill，请检查源码包中的 skills 目录：' + str(exc)) from exc


def skill_prompt(snapshot):
    if not snapshot:
        return ''
    validate_snapshot(snapshot)
    return ('\n剪辑 Skill：' + snapshot['name'] +
            '\n用户事实与明确限制优先，其次使用本 Skill，最后参考通用导演风格。\n' + snapshot['instructions'])


def apply_editing_skill(plan, snapshot):
    """Compile the selected skill without inventing footage, facts or source ranges."""
    result = copy.deepcopy(plan)
    if not snapshot:
        return result
    validate_snapshot(snapshot)
    from creative_director import _event
    from edit_protocol import validate_edit_plan
    profile = snapshot['profile']
    cursor, last_accent = 0., -999.
    cues, events = [], []
    for index, shot in enumerate(result.get('decisions') or []):
        duration = float(shot['end']) - float(shot['start'])
        event = _event(shot)
        # Role/reason is supplied by analysis, not inferred as a visual fact here.
        accent = event in profile['accent_events'] and cursor - last_accent >= profile['accent_spacing']
        shot['transition'] = 'none'
        shot['motion_effect'] = profile['motion'] if accent else 'none'
        shot['caption_effect'] = profile['caption_effect'] if accent else 'clean'
        shot['caption_font'] = '微软雅黑'
        shot['caption_color'] = profile['accent_color'] if accent else '#FFFFFF'
        # Resolve uses static framing + a small saturation treatment, not local animation.
        shot['resolve_treatment'] = {'zoom': profile['zoom'] if accent else 1.,
                                     'saturation': profile['saturation']}
        if accent:
            last_accent = cursor
            if profile['sound']:
                cues.append({'effect_id': profile['sound'], 'start': round(cursor, 3), 'volume': .32,
                             'reason': 'Skill 重点事件：' + event})
        events.append({'ordinal': index, 'timeline_start': round(cursor, 3), 'event': event,
                       'accent': accent, 'motion': shot['motion_effect'], 'caption_effect': shot['caption_effect']})
        cursor += duration
    result['editing_skill'] = copy.deepcopy(snapshot)
    result['sound_cues'] = cues
    result['global_creative_direction'] = {
        'density': 'skill', 'minimum_accent_spacing': profile['accent_spacing'], 'events': events,
        'accent_count': sum(x['accent'] for x in events), 'music_id': profile['music'],
        'music_volume': profile['music_volume'], 'music_ducking': True, 'music_fade_in': 1., 'music_fade_out': 2.,
        'rule': 'Skill 只处理有依据的重点；保留原素材范围、正文顺序和原声，不编造字幕。',
    }
    result['skill_capabilities'] = {
        'local': ['入出点与顺序', '重点镜头运镜', '已有字幕强调', '内置音效/配乐（资源存在时）'],
        'resolve': ['入出点与顺序', '静态缩放构图', '轻量饱和度 CDL（实验）'],
        'unmapped': ['Resolve 字幕/关键帧/声音设计/Fusion 合成尚未映射；静态构图不等同动态运镜',
                     '三维、跟踪、实拍机械装置需要额外素材或人工制作'],
    }
    result['validation'] = validate_edit_plan(result)
    return result


def skill_summary(plan):
    skill = plan.get('editing_skill') or {}
    if not skill:
        return ''
    caps = plan.get('skill_capabilities') or {}
    return ('\n剪辑 Skill：' + skill['name'] + ' · v' + str(skill['profile']['version']) +
            ' · ' + skill['sha256'][:12] + '\n本地执行：' + '、'.join(caps.get('local', [])) +
            '\n达芬奇实验映射：' + '、'.join(caps.get('resolve', [])) +
            '\n待补能力：' + '；'.join(caps.get('unmapped', [])))
