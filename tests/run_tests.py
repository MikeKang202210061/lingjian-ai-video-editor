"""Run the portable core regression checks without external test frameworks."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
PORTABLE_TESTS = [
    "test_project_files.py",
    "test_visual_sampling.py",
    "test_edit_protocol.py",
    "test_auto_order.py",
    "test_continuity.py",
    "test_story_profile.py",
    "test_longform.py",
    "test_capture_order.py",
]


def run(path: Path, *args: str) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    print(f"\n==> {path.name}", flush=True)
    subprocess.run([sys.executable, str(path), *args], cwd=ROOT, env=env, check=True)


def main() -> None:
    for filename in PORTABLE_TESTS:
        run(TESTS / filename)

    with tempfile.TemporaryDirectory(prefix="lingjian-tests-") as folder:
        fixture = Path(folder) / "contact-sheet.jpg"
        fixture.write_bytes(b"lingjian-test-fixture")
        run(TESTS / "test_ai_director.py", str(fixture))

    print("\nAll portable regression checks passed.")


if __name__ == "__main__":
    main()

