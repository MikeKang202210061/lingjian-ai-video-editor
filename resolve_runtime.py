"""Project-local SDK library configuration for nonstandard Resolve installs."""
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def validate_library(value):
    path = Path(value)
    if not path.is_absolute() or path.name.lower() != 'fusionscript.dll' or not path.is_file():
        raise ValueError('Select the installed Resolve fusionscript.dll using an absolute path.')
    return str(path.resolve())


def configure_library(value, root=ROOT):
    library = validate_library(value)
    folder = Path(root) / '.local'
    if folder.resolve().parent != Path(root).resolve():
        raise ValueError('Local configuration points outside this project.')
    folder.mkdir(exist_ok=True)
    target = folder / 'resolve.json'
    if target.is_symlink():
        raise ValueError('Resolve configuration is a symlink.')
    from project_files import write_project
    write_project(target, {'script_library': library})
    return library


def bridge_environment(environment=None, root=ROOT):
    env = dict(os.environ if environment is None else environment)
    # An explicit process setting always wins over the project-local setting.
    if env.get('RESOLVE_SCRIPT_LIB'):
        validate_library(env['RESOLVE_SCRIPT_LIB'])
        return env
    target = Path(root) / '.local' / 'resolve.json'
    if target.is_file():
        config = json.loads(target.read_text(encoding='utf-8'))
        if not isinstance(config, dict) or set(config) != {'script_library'}:
            raise ValueError('Invalid project-local Resolve configuration.')
        env['RESOLVE_SCRIPT_LIB'] = validate_library(config['script_library'])
    return env
