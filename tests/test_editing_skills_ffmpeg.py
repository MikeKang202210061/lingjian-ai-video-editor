"""Both skill recipes rendered by the real local engine, without cloud calls."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from core import Clip, Project, build_render_command, validate_rendered_mp4
from edit_protocol import build_edit_plan, plan_to_clips
from editing_skills import apply_editing_skill, load_skill

FFMPEG = shutil.which('ffmpeg')


@unittest.skipUnless(FFMPEG, 'FFmpeg required')
class EditingSkillMediaTests(unittest.TestCase):
    def test_both_recipes_render_valid_h264_with_original_audio(self):
        with tempfile.TemporaryDirectory(prefix='lingjian-skill-media-') as folder:
            root = Path(folder)
            source = root / 'source.mp4'
            subprocess.run([FFMPEG, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=s=320x180:r=30:d=4',
                            '-f', 'lavfi', '-i', 'sine=frequency=440:duration=4', '-c:v', 'libx264',
                            '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(source)],
                           check=True, capture_output=True)
            original = source.read_bytes()
            analyses = [{'meta': {'path': str(source), 'name': 'fixture', 'duration': 4}, 'segments': []}]
            sequence = [{'source_index': 0, 'start': 0, 'end': 2, 'role': 'climax', 'reason': '结果揭示',
                         'caption': 'Result', 'transition': 'none'},
                        {'source_index': 0, 'start': 2, 'end': 4, 'role': 'outro', 'reason': '结尾回收',
                         'caption': '', 'transition': 'none'}]
            decoded = []
            for key in ('hetongxue-inspired', 'mediastorm-inspired'):
                plan = apply_editing_skill(build_edit_plan(sequence, analyses, 4), load_skill(key))
                project = Project(clips=plan_to_clips(plan, Clip))
                out = root / (key + '.mp4')
                command = build_render_command(FFMPEG, project, str(out), 320, 180)
                result = subprocess.run(command, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace')[-2000:])
                meta = validate_rendered_mp4(FFMPEG, str(out))
                self.assertEqual((meta['width'], meta['height'], meta['has_audio']), (320, 180, True))
                self.assertAlmostEqual(meta['duration'], 4, delta=.15)
                frame = subprocess.run([FFMPEG, '-v', 'error', '-ss', '0.4', '-i', str(out),
                                        '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                                       check=True, capture_output=True).stdout
                self.assertEqual(len(frame), 320 * 180 * 3)
                decoded.append(frame)
            self.assertNotEqual(decoded[0], decoded[1])
            self.assertEqual(source.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
