"""Project save failures must not truncate an existing project."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import Clip, Project
from project_files import write_project


class ProjectFileTests(unittest.TestCase):
    def test_unicode_project_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '旅行工程.ljproject'
            project = Project('优胜美地')
            project.clips = [Clip('素材.mp4', 0, 2, caption='出发')]
            project.save(path)
            self.assertEqual(Project.load(path).to_dict(), project.to_dict())

    def test_failed_replace_preserves_previous_file_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'saved.ljproject'
            write_project(path, {'title': 'previous'})
            with patch('project_files.os.replace', side_effect=OSError('disk unavailable')):
                with self.assertRaises(OSError):
                    write_project(path, {'title': 'new'})
            self.assertEqual(json.loads(path.read_text())['title'], 'previous')
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_failed_serialization_leaves_existing_project_intact(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'saved.ljproject'
            write_project(path, {'title': 'previous'})
            with self.assertRaises(TypeError):
                write_project(path, {'bad': object()})
            self.assertEqual(json.loads(path.read_text())['title'], 'previous')
            self.assertEqual(len(list(Path(folder).iterdir())), 1)


if __name__ == '__main__':
    unittest.main()
