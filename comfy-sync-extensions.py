#!/usr/bin/env python3
"""Mirror ComfyUI custom_nodes against a commit-pinned XLSX manifest."""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from urllib.parse import urlsplit
from zipfile import BadZipFile
from xml.etree.ElementTree import ParseError

FIELDS = ('Name', 'Link', 'Commit')

@dataclass(frozen=True)
class Extension:
    name: str
    url: str
    commit: str

def load_manifest(path):
    from openpyxl import load_workbook
    result, names = {}, set()
    w = load_workbook(path, read_only=True, keep_links=False)
    try:
        rows = w.worksheets[0].iter_rows()
        header = [str(c.value or '').strip().casefold() for c in next(rows, ())]
        while header and not header[-1]:
            header.pop()
        if len(header) != 3 or set(header) != {x.casefold() for x in FIELDS}:
            raise ValueError('XLSX must contain exactly: ' + ', '.join(FIELDS))
        for number, cells in enumerate(rows, 2):
            values = [str(c.value if c.value is not None else '').strip() for c in cells]
            if not any(values):
                continue
            try:
                if any(c.data_type in ('f', 'e') for c in cells) or any(values[3:]):
                    raise ValueError('unexpected cells or formulas')
                row = dict(zip(header, values))
                name, url, commit = (row[k] for k in ('name', 'link', 'commit'))
                if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', name) or name == '__pycache__':
                    raise ValueError('unsafe extension name')
                if name.casefold() in names:
                    raise ValueError('duplicate extension name')
                u = urlsplit(url)
                if u.scheme != 'https' or not u.hostname or u.username is not None or u.password is not None or u.query or u.fragment:
                    raise ValueError('HTTPS repository URL without credentials required')
                if not re.fullmatch(r'[0-9a-fA-F]{40}', commit):
                    raise ValueError('full Git commit SHA required')
                result[name] = Extension(name, url, commit.lower())
                names.add(name.casefold())
            except ValueError as e:
                raise ValueError(f'XLSX row {number}: {e}') from None
    finally:
        w.close()
    if not result:
        raise ValueError('empty manifest refused as a deletion safeguard')
    return result

def git(path, *args):
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0')
    p = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'protocol.file.allow=never', '-C', str(path), *args],
                       env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode:
        raise RuntimeError(f'git {args[0]} failed for {path.name} (exit {p.returncode})')
    return p.stdout.strip()

def repository(path):
    return (path / '.git').is_dir() and not (path / '.git').is_symlink()

def clean(path):
    if not repository(path):
        raise ValueError(f'not a standalone Git repository: {path.name}')
    if git(path, 'status', '--porcelain', '--untracked-files=all', '--ignored'):
        raise ValueError(f'local changes or ignored files in {path.name}; refusing replacement/removal')

def origin(url):
    return url.rstrip('/').removesuffix('.git')

def problem(ext, path):
    if not path.exists():
        return 'missing'
    if not repository(path):
        return 'not a Git repository'
    if origin(git(path, 'remote', 'get-url', 'origin')) != origin(ext.url):
        return 'origin mismatch'
    if git(path, 'rev-parse', 'HEAD') != ext.commit:
        return 'commit mismatch'
    if git(path, 'status', '--porcelain', '--untracked-files=all'):
        return 'local changes'
    return None

def install(ext, root):
    target = root / ext.name
    with tempfile.TemporaryDirectory(prefix='.ext-sync-', dir=root) as tmp:
        stage = Path(tmp) / 'repo'
        git(Path(tmp), 'clone', '--no-checkout', '--', ext.url, str(stage))
        git(stage, 'fetch', '--no-tags', 'origin', ext.commit)
        git(stage, 'checkout', '--detach', ext.commit)
        if git(stage, 'rev-parse', 'HEAD') != ext.commit:
            raise ValueError('checkout commit mismatch')
        backup = Path(tmp) / 'backup'
        if target.exists():
            clean(target)
            target.rename(backup)
        try:
            stage.rename(target)
        except BaseException:
            if backup.exists():
                backup.rename(target)
            raise

def install_requirements(ext, root, python):
    requirements = root / ext.name / 'requirements.txt'
    if requirements.is_file():
        print(f'REQUIREMENTS {ext.name}', flush=True)
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(requirements)],
                       cwd=requirements.parent, check=True)

