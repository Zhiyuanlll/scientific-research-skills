#!/usr/bin/env python3
"""Install selected bundled skills and local dependencies without overwriting conflicts."""
import argparse
from pathlib import Path
import shutil
import sys
sys.dont_write_bytecode = True
from verify_package import digest, verify


IGNORED = {'__pycache__', '.pytest_cache', '.git', 'node_modules', '.venv'}


def contents(directory):
    result = {}
    for path in directory.rglob('*'):
        relative = path.relative_to(directory)
        if any(part in IGNORED for part in relative.parts) or path.suffix == '.pyc':
            continue
        if path.is_symlink():
            raise ValueError('Refusing symlinked payload: ' + str(path))
        if path.is_file():
            result[relative.as_posix()] = digest(path)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=Path, default=Path.home() / '.agents' / 'skills')
    parser.add_argument('--only', nargs='+', help='Skill directories or invocation names; includes local dependencies')
    parser.add_argument('--list', action='store_true', help='List bundle contents; do not install')
    parser.add_argument('--dry-run', action='store_true', help='Check and show the installation plan; do not write')
    args = parser.parse_args()
    bundle = Path(__file__).resolve().parent
    manifest, errors = verify(bundle)
    if errors:
        raise ValueError('\n'.join(errors))
    items = {item['directory']: item for item in manifest['skills']}
    aliases = {item['name']: key for key, item in items.items()}
    if args.list:
        for key, item in items.items():
            print('%s  ($%s)  %s' % (key, item['name'], item['title']))
        return 0
    selected = set()
    for name in args.only or items:
        key = name if name in items else aliases.get(name)
        if key is None:
            raise ValueError('Unknown skill: ' + name)
        selected.add(key)
    while True:
        expanded = selected | {dep for key in selected for dep in items[key]['requires']}
        if not expanded.issubset(items):
            raise ValueError('Missing bundled dependency.')
        if expanded == selected:
            break
        selected = expanded
    target = args.target.expanduser().resolve()
    if target == bundle or bundle in target.parents:
        raise ValueError('Choose an installation target outside the extracted package.')
    if target.exists() and not target.is_dir():
        raise ValueError('Installation target is not a directory.')
    plan, conflicts = [], []
    for key in items:
        if key not in selected:
            continue
        source = bundle / 'skills' / key
        expected = {path[len('skills/' + key + '/'):]: sha for path, sha in manifest['files'].items() if path.startswith('skills/' + key + '/')}
        if contents(source) != expected:
            raise ValueError('Unlisted or changed source files in skill: ' + key)
        destination = target / key
        if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
            conflicts.append(key)
        elif destination.exists():
            if contents(destination) != expected:
                conflicts.append(key)
            else:
                plan.append((key, 'skip'))
        else:
            plan.append((key, 'install'))
    if conflicts:
        raise ValueError('No skills installed. Existing different content: ' + ', '.join(conflicts) + '. Back up outside skill discovery directories before replacing.')
    print('Target: ' + str(target))
    for key, action in plan:
        print(action.upper() + ': ' + key)
    if args.dry_run:
        print('Dry run; no files written.')
        return 0
    created = []
    try:
        target.mkdir(parents=True, exist_ok=True)
        for key, action in plan:
            if action == 'skip':
                continue
            destination = target / key
            if destination.exists():
                raise ValueError('Destination appeared during installation: ' + key)
            # Create ownership before copying so rollback touches only our new directories.
            destination.mkdir()
            created.append(destination)
            shutil.copytree(bundle / 'skills' / key, destination, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(*IGNORED, '*.pyc'))
            if contents(destination) != contents(bundle / 'skills' / key):
                raise ValueError('Post-copy mismatch: ' + key)
    except (OSError, ValueError):
        for directory in reversed(created):
            shutil.rmtree(directory)
        raise
    print('Installation complete; existing identical skills were preserved.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        sys.exit(1)
