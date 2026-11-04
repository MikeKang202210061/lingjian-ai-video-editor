"""Isolated Resolve SDK executor. Run by a configured local Python interpreter.

Only fixed operations from resolve_plan are accepted. No eval, shell commands,
model-generated Python, project deletion or access to an existing open project.
API contracts follow Blackmagic's installed Developer/Scripting/README.txt;
actual compatibility must be checked against the user's installed Resolve SDK.
"""
import importlib
import json
import math
import os
import sys
import time
from pathlib import Path

from resolve_plan import validate_request

RESULT_PREFIX = 'LJ_RESOLVE_RESULT '


def connect():
    roots = []
    if os.environ.get('RESOLVE_SCRIPT_API'):
        roots.append(Path(os.environ['RESOLVE_SCRIPT_API']))
    if sys.platform == 'win32':
        roots.append(Path(os.environ.get('PROGRAMDATA', r'C:\ProgramData')) /
                     'Blackmagic Design/DaVinci Resolve/Support/Developer/Scripting')
    elif sys.platform == 'darwin':
        roots.append(Path('/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting'))
    else:
        roots.append(Path('/opt/resolve/Developer/Scripting'))
    for root in roots:
        module_dir = root if (root / 'DaVinciResolveScript.py').is_file() else root / 'Modules'
        if (module_dir / 'DaVinciResolveScript.py').is_file():
            sys.path.insert(0, str(module_dir))
            break
    try:
        sdk = importlib.import_module('DaVinciResolveScript')
    except ImportError as exc:
        raise RuntimeError('未找到达芬奇官方 SDK。请选择兼容的 Python 和 Developer/Scripting 目录。') from exc
    resolve = sdk.scriptapp('Resolve')
    if resolve is None:
        raise RuntimeError('无法连接达芬奇。请先启动兼容的 Resolve Studio，并将外部脚本设为 Local。')
    return resolve


def require(value, message):
    if not value:
        raise RuntimeError(message)
    return value


def source_frames(item, clip):
    fps = float(item.GetClipProperty('FPS'))
    frames = float(item.GetClipProperty('Frames'))
    if not math.isfinite(fps) or not 1 <= fps <= 240 or not math.isfinite(frames) or frames < 1:
        raise ValueError('达芬奇没有返回有效的素材帧率/帧数。')
    first = math.floor(float(clip['start']) * fps + .5)
    stop = math.floor(float(clip['end']) * fps + .5)
    if first < 0 or stop <= first or stop > int(frames):
        raise ValueError('剪辑范围无法映射到素材帧边界：' + clip['path'])
    # Resolve clipInfo uses an inclusive final source frame.
    return first, stop - 1