def record(args):
    from openpyxl import Workbook
    root = args.extensions_dir
    if root.is_symlink():
        raise ValueError('symlink root refused')
    root = root.resolve(strict=True)
    if not root.is_dir() or root in (Path(root.anchor), Path.home().resolve(), Path.cwd().resolve()):
        raise ValueError('unsafe extensions root')
    destination = args.database.resolve()
    if destination.is_relative_to(root):
        raise ValueError('manifest must be outside extensions root')
    rows = []
    for path in sorted(root.iterdir()):
        if path.is_symlink():
            raise ValueError(f'symlink refused: {path.name}')
        if path.name in ('__pycache__', 'example_node.py.example', 'websocket_image_save.py'):
            continue
        if not path.is_dir():
            continue
        if not repository(path):
            print(f'SKIP {path.name}: not a standalone Git repository', file=sys.stderr)
            continue
        try:
            url = git(path, 'remote', 'get-url', 'origin')
        except RuntimeError:
            print(f'SKIP {path.name}: cannot read Git origin', file=sys.stderr)
            continue
        commit = git(path, 'rev-parse', 'HEAD')
        rows.append((path.name, url, commit))
        print(f'RECORD {path.name}')
    if args.dry_run:
        return 0
    workbook = Workbook()
    try:
        sheet = workbook.active
        sheet.append(FIELDS)
        for row in rows:
            sheet.append(row)
        with tempfile.TemporaryDirectory(prefix='.ext-record-', dir=destination.parent) as tmp:
            staged = Path(tmp) / 'catalog.xlsx'
            workbook.save(staged)
            if rows:
                load_manifest(staged)
            os.replace(staged, destination)
    finally:
        workbook.close()
    return 0


def run(args):
    if args.command == 'record':
        return record(args)
    catalog = load_manifest(args.database.resolve(strict=True))
    if args.command == 'check':
        print(f'XLSX valid: {len(catalog)} extensions')
        return 0
    root = args.extensions_dir
    if root.is_symlink():
        raise ValueError('symlink root refused')
    root = root.resolve(strict=True)
    if not root.is_dir() or root in (Path(root.anchor), Path.home().resolve(), Path.cwd().resolve()):
        raise ValueError('unsafe extensions root')
    if args.database.resolve().is_relative_to(root):
        raise ValueError('manifest must be outside extensions root')
    entries = {}
    for p in root.iterdir():
        if p.is_symlink():
            raise ValueError(f'symlink refused: {p.name}')
        if p.name in ('__pycache__', 'example_node.py.example', 'websocket_image_save.py'):
            if p.name in catalog:
                raise ValueError(f'reserved name: {p.name}')
            continue
        entries[p.name] = p
    changes = [(e, problem(e, root / e.name)) for e in catalog.values()]
    changes = [(e, reason) for e, reason in changes if reason]
    extras = sorted(set(entries) - set(catalog))
    if args.command == 'verify':
        for e, reason in changes:
            print(f'INVALID {e.name} ({reason})')
        for name in extras:
            print(f'EXTRA {name}')
        print(f'{len(catalog)-len(changes)} valid, {len(changes)} invalid, {len(extras)} extra')
        return 0 if args.dry_run else int(bool(changes or extras))
    installs = changes if args.command in ('update', 'add') else []
    deletes = extras if args.command in ('update', 'remove') else []
    for e, reason in installs:
        print(f'INSTALL {e.name} ({reason})')
    for name in deletes:
        print(f'DELETE {name}')
    if args.dry_run:
        return 0
    for name in [e.name for e, _ in installs if e.name in entries] + deletes:
        clean(entries[name])
    for e, _ in installs:
        install(e, root)
    for ext, _ in installs:
        install_requirements(ext, root, args.python)
    for name in deletes:
        clean(entries[name])
        shutil.rmtree(entries[name])
    return 0

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', nargs='?', choices=('update','add','remove','verify','check','record'), default='update')
    p.add_argument('--database', type=Path, help='XLSX manifest (default: extension-db.xlsx in unique ../comfy-db* directory relative to script)')
    p.add_argument('--directory', dest='extensions_dir', type=Path)
    p.add_argument('--python', type=Path, help='Python executable for extension requirements (default: ../comfy-ui/.venv/bin/python relative to script)')
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args(argv)
    if a.command == 'check' and a.dry_run:
        return 0
    try:
        base = Path(__file__).resolve().parent.parent
        if a.database is None:
            db = sorted(x for x in base.glob('comfy-db*') if x.is_dir())
            if len(db) != 1:
                raise ValueError(f'expected exactly one comfy-db* directory; found {len(db)}')
            a.database = db[0] / 'extension-db.xlsx'
        if a.database.suffix.lower() != '.xlsx':
            raise ValueError('manifest must have .xlsx extension')
        if a.extensions_dir is None:
            a.extensions_dir = base / 'comfy-extensions'
        a.python = (a.python or base / 'comfy-ui' / '.venv' / 'bin' / 'python').absolute()
        return run(a)
    except (OSError, ValueError, RuntimeError, BadZipFile, ParseError, ImportError, subprocess.CalledProcessError) as e:
        print(f'Error: {e}', file=sys.stderr)
        return 1

if __name__ == '__main__':
    sys.exit(main())
