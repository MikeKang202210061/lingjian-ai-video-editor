import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import Project
from project_session import DocumentState, RecoveryStore


class ProjectSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_clean_identity_tracks_content_instead_of_history_length(self):
        original = Project('旅行').to_dict()
        state = DocumentState(original)
        original['title'] = '新标题'
        self.assertTrue(state.is_dirty(original))
        original['title'] = '旅行'
        self.assertFalse(state.is_dirty(original))
        state.force_dirty()
        self.assertTrue(state.is_dirty(original))
        state.mark_clean(original)
        self.assertFalse(state.is_dirty(original))

    def test_independent_sessions_do_not_overwrite_each_other(self):
        first = RecoveryStore(self.root)
        second = RecoveryStore(self.root)
        first.save(Project('第一份').to_dict(), 'original.ljproject')
        second.save(Project('第二份').to_dict())
        self.assertNotEqual(first.current, second.current)
        self.assertEqual([e.title for e in second.entries()], ['第一份'])
        second.clear_current()
        self.assertTrue(first.current.exists())

    def test_snapshot_is_a_loadable_project_and_identical_content_is_not_rewritten(self):
        store = RecoveryStore(self.root)
        data = Project('恢复工程').to_dict()
        self.assertTrue(store.save(data))
        before = store.current.stat().st_mtime_ns
        self.assertFalse(store.save(data))
        self.assertEqual(store.current.stat().st_mtime_ns, before)
        self.assertEqual(Project.load(store.current).to_dict(), data)
        self.assertNotIn('_recovery', data)

    def test_legacy_and_malformed_snapshots_are_handled_without_losing_valid_data(self):
        legacy = self.root / 'legacy.ljproject'
        Project('旧版副本').save(legacy)
        folder = self.root / 'recovery'
        folder.mkdir()
        (folder / 'broken.ljproject').write_text('{broken')
        store = RecoveryStore(folder, legacy)
        self.assertEqual([e.title for e in store.entries()], ['旧版副本'])
        self.assertTrue(legacy.exists())

    def test_failed_snapshot_write_can_be_retried_and_keeps_previous_content(self):
        store = RecoveryStore(self.root)
        original = Project('原副本').to_dict()
        changed = Project('新副本').to_dict()
        store.save(original)
        with patch('project_session.write_project', side_effect=OSError('full')):
            with self.assertRaises(OSError):
                store.save(changed)
        self.assertEqual(Project.load(store.current).title, '原副本')
        self.assertTrue(store.save(changed))

    def test_cleanup_cannot_delete_an_arbitrary_or_protected_project(self):
        store = RecoveryStore(self.root / 'recovery')
        ordinary = self.root / 'real-project.ljproject'
        Project('原工程').save(ordinary)
        store.remove(ordinary)
        self.assertTrue(ordinary.exists())
        store.save(Project('恢复副本').to_dict())
        store.clear_current(protected=store.current)
        self.assertTrue(store.current.exists())


if __name__ == '__main__':
    unittest.main()
