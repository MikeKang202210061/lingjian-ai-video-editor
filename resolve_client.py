"""Subprocess isolation and protected publication for the optional Resolve SDK."""
import json
import os
import shutil
import subprocess
from pathlib import Path

from core import validate_rendered_mp4
from resolve_plan import validate_request
from resolve_runtime import bridge_environment

RESULT_PREFIX = 'LJ_RESOLVE_RESULT '


def run_bridge(python, sdk_path, request, ffmpeg, *, runner=subprocess.run,
               cancel_requested=lambda: False):
    validate_request(request)
    helper = Path(__file__).with_name('resolve_bridge.py')
    if not helper.is_file():
        return {'status': 'failed', 'message': '桥接脚本缺失，请使用本分支源码运行。'}
    try:
        env = bridge_environment()
    except (OSError, ValueError, TypeError) as exc:
        return {'status': 'failed', 'message': 'Resolve 本机脚本库配置无效：' + str(exc)}
    if sdk_path:
        env['RESOLVE_SCRIPT_API'] = str(sdk_path)
    job_dir = Path(request['job_dir']) if request['operation'] == 'render_basic' else None
    owns_dir = False
    invoked = False
    result = {'status': 'uncertain', 'message': '达芬奇任务没有返回可确认的结果。'}
    if job_dir:
        result['job_dir'] = str(job_dir)
    try:
        if job_dir:
            job_dir.mkdir(exist_ok=False)
            owns_dir = True
            (job_dir / 'request.json').write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
            if cancel_requested():
                (job_dir / 'cancel').touch()
        try:
            invoked = True
            process = runner([python, str(helper)], input=json.dumps(request, ensure_ascii=True),
                             capture_output=True, text=True, encoding='utf-8', errors='replace',
                             env=env, timeout=30 if job_dir is None else 3700,
                             creationflags=0x08000000 if os.name == 'nt' else 0)
        except subprocess.TimeoutExpired:
            return {'status': 'uncertain', 'message': '连接/执行超时。达芬奇任务状态未知，请在达芬奇检查后再重试。',
                    'job_dir': str(job_dir) if job_dir else ''}
        except OSError as exc:
            result = {'status': 'failed', 'message': '无法启动桥接解释器：' + str(exc)}
            return result
        messages = [line[len(RESULT_PREFIX):] for line in process.stdout.splitlines()
                    if line.startswith(RESULT_PREFIX)]
        if not messages:
            code = process.returncode
            result['message'] += ('\nBridge exit code: ' + str(code) +
                                  (' (0x%08X)' % (code & 0xffffffff) if code else '') +
                                  '\n' + (process.stderr or process.stdout)[-1600:])
            return result
        response = json.loads(messages[-1])
        if not isinstance(response, dict) or response.get('status') not in (
                'connected', 'rendered', 'cancelled', 'failed', 'uncertain'):
            raise ValueError('Invalid bridge response')
        if ((job_dir is None and response['status'] in ('rendered', 'cancelled')) or
                (job_dir is not None and response['status'] == 'connected')):
            raise ValueError('Bridge response does not match the requested operation')
        result = response
        if process.returncode != 0:
            result = {'status': 'uncertain', 'message': '桥接进程异常退出；请检查达芬奇任务。'}
        if job_dir and result.get('status') == 'rendered':
            if (job_dir / 'cancel').exists():
                result.update(status='cancelled', message='已取消发布，旧输出未改变。')
                return result
            video = job_dir / 'render.mp4'
            meta = validate_rendered_mp4(ffmpeg, str(video))
            # Recheck input aliases immediately before publication.
            validate_request(request)
            if (job_dir / 'cancel').exists():
                result.update(status='cancelled', message='已取消发布，旧输出未改变。')
                return result
            os.replace(video, request['output'])
            result.update(status='completed', output=request['output'], metadata=meta,
                          message='达芬奇基础剪辑已导出并通过校验。')
        return result
    except Exception as exc:
        # A malformed/native crash response may leave Resolve rendering.
        if result.get('status') == 'rendered':
            result = {'status': 'failed', 'message': '输出校验或发布失败，旧输出未改变：' + str(exc)}
        else:
            result = {'status': 'uncertain' if invoked else 'failed',
                      'message': '桥接任务失败：' + str(exc)}
            if job_dir:
                result['job_dir'] = str(job_dir)
        return result
    finally:
        if job_dir and owns_dir and result.get('status') != 'uncertain':
            try:
                shutil.rmtree(job_dir)
            except OSError:
                result['cleanup_warning'] = '任务文件未清理：' + str(job_dir)