def execute(request, resolve, *, sleep=time.sleep, clock=time.monotonic):
    validate_request(request)
    manager = require(resolve.GetProjectManager(), '达芬奇项目管理器不可用。')
    current = manager.GetCurrentProject()
    if request['operation'] == 'probe':
        return {'status': 'connected', 'product': str(resolve.GetProductName()),
                'version': str(resolve.GetVersionString()),
                'current_project': str(current.GetName()) if current else '',
                'ready': current is None}
    if current is not None:
        raise RuntimeError('实验后端要求达芬奇停留在项目管理器。请先保存并关闭现有工程；灵剪不会替你关闭它。')
    job_dir = Path(request['job_dir'])
    require(job_dir.is_dir(), '任务目录不存在。')
    cancel = lambda: (job_dir / 'cancel').exists()
    if cancel():
        return {'status': 'cancelled', 'message': '任务已取消。'}
    project = None
    render_started = False
    settled = True
    result = None
    name = 'LingJian_' + request['job_id']
    try:
        project = require(manager.CreateProject(name), '无法新建独立达芬奇工程。')
        for setting, value in [('timelineFrameRate', '30'),
                               ('timelineResolutionWidth', str(int(request['width']))),
                               ('timelineResolutionHeight', str(int(request['height'])))]:
            require(project.SetSetting(setting, value), '达芬奇拒绝工程设置：' + setting)
        pool = require(project.GetMediaPool(), '媒体池不可用。')
        items = {}
        for clip in request['clips']:
            if cancel():
                return {'status': 'cancelled', 'message': '导入期间已取消。', 'project': name}
            path = clip['path']
            if path not in items:
                imported = require(pool.ImportMedia([path]), '无法导入素材：' + path)
                if len(imported) != 1:
                    raise RuntimeError('素材未解析为单个视频：' + path)
                items[path] = imported[0]
        instructions = []
        for clip in request['clips']:
            item = items[clip['path']]
            start, end = source_frames(item, clip)
            instructions.append({'mediaPoolItem': item, 'startFrame': start, 'endFrame': end})
        timeline = require(pool.CreateTimelineFromClips('LingJian 基础剪辑', instructions), '时间线创建失败。')
        require(project.SetCurrentTimeline(timeline), '无法设置渲染时间线。')
        if any('treatment' in clip for clip in request['clips']):
            timeline_items = require(timeline.GetItemListInTrack('video', 1), '无法读取 Skill 镜头。')
            # Never apply recipes to a different count/order of timeline objects.
            if len(timeline_items) != len(request['clips']):
                raise RuntimeError('Skill 镜头数量与达芬奇时间线不一致。')
            for clip, target in zip(request['clips'], timeline_items):
                recipe = clip.get('treatment')
                if not recipe:
                    continue
                require(target.SetProperty('ZoomX', recipe['zoom']), '达芬奇拒绝 Skill 水平缩放。')
                require(target.SetProperty('ZoomY', recipe['zoom']), '达芬奇拒绝 Skill 垂直缩放。')
                require(target.SetCDL({'NodeIndex': '1', 'Slope': '1 1 1', 'Offset': '0 0 0',
                                       'Power': '1 1 1', 'Saturation': str(recipe['saturation'])}),
                        '达芬奇拒绝 Skill CDL；请检查安装版接口及首个调色节点。')
        codecs = project.GetRenderCodecs('mp4') or {}
        # Do not guess a codec's identifier or silently switch formats.
        codec = next((value for value in codecs.values() if value == 'H264'), None)
        require(codec, '此版本/平台未提供脚本可用的 MP4/H264 编码器。')
        require(project.SetCurrentRenderFormatAndCodec('mp4', codec), '无法选择 MP4/H264。')
        require(project.SetRenderSettings({'TargetDir': str(job_dir), 'CustomName': 'render',
                                          'SelectAllFrames': True, 'ExportVideo': True,
                                          'ExportAudio': True}), '达芬奇拒绝渲染设置。')
        require(manager.SaveProject(), '达芬奇工程保存失败。')
        if cancel():
            return {'status': 'cancelled', 'message': '渲染前已取消。', 'project': name}
        job = require(project.AddRenderJob(), '无法添加渲染任务。')
        # Treat even a failed StartRendering response as potentially ambiguous.
        render_started = True
        settled = False
        require(project.StartRendering(job, isInteractiveMode=False), '无法启动达芬奇渲染。')
        deadline = clock() + 3600
        while project.IsRenderingInProgress():
            if cancel() or clock() >= deadline:
                project.StopRendering()
                stop_deadline = clock() + 10
                while project.IsRenderingInProgress() and clock() < stop_deadline:
                    sleep(.2)
                settled = not project.IsRenderingInProgress()
                if not settled:
                    raise RuntimeError('无法确认达芬奇已停止，请在达芬奇检查任务。')
                if cancel():
                    return {'status': 'cancelled', 'message': '达芬奇渲染已停止，旧输出未改变。',
                            'project': name, 'render_job': str(job)}
                raise RuntimeError('达芬奇渲染超过一小时，已停止本次任务。')
            sleep(.5)
        settled = True
        status = project.GetRenderJobStatus(job) or {}
        require(status.get('JobStatus') == 'Complete', '达芬奇渲染未完成：' + str(status))
        require((job_dir / 'render.mp4').is_file(), '未找到预期的 MP4 输出。')
        result = {'status': 'rendered', 'project': name, 'render_job': str(job),
                  'message': '达芬奇基础剪辑及渲染完成；等待灵剪校验和发布。'}
    except Exception as exc:
        if project is not None and render_started and not settled:
            try:
                project.StopRendering()
                settled = not project.IsRenderingInProgress()
            except Exception:
                settled = False
        result = {'status': 'failed' if settled else 'uncertain', 'project': name,
                  'message': str(exc)}
    finally:
        # Only a project created by this call is ever saved/closed. Never delete it.
        if project is not None and settled:
            try:
                if manager.SaveProject():
                    manager.CloseProject(project)
            except Exception:
                pass  # Leave the saved/partial project for inspection, not deletion.
    return result


def main():
    try:
        request = validate_request(json.loads(sys.stdin.buffer.read(2_000_001).decode('utf-8')))
        result = execute(request, connect())
    except Exception as exc:
        result = {'status': 'failed', 'message': str(exc)}
    # ASCII JSON framing works even when Windows pipes use a legacy code page.
    payload = '\n' + RESULT_PREFIX + json.dumps(result, ensure_ascii=True) + '\n'
    sys.stdout.flush()
    sys.stdout.buffer.write(payload.encode('ascii'))
    sys.stdout.buffer.flush()


if __name__ == '__main__':
    main()
