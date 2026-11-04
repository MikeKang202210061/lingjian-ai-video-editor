"""Skills compile auditable data; no model, Resolve installation or network."""
import copy
import json
import tempfile
import unittest
import uuid
from pathlib import Path

from core import Clip, Project
from edit_protocol import build_edit_plan, plan_to_clips, plan_preview_text
from editing_skills import CATALOG, ROOT, apply_editing_skill, load_skill, skill_prompt, validate_snapshot
from resolve_bridge import execute
from resolve_plan import project_request, validate_request
from tests.test_resolve_backend import SDK


def sample_plan(path='source.mp4'):
    analyses = [{'meta': {'path': path, 'name': '实验素材', 'duration': 60, 'capture_order': '001'}, 'segments': []}]
    sequence = [{'source_index': 0, 'start': i * 8, 'end': i * 8 + 8, 'role': role,
                 'reason': reason, 'caption': caption, 'transition': 'none'}
                for i, (role, reason, caption) in enumerate([
                    ('setup', '建立地点与目标', ''), ('development', '操作细节', '旋转装置'),
                    ('development', '继续操作', ''), ('climax', '结果揭示', '结果'),
                    ('outro', '结尾回收', '')])]
    return build_edit_plan(sequence, analyses, 40, '记录一次实验', 'offline')


