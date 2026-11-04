"""Real FFmpeg fixtures for scene retention, timestamps, silent media and cache."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_api import extract_assets
from visual_sampling import build_contact_sheets, scan_changes, select_samples

FFMPEG = shutil.which('ffmpeg')


@unittest.skipUnless(FFMPEG, 'FFmpeg required')
class RealMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PIL import Image
        cls.Image = Image
        cls.temp = tempfile.TemporaryDirectory(prefix='lingjian-visual-test-')
        cls.root = Path(cls.temp.name)
        cls.video = cls.root / 'brief-scene.mp4'
        # Half-second blue scene in an otherwise red, silent seven-second clip.
        cmd = [FFMPEG, '-v', 'error', '-y', '-f', 'lavfi', '-i',
               'color=red:s=160x90:r=8:d=3', '-f', 'lavfi', '-i',
               'color=blue:s=160x90:r=8:d=0.5', '-f', 'lavfi', '-i',
               'color=red:s=160x90:r=8:d=3.5', '-filter_complex',
               '[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]', '-map', '[v]',
               '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(cls.video)]
        subprocess.run(cmd, capture_output=True, check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_brief_blue_scene_is_detected_and_visible_in_contact_sheet(self):
        changes = scan_changes(FFMPEG, str(self.video))
        samples = select_samples(7, changes)
        self.assertTrue(any(3 <= frame['time'] < 3.5 for frame in samples))
        sheets = build_contact_sheets(FFMPEG, str(self.video), str(self.root / 'overview'), 7)
        self.assertLessEqual(len(sheets), 3)
        blue_seen = False
        for sheet in sheets:
            with self.Image.open(sheet['path']) as image:
                for index, frame in enumerate(sheet['frames']):
                    # Real PTS must land on this fixture's 8-fps grid.
                    self.assertAlmostEqual(frame['time'] * 8, round(frame['time'] * 8), places=4)
                    if 3 <= frame['time'] < 3.5:
                        red, green, blue = image.getpixel(((index % 4) * 240 + 120,
                                                          (index // 4) * 156 + 65))
                        blue_seen |= blue > 180 and red < 50
        self.assertTrue(blue_seen)
        with patch('visual_sampling.scan_changes', side_effect=AssertionError('cache not reused')):
            self.assertEqual(sheets, build_contact_sheets(FFMPEG, str(self.video), str(self.root / 'overview'), 7))
        Path(sheets[0]['path']).unlink()
        rebuilt = build_contact_sheets(FFMPEG, str(self.video), str(self.root / 'overview'), 7)
        self.assertTrue(Path(rebuilt[0]['path']).is_file())

    def test_nonzero_start_timestamp_maps_back_to_relative_clip_time(self):
        offset = self.root / 'offset.mp4'
        subprocess.run([FFMPEG, '-v', 'error', '-y', '-i', str(self.video), '-c', 'copy',
                        '-output_ts_offset', '5', str(offset)], capture_output=True, check=True)
        sheets = build_contact_sheets(FFMPEG, str(offset), str(self.root / 'offset-cache'), 7,
                                      samples=[dict(time=3.06, kind='test', score=0)])
        frame = sheets[0]['frames'][0]
        self.assertAlmostEqual(frame['time'], 3.125, places=5)
        with self.Image.open(sheets[0]['path']) as image:
            red, green, blue = image.getpixel((120, 65))
            self.assertGreater(blue, 180)
            self.assertLess(red, 50)

    def test_silent_clip_does_not_require_audio_transcription(self):
        audio, sheets = extract_assets(FFMPEG, str(self.video), str(self.root / 'silent-cache'), 7)
        self.assertFalse(Path(audio).exists())
        self.assertTrue(sheets)

    def test_partial_last_sheet_has_only_real_frames_and_matching_manifest(self):
        samples = [dict(time=i * .5, kind='test', score=0) for i in range(9)]
        sheets = build_contact_sheets(FFMPEG, str(self.video), str(self.root / 'group-cache'), 7,
                                      samples=samples)
        self.assertEqual([len(sheet['frames']) for sheet in sheets], [8, 1])
        self.assertEqual([frame['time'] for sheet in sheets for frame in sheet['frames']],
                         [i * .5 for i in range(9)])
        manifest = json.loads((Path(sheets[0]['path']).parent / 'manifest.json').read_text())
        self.assertEqual(manifest['sheets'][1]['frames'], sheets[1]['frames'])
        with self.Image.open(sheets[1]['path']) as image:
            self.assertEqual(image.size, (240, 156))


if __name__ == '__main__':
    unittest.main()
