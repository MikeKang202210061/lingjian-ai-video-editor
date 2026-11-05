"""Restricted protocol, SDK contract doubles and isolated subprocess checks.

No installed Resolve instance, license, network or paid model is used.
"""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core import Clip, Project
from resolve_bridge import execute, source_frames
from resolve_client import RESULT_PREFIX, run_bridge
from resolve_plan import project_request, transfer_warnings, validate_request


class Media:
    def GetClipProperty(self, key):
        return {'FPS': '24', 'Frames': '240'}[key]


class Pool:
    def __init__(self):
        self.imported = []
        self.instructions = []

    def ImportMedia(self, paths):
        self.imported.extend(paths)
        return [Media()]

    def CreateTimelineFromClips(self, name, instructions):
        self.instructions = instructions
        return object()


class SDKProject:
    def __init__(self):
        self.pool = Pool()
        self.settings = {}
        self.stopped = False

    def GetName(self):
        return 'Existing user project'

    def SetSetting(self, key, value):
        self.settings[key] = value
        return True

    def GetMediaPool(self):
        return self.pool

    def SetCurrentTimeline(self, timeline):
        return True

    def GetRenderCodecs(self, format):
        return {'H.264': 'H264'}

    def SetCurrentRenderFormatAndCodec(self, format, codec):
        return format == 'mp4' and codec == 'H264'

    def SetRenderSettings(self, values):
        self.render_settings = values
        return True

    def AddRenderJob(self):
        return 'owned-job'

    def StartRendering(self, job, isInteractiveMode=False):
        assert job == 'owned-job'
        (Path(self.render_settings['TargetDir']) / 'render.mp4').write_bytes(b'render fixture')
        return True

    def IsRenderingInProgress(self):
        return False

    def GetRenderJobStatus(self, job):
        return {'JobStatus': 'Complete'}

    def StopRendering(self):
        self.stopped = True


class Manager:
    def __init__(self, current=None):
        self.current = current
        self.created = []
        self.closed = []
        self.project = SDKProject()

    def GetCurrentProject(self):
        return self.current

    def CreateProject(self, name):
        self.created.append(name)
        return self.project

    def SaveProject(self):
        return True

    def CloseProject(self, project):
        self.closed.append(project)
        return True


class SDK:
    def __init__(self, current=None):
        self.manager = Manager(current)

    def GetProjectManager(self):
        return self.manager

    def GetProductName(self):
        return 'Resolve Studio simulator'

    def GetVersionString(self):
        return 'test-only'


class ResolveBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lingjian-resolve-tests-')
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.mp4'
        self.source.write_bytes(b'original source')
        self.output = self.root / 'output.mp4'
        self.output.write_bytes(b'previous output')
        self.project = Project(clips=[Clip(str(self.source), 1, 2, transition='none'),
                                      Clip(str(self.source), 3, 4, transition='none')])
        self.request = project_request(self.project, self.output, 320, 180, uuid.uuid4().hex)
        self.directory = Path(self.request['job_dir'])

    def tearDown(self):
        self.temp.cleanup()

    def old_output_unchanged(self):
        self.assertEqual(self.output.read_bytes(), b'previous output')
        self.assertEqual(self.source.read_bytes(), b'original source')

    def run_client(self, status='rendered', *, result_text=None, **kwargs):
        def runner(command, **options):
            self.assertIsInstance(command, list)
            self.assertEqual(json.loads(options['input']), self.request)
            (self.directory / 'render.mp4').write_bytes(b'new video')
            return SimpleNamespace(returncode=0, stderr='', stdout='SDK banner\n' + RESULT_PREFIX +
                                   (result_text if result_text is not None else json.dumps({'status': status})))
        return run_bridge(sys.executable, '', self.request, 'ffmpeg', runner=runner, **kwargs)

    def test_request_accepts_only_named_operations_not_model_code(self):
        for change in ({'operation': 'exec'}, {'script': 'import os'}, {'version': True}):
            request = dict(self.request, **change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_request(request)
        self.old_output_unchanged()

    def test_native_crash_reports_exit_code_and_stage_without_retry(self):
        runner = lambda *args, **kwargs: SimpleNamespace(
            returncode=-1073741819, stdout='', stderr='LJ_RESOLVE_STAGE sdk_import\n')
        result = run_bridge(sys.executable, '', {'version': 1, 'operation': 'probe'}, '', runner=runner)
        self.assertEqual(result['status'], 'uncertain')
        self.assertIn('0xC0000005', result['message'])
        self.assertIn('sdk_import', result['message'])

    def test_invalid_ranges_and_nonfinite_numbers_are_rejected(self):
        for value in (float('nan'), float('inf'), -1, True):
            request = copy.deepcopy(self.request)
            request['clips'][0]['start'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_request(request)

    def test_input_and_hardlink_outputs_are_rejected(self):
        with self.assertRaisesRegex(ValueError, '覆盖输入素材'):
            project_request(self.project, self.source, 320, 180, uuid.uuid4().hex)
        alias = self.root / 'alias.mp4'
        os.link(self.source, alias)
        with self.assertRaisesRegex(ValueError, '覆盖输入素材'):
            project_request(self.project, alias, 320, 180, uuid.uuid4().hex)

    def test_unacknowledged_or_foreign_job_directory_is_rejected(self):
        for change in ({'acknowledge_basic_only': False}, {'job_dir': str(self.root / 'other')}):
            with self.assertRaises(ValueError):
                validate_request(dict(self.request, **change))

    def test_unsupported_features_are_named_before_transfer(self):
        self.project.clips[0].caption = 'caption'
        self.project.clips[0].motion_effect = 'slow_push'
        self.project.clips[1].transition = 'fade'
        self.project.bgm = 'music.wav'
        result = transfer_warnings(self.project, True)
        self.assertIn('字幕及标题', result)
        self.assertIn('转场', result)
        self.assertIn('背景音乐及闪避', result)
        self.assertIn('静音及音量调整', result)
        self.assertIn('蒙版、运镜或人物效果', result)

    def test_probe_is_read_only_and_reports_existing_project(self):
        sdk = SDK(SDKProject())
        result = execute({'version': 1, 'operation': 'probe'}, sdk)
        self.assertEqual(result['status'], 'connected')
        self.assertFalse(result['ready'])
        self.assertFalse(sdk.manager.created)
        self.assertFalse(sdk.manager.closed)

    def test_existing_user_project_is_never_closed_or_modified(self):
        sdk = SDK(SDKProject())
        with self.assertRaisesRegex(RuntimeError, '保存并关闭'):
            execute(self.request, sdk)
        self.assertFalse(sdk.manager.created)
        self.assertFalse(sdk.manager.closed)

    def test_sdk_contract_imports_once_trims_and_renders_owned_project(self):
        self.directory.mkdir()
        sdk = SDK()
        result = execute(self.request, sdk)
        self.assertEqual(result['status'], 'rendered')
        project = sdk.manager.project
        self.assertEqual(project.pool.imported, [str(self.source)])
        self.assertEqual([(c['startFrame'], c['endFrame']) for c in project.pool.instructions],
                         [(24, 47), (72, 95)])
        self.assertEqual(project.settings['timelineFrameRate'], '30')
        self.assertEqual(sdk.manager.closed, [project])
        self.old_output_unchanged()  # SDK only writes its temporary render.

    def test_bad_source_frames_are_rejected(self):
        with self.assertRaises(ValueError):
            source_frames(Media(), {'path': str(self.source), 'start': 10, 'end': 11})

    def test_missing_h264_fails_without_publishing(self):
        self.directory.mkdir()
        sdk = SDK()
        sdk.manager.project.GetRenderCodecs = lambda _: {}
        result = execute(self.request, sdk)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('编码器', result['message'])
        self.old_output_unchanged()

    def test_cancel_during_render_stops_only_owned_project(self):
        self.directory.mkdir()
        sdk = SDK()
        project = sdk.manager.project
        def in_progress():
            if project.stopped:
                return False
            (self.directory / 'cancel').touch()
            return True
        project.IsRenderingInProgress = in_progress
        result = execute(self.request, sdk, sleep=lambda _: None)
        self.assertEqual(result['status'], 'cancelled')
        self.assertTrue(project.stopped)
        self.assertEqual(sdk.manager.closed, [project])
        self.old_output_unchanged()

    def test_unconfirmed_stop_does_not_close_a_live_project(self):
        self.directory.mkdir()
        sdk = SDK()
        sdk.manager.project.IsRenderingInProgress = lambda: (_ for _ in ()).throw(RuntimeError('SDK lost'))
        result = execute(self.request, sdk)
        self.assertEqual(result['status'], 'uncertain')
        self.assertFalse(sdk.manager.closed)

    def test_client_validates_before_replacing_output(self):
        def validate(ffmpeg, path):
            self.old_output_unchanged()
            self.assertEqual(Path(path).read_bytes(), b'new video')
            return {'duration': 2}
        with patch('resolve_client.validate_rendered_mp4', side_effect=validate):
            result = self.run_client()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.output.read_bytes(), b'new video')
        self.assertFalse(self.directory.exists())

    def test_validation_failure_preserves_output(self):
        with patch('resolve_client.validate_rendered_mp4', side_effect=ValueError('bad video')):
            result = self.run_client()
        self.assertEqual(result['status'], 'failed')
        self.old_output_unchanged()

    def test_publication_failure_preserves_output(self):
        with patch('resolve_client.validate_rendered_mp4', return_value={}), \
                patch('resolve_client.os.replace', side_effect=PermissionError('locked')):
            result = self.run_client()
        self.assertEqual(result['status'], 'failed')
        self.old_output_unchanged()

    def test_unknown_or_malformed_response_keeps_job_files(self):
        for response in ('not json', '[]', '{"status":"connected"}'):
            with self.subTest(response=response):
                result = self.run_client(result_text=response)
                self.assertEqual(result['status'], 'uncertain')
                self.assertTrue(self.directory.exists())
                self.old_output_unchanged()
                shutil.rmtree(self.directory)

    def test_timeout_preserves_old_output_and_uncertain_job_files(self):
        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], 3700)
        result = run_bridge(sys.executable, '', self.request, 'ffmpeg', runner=timeout)
        self.assertEqual(result['status'], 'uncertain')
        self.assertTrue(self.directory.exists())
        self.old_output_unchanged()

    def test_early_cancel_race_creates_marker_and_preserves_output(self):
        def runner(*args, **kwargs):
            self.assertTrue((self.directory / 'cancel').is_file())
            return SimpleNamespace(returncode=0, stderr='', stdout=RESULT_PREFIX + '{"status":"cancelled"}')
        result = run_bridge(sys.executable, '', self.request, 'ffmpeg', runner=runner,
                            cancel_requested=lambda: True)
        self.assertEqual(result['status'], 'cancelled')
        self.assertFalse(self.directory.exists())
        self.old_output_unchanged()

    def test_real_subprocess_connects_only_to_explicit_fake_sdk(self):
        sdk = self.root / '模拟-sdk'
        sdk.mkdir()
        (sdk / 'DaVinciResolveScript.py').write_text(
            "class Manager:\n def GetCurrentProject(self): return None\n"
            "class Resolve:\n def GetProjectManager(self): return Manager()\n"
            " def GetProductName(self): return '仅模拟测试'\n"
            " def GetVersionString(self): return 'fake'\n"
            "def scriptapp(name):\n print('SDK startup',end='')\n return Resolve()\n", encoding='utf-8')
        with patch.dict(os.environ, {'PYTHONIOENCODING': 'ascii'}):
            result = run_bridge(sys.executable, str(sdk), {'version': 1, 'operation': 'probe'}, 'ffmpeg')
        self.assertEqual(result['status'], 'connected')
        self.assertEqual(result['product'], '仅模拟测试')
        self.assertTrue(result['ready'])

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg required for generated MP4')
    def test_isolated_fake_sdk_output_passes_real_mp4_validation(self):
        ffmpeg = shutil.which('ffmpeg')
        subprocess.run([ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i',
                        'testsrc2=s=320x180:r=24:d=1', '-c:v', 'libx264', '-pix_fmt',
                        'yuv420p', str(self.source)], capture_output=True, check=True)
        original = self.source.read_bytes()
        self.request['clips'] = [{'path': str(self.source), 'start': 0, 'end': 1}]
        sdk = self.root / 'render-sdk'
        sdk.mkdir()
        (sdk / 'DaVinciResolveScript.py').write_text(
            "from tests.test_resolve_backend import SDK\nimport shutil\nfrom pathlib import Path\n"
            "def scriptapp(name):\n sdk=SDK()\n project=sdk.manager.project\n"
            " def render(job,isInteractiveMode=False):\n"
            f"  shutil.copyfile({str(self.source)!r}, Path(project.render_settings['TargetDir'])/'render.mp4')\n"
            "  return True\n project.StartRendering=render\n return sdk\n", encoding='utf-8')
        result = run_bridge(sys.executable, str(sdk), self.request, ffmpeg)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(self.output.read_bytes(), original)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(result['metadata']['width'], 320)
        self.assertFalse(self.directory.exists())


if __name__ == '__main__':
    unittest.main()