class EditingSkillTests(unittest.TestCase):
    def test_both_documents_and_profiles_are_loadable(self):
        for key in CATALOG:
            skill = load_skill(key)
            self.assertEqual(skill['id'], key)
            self.assertIn('references/sources.md', skill['instructions'])
            self.assertTrue((ROOT / key / 'references/sources.md').is_file())
            self.assertIn('剪辑 Skill', skill_prompt(skill))

    def test_distinct_recipes_preserve_source_evidence_and_input(self):
        original = sample_plan()
        untouched = copy.deepcopy(original)
        plans = [apply_editing_skill(original, load_skill(key)) for key in CATALOG]
        self.assertEqual(original, untouched)
        for plan in plans:
            self.assertTrue(plan['validation']['ok'], plan['validation'])
            self.assertEqual([(x['source_path'], x['start'], x['end'], x['caption']) for x in original['decisions']],
                             [(x['source_path'], x['start'], x['end'], x['caption']) for x in plan['decisions']])
            self.assertIn('待补能力', plan_preview_text(plan))
        self.assertNotEqual(plans[0]['sound_cues'], plans[1]['sound_cues'])
        self.assertEqual(plans[1]['sound_cues'], [])
        self.assertNotEqual(plans[0]['decisions'][1]['motion_effect'], plans[1]['decisions'][1]['motion_effect'])
        self.assertNotEqual(plans[0]['decisions'][3]['resolve_treatment'], plans[1]['decisions'][3]['resolve_treatment'])

    def test_accent_spacing_is_enforced(self):
        for key in CATALOG:
            skill = load_skill(key)
            plan = sample_plan()
            for shot in plan['decisions']:
                shot['reason'] = '结果揭示'
            events = apply_editing_skill(plan, skill)['global_creative_direction']['events']
            starts = [x['timeline_start'] for x in events if x['accent']]
            self.assertTrue(all(b - a >= skill['profile']['accent_spacing'] for a, b in zip(starts, starts[1:])))

    def test_no_skill_keeps_existing_plan_and_does_not_alias_it(self):
        plan = sample_plan()
        result = apply_editing_skill(plan, load_skill(''))
        self.assertEqual(result, plan)
        self.assertIsNot(result, plan)

    def test_unknown_path_missing_or_invalid_pack_fails_closed(self):
        with self.assertRaises(ValueError):
            load_skill('../evil')
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                load_skill('hetongxue-inspired', folder)
            dest = Path(folder) / 'hetongxue-inspired'
            dest.mkdir()
            (dest / 'SKILL.md').write_text((ROOT / dest.name / 'SKILL.md').read_text(encoding='utf-8'), encoding='utf-8')
            profile = load_skill(dest.name)['profile']
            profile['zoom'] = float('nan')
            (dest / 'profile.json').write_text(json.dumps(profile), encoding='utf-8')
            with self.assertRaises(ValueError):
                load_skill(dest.name, folder)

    def test_snapshot_isolated_and_tampering_rejected(self):
        skill = load_skill('hetongxue-inspired')
        skill['profile']['zoom'] = 9
        with self.assertRaises(ValueError):
            validate_snapshot(skill)
        self.assertEqual(load_skill('hetongxue-inspired')['profile']['zoom'], 1.06)
        skill = load_skill('hetongxue-inspired')
        skill['instructions'] += 'changed'
        with self.assertRaises(ValueError):
            skill_prompt(skill)

    def test_project_roundtrip_split_and_request_keep_per_clip_treatment(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.mp4'
            source.touch()
            plan = apply_editing_skill(sample_plan(str(source)), load_skill('hetongxue-inspired'))
            project = Project(clips=plan_to_clips(plan, Clip), edit_plan=plan)
            restored = Project.from_dict(project.to_dict())
            restored.split(1, 12)
            request = project_request(restored, Path(folder) / 'out.mp4', 320, 180, uuid.uuid4().hex)
            self.assertEqual(request['clips'][1]['treatment'], request['clips'][2]['treatment'])
            restored.clips.reverse()
            reversed_request = project_request(restored, Path(folder) / 'out.mp4', 320, 180, uuid.uuid4().hex)
            self.assertEqual(reversed_request['clips'][0]['treatment'], request['clips'][-1]['treatment'])

    def test_resolve_rejects_arbitrary_effects_and_unsafe_parameters(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.mp4'
            source.touch()
            project = Project(clips=[Clip(str(source), 0, 1)])
            request = project_request(project, Path(folder) / 'out.mp4', 320, 180, uuid.uuid4().hex)
            for recipe in ({'method': 'exec'}, {'zoom': 2, 'saturation': 1}, {'zoom': True, 'saturation': 1},
                           {'zoom': 1, 'saturation': float('nan')}, {'zoom': '1', 'saturation': 1}):
                bad = copy.deepcopy(request)
                bad['clips'][0]['treatment'] = recipe
                with self.subTest(recipe=recipe), self.assertRaises(ValueError):
                    validate_request(bad)

    def test_fake_sdk_receives_only_mapped_properties_and_failure_stops_render(self):
        class Target:
            def __init__(self, accept=True):
                self.properties, self.cdl, self.accept = {}, None, accept
            def SetProperty(self, key, value):
                self.properties[key] = value
                return self.accept
            def SetCDL(self, values):
                self.cdl = values
                return self.accept
        class Timeline:
            def __init__(self, targets):
                self.targets = targets
            def GetItemListInTrack(self, kind, index):
                assert (kind, index) == ('video', 1)
                return self.targets
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.mp4'
            source.touch()
            project = Project(clips=[Clip(str(source), 0, 1, resolve_treatment={'zoom': 1.06, 'saturation': 1.03})])
            for accept, count in [(True, 1), (False, 1), (True, 2)]:
                request = project_request(project, Path(folder) / 'out.mp4', 320, 180, uuid.uuid4().hex)
                Path(request['job_dir']).mkdir()
                sdk, target = SDK(), Target(accept)
                sdk.manager.project.pool.CreateTimelineFromClips = lambda name, clips: Timeline([target] * count)
                result = execute(request, sdk)
                self.assertEqual(result['status'], 'rendered' if accept and count == 1 else 'failed')
                if accept and count == 1:
                    self.assertEqual(target.properties, {'ZoomX': 1.06, 'ZoomY': 1.06})
                    self.assertEqual(target.cdl['Saturation'], '1.03')
                else:
                    self.assertFalse((Path(request['job_dir']) / 'render.mp4').exists())


if __name__ == '__main__':
    unittest.main()
