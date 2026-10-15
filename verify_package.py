#!/usr/bin/env python3
"""Verify the bundled payload against PACKAGE_MANIFEST.json; standard library only."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def verify(root):
    root = Path(root).resolve()
    errors = []
    try:
        manifest = json.loads((root / 'PACKAGE_MANIFEST.json').read_text(encoding='utf-8'))
        if manifest.get('schema_version') != 1 or not isinstance(manifest.get('files'), dict) or not manifest['files']:
            raise ValueError('Invalid or empty package manifest.')
        for relative, expected in manifest['files'].items():
            logical = PurePosixPath(relative)
            if logical.is_absolute() or '..' in logical.parts or '\\' in relative:
                errors.append('Invalid manifest path: ' + relative)
                continue
            path = root.joinpath(*logical.parts)
            try:
                path.resolve().relative_to(root)
            except ValueError:
                errors.append('Path escapes package: ' + relative)
                continue
            if path.is_symlink() or not path.is_file():
                errors.append('Missing or unsupported file: ' + relative)
            elif not isinstance(expected, str) or digest(path) != expected:
                errors.append('File checksum mismatch: ' + relative)
        if not isinstance(manifest.get('skills'), list) or type(manifest.get('skill_count')) is not int or manifest['skill_count'] < 1 or len(manifest['skills']) != manifest['skill_count']:
            errors.append('Skill count does not match the edition manifest.')
        for item in manifest.get('skills', []):
            if not isinstance(item, dict) or not isinstance(item.get('directory'), str):
                errors.append('Invalid skill metadata.')
                continue
            entry = 'skills/' + item['directory'] + '/SKILL.md'
            if entry not in manifest['files']:
                errors.append('Unlisted skill entrypoint: ' + entry)
        return manifest, errors
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {}, ['Cannot verify package: ' + str(exc)]


def main():
    manifest, errors = verify(Path(__file__).resolve().parent)
    if errors:
        print('\n'.join('ERROR: ' + item for item in errors), file=sys.stderr)
        return 1
    print('Package verified: %d skills, %d payload files.' % (len(manifest['skills']), len(manifest['files'])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
