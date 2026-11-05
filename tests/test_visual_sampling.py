"""Portable selection/API checks; no FFmpeg, GUI, network, or paid calls."""
import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_api import APIConfig, analyze_video, refine_highlight_boundaries, request_highlights
from visual_sampling import media_key, select_samples


def candidate(start=2.0, end=4.0):
    return dict(start=start, end=end, score=90, reason='clear', caption='', role='hook',
                stage='other', chapter='trip', shot_type='wide', motion=50, stability=80,
                audio_value=0, visual_signature='blue')


class SamplingTests(unittest.TestCase):
    def test_brief_scene_between_old_uniform_samples_is_kept(self):
        frames = select_samples(600, [(101.25, 42), (101.75, 38)])
        self.assertTrue(any(101.25 <= f['time'] < 101.75 for f in frames))
        self.assertLessEqual(len(frames), 24)
        self.assertEqual(frames[0]['time'], 0)
        self.assertGreater(frames[-1]['time'], 590)

    def test_busy_opening_does_not_starve_later_scenes(self):
        changes = [(i / 4, 60) for i in range(1, 200)]
        changes.extend([(200.25, 14), (400.25, 14), (570.25, 14)])
        times = [f['time'] for f in select_samples(600, changes)]
        self.assertLessEqual(len(times), 24)
        for wanted in (200.25, 400.25, 570.25):
            self.assertIn(wanted, times)

    def test_static_and_subsecond_media_remain_bounded(self):
        self.assertEqual(len(select_samples(600, [(i, 0) for i in range(600)])), 8)
        for duration in (.05, .4, 1.0):
            frames = select_samples(duration, [])
            self.assertTrue(all(0 <= f['time'] < duration for f in frames))
        for invalid in (0, -1, math.nan, math.inf):
            with self.assertRaises(ValueError):
                select_samples(invalid, [])

    def test_cache_identity_is_stable_and_invalidates_replacement(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'media.mp4'
            path.write_bytes(b'one')
            first = media_key(str(path), 3)
            self.assertEqual(first, media_key(str(path), 3))
            stat = path.stat()
            path.write_bytes(b'two')
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
            self.assertNotEqual(first, media_key(str(path), 3))

    def test_multisheet_payload_uses_actual_nonuniform_times(self):
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / 'sheet.jpg'
            image.write_bytes(b'fixture')
            sheets = [dict(path=str(image), frames=[dict(time=1.125), dict(time=101.25)]),
                      dict(path=str(image), frames=[dict(time=599.5)])]
            response = {'output_text': json.dumps({'summary': 'trip', 'segments': [candidate()]})}
            with patch('ai_api._request', return_value=response) as api:
                result = request_highlights(APIConfig(), '', sheets, 600, 'trip')
            body = json.loads(api.call_args.args[1])
            content = body['input'][0]['content']
            self.assertEqual(sum(item['type'] == 'input_image' for item in content), 2)
            for stamp in ('1.125000s', '101.250000s', '599.500000s'):
                self.assertIn(stamp, content[0]['text'])
            self.assertEqual(result[0]['start'], 2)

    def test_refinement_is_one_call_and_rejects_unbounded_moves(self):
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / 'fine.jpg'
            image.write_bytes(b'fixture')
            sheets = [dict(path=str(image), frames=[dict(time=1), dict(time=5)])]
            changes = [dict(candidate_index=0, start=1.5, end=4.5),
                       dict(candidate_index=1, start=0, end=99),
                       dict(candidate_index=0, start=2, end=3),
                       dict(candidate_index=4, start=0, end=1)]
            original = [candidate(), candidate(10, 12)]
            with patch('ai_api.build_contact_sheets', return_value=sheets) as build, \
                    patch('ai_api._request', return_value={'output_text': json.dumps({'refinements': changes})}) as api:
                result = refine_highlight_boundaries('ffmpeg', APIConfig(), 'source', 20, folder, original)
            self.assertEqual(api.call_count, 1)
            self.assertEqual(len(build.call_args.kwargs['samples']), 8)
            self.assertEqual((result[0]['start'], result[0]['end']), (1.5, 4.5))
            self.assertEqual(result[1], original[1])
            self.assertEqual(original[0]['start'], 2)  # original plan remains undoable

    def test_optional_refinement_failure_keeps_coarse_plan_and_reports_it(self):
        cfg = APIConfig(api_key='test', refine_highlights=True)
        coarse = [candidate()]
        report = {}
        with patch('ai_api.extract_assets', return_value=('', [])), \
                patch('ai_api.request_highlights', return_value=coarse), \
                patch('ai_api.refine_highlight_boundaries', side_effect=RuntimeError('offline')) as fine:
            self.assertEqual(analyze_video('ffmpeg', cfg, 'source', 8, '.', 'trip', report=report), coarse)
            self.assertEqual(fine.call_count, 1)
            self.assertIn('offline', report['warnings'][0])
            cfg.refine_highlights = False
            fine.reset_mock()
            analyze_video('ffmpeg', cfg, 'source', 8, '.', 'trip')
            fine.assert_not_called()


if __name__ == '__main__':
    unittest.main()
