"""Prepare a project-local Windows venv and collect explicit, bounded diagnostics.

No model calls, administrator changes, global pip installs, downloads of Resolve,
or modifications to an open Resolve project. Invoke `check --resolve` to probe it.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ('PySide6', 'numpy', 'onnxruntime', 'Pillow')


def run(command, *, cwd=ROOT, timeout=None):
    return subprocess.run(command, cwd=cwd, check=True, timeout=timeout, shell=False)


def check_python(version=None, bits=None):
    version = sys.version_info[:2] if version is None else version
    bits = struct.calcsize('P') * 8 if bits is None else bits
    if tuple(version) not in ((3, 11), (3, 12)) or bits != 64:
        raise ValueError('Use 64-bit Python 3.11 or 3.12. Existing installations are not changed.')


def environment_python(root=ROOT):
    root = Path(root).resolve()
    folder = root / '.venv'
    if folder.resolve().parent != root:
        raise ValueError('The .venv path points outside this project; it will not be modified.')
    if folder.exists() and not (folder / 'pyvenv.cfg').is_file():
        raise ValueError('Existing .venv is not a Python environment; it will not be replaced.')
    return folder / 'Scripts' / 'python.exe'


def setup(root=ROOT, *, runner=run, system=None):
    root = Path(root).resolve()
    if (platform.system() if system is None else system) != 'Windows':
        raise ValueError('Setup targets native Windows, where the installed Resolve SDK runs.')
    check_python()
    for item in ('app.py', 'requirements.txt', 'skills'):
        if not (root / item).exists():
            raise ValueError('Incomplete source folder: missing ' + item)
    python = environment_python(root)
    if not python.is_file():
        if python.parent.parent.exists():
            raise ValueError('Existing .venv is incomplete; inspect it before retrying.')
        runner([sys.executable, '-m', 'venv', str(python.parent.parent)], cwd=root)
    # Verify the existing interpreter belongs to this environment before pip changes.
    probe = ('import pathlib,sys; expected=pathlib.Path(sys.argv[1]).resolve(); '
             'assert pathlib.Path(sys.prefix).resolve()==expected; '
             'assert sys.version_info[:2] in ((3,11),(3,12)); '
             'import struct; assert struct.calcsize("P")==8')
    runner([str(python), '-c', probe, str(python.parent.parent)], cwd=root, timeout=15)
    runner([str(python), '-m', 'pip', 'install', '-r', str(root / 'requirements.txt')], cwd=root)
    runner([str(python), '-m', 'pip', 'check'], cwd=root, timeout=60)
    runner([str(python), str(root / 'scripts/local_environment.py'), 'check'], cwd=root, timeout=60)
    return python


def find_ffmpeg(root=ROOT):
    candidate = Path(root) / 'ffmpeg.exe'
    return str(candidate) if candidate.is_file() else shutil.which('ffmpeg')


def collect_report(root=ROOT, *, probe_resolve=False):
    root = Path(root).resolve()
    report = {'system': platform.system(), 'python': platform.python_version(),
              'python_executable': sys.executable, 'project': str(root), 'packages': {}, 'issues': []}
    try:
        check_python()
    except ValueError as exc:
        report['issues'].append(str(exc))
    for name in PACKAGES:
        try:
            report['packages'][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            report['packages'][name] = None
            report['issues'].append('Missing dependency: ' + name)
    # A package version alone does not establish that native DLLs/plugins can load.
    code = ('from PySide6.QtWidgets import QApplication; '
            'from PySide6.QtMultimedia import QMediaPlayer; '
            'from PySide6.QtMultimediaWidgets import QVideoWidget; '
            'import numpy,onnxruntime; from PIL import Image; '
            'app=QApplication([]); assert QApplication.instance() is not None; print("QT_READY")')
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen')
    try:
        result = subprocess.run([sys.executable, '-c', code], cwd=root, env=env, capture_output=True,
                                text=True, encoding='utf-8', errors='replace', timeout=20, check=True)
        report['qt_startup'] = 'QT_READY' in result.stdout
        if not report['qt_startup']:
            report['issues'].append('Qt startup did not return its readiness marker.')
    except (OSError, subprocess.SubprocessError) as exc:
        report['qt_startup'] = False
        report['issues'].append('Qt/native dependencies cannot start: ' + str(exc))
    ffmpeg = find_ffmpeg(root)
    report['ffmpeg'] = ffmpeg
    if ffmpeg:
        try:
            result = subprocess.run([ffmpeg, '-version'], capture_output=True, text=True,
                                    encoding='utf-8', errors='replace', timeout=10, check=True)
            report['ffmpeg_version'] = result.stdout.splitlines()[0]
        except (OSError, subprocess.SubprocessError, IndexError) as exc:
            report['issues'].append('FFmpeg cannot run: ' + str(exc))
    else:
        report['issues'].append('FFmpeg missing: put ffmpeg.exe in the project folder or add it to PATH. '
                                'Official download page: https://ffmpeg.org/download.html')
    try:
        sys.path.insert(0, str(root))
        from editing_skills import CATALOG, load_skill
        report['skills'] = {key: load_skill(key, root / 'skills')['sha256'] for key in CATALOG}
    except Exception as exc:
        report['issues'].append('Skill resources cannot load: ' + str(exc))
    # Report missing resources instead of claiming their audio/fonts were tested.
    report['assets'] = {name: (root / 'assets' / name).is_dir() for name in ('fonts', 'music', 'sfx')}
    for name, exists in report['assets'].items():
        if not exists:
            report['issues'].append('Source resource folder missing: assets/' + name)
    if probe_resolve:
        if platform.system() != 'Windows':
            report['resolve'] = {'status': 'not_run', 'message': 'Run this check on your Windows PC.'}
        else:
            from resolve_client import run_bridge
            report['resolve'] = run_bridge(sys.executable, '', {'version': 1, 'operation': 'probe'}, ffmpeg or '')
            if report['resolve'].get('status') != 'connected':
                report['issues'].append('Resolve connection was not verified. See resolve result.')
    else:
        report['resolve'] = {'status': 'not_run', 'message': 'Use check --resolve for a read-only connection probe.'}
    report['ok'] = not report['issues']
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('setup', 'check', 'start'))
    parser.add_argument('--resolve', action='store_true', help='Read-only SDK connection probe; never render or alter a project.')
    parser.add_argument('--resolve-library', help='Save the installed fusionscript.dll path in ignored project-local configuration.')
    args = parser.parse_args(argv)
    try:
        if args.resolve_library:
            sys.path.insert(0, str(ROOT))
            from resolve_runtime import configure_library
            configure_library(args.resolve_library)
        if args.action == 'setup':
            python = setup()
            print('Local environment prepared: ' + str(python))
            print('Open this project in a LOCAL desktop chat; no cloud chat is moved automatically.')
            return 0
        if args.action == 'start':
            python = environment_python()
            if not python.is_file():
                raise ValueError('Run setup_windows.cmd first.')
            run([str(python), str(ROOT / 'app.py')])
            return 0
        report = collect_report(probe_resolve=args.resolve)
        folder = ROOT / '.local'
        if folder.resolve().parent != ROOT.resolve():
            raise ValueError('The diagnostic folder points outside this project.')
        folder.mkdir(exist_ok=True)
        destination = folder / 'environment.json'
        if destination.is_symlink():
            raise ValueError('Diagnostic target is a symlink; it will not be overwritten.')
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True, indent=2))
        print('Local report: ' + str(destination))
        return 0 if report['ok'] else 2
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print('Environment setup/check stopped: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
