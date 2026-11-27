#!/usr/bin/env python3
"""Mirror ComfyUI custom_nodes against a commit-pinned XLSX manifest."""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

FIELDS = ('Name', 'Link', 'Commit')
# Built-in ComfyUI entries are excluded from catalog management.
RESERVED_ENTRIES = frozenset({
    '__pycache__',
    'example_node.py.example',
    'websocket_image_save.py',
})

@dataclass(frozen=True)
class Extension:
    """Catalog identity, HTTPS origin and exact Git revision."""

    name: str
    url: str
    commit: str


@dataclass
class SyncPlan:
    """Observed discrepancies, selected operations and summary counts."""

    entries: dict[str, Path]
    changes: list[tuple[Extension, str]]
    extras: list[str]
    installs: list[tuple[Extension, str]]
    deletes: list[str]
    matches: int
    additions: int
    replacements: int


def load_manifest(path):
    """Read and validate the first worksheet as a nonempty, commit-pinned catalog."""
    from openpyxl import load_workbook
    result, names = {}, set()
    workbook = load_workbook(path, read_only=True, keep_links=False)
    try:
        rows = workbook.worksheets[0].iter_rows()
        # Accept reordered, case-insensitive headers but reject additional columns.
        header = [str(cell.value or '').strip().casefold() for cell in next(rows, ())]
        while header and not header[-1]:
            header.pop()
        if len(header) != 3 or set(header) != {x.casefold() for x in FIELDS}:
            raise ValueError('XLSX must contain exactly: ' + ', '.join(FIELDS))
        for number, cells in enumerate(rows, 2):
            values = [str(cell.value if cell.value is not None else '').strip() for cell in cells]
            if not any(values):
                continue
            try:
                if any(cell.data_type in ('f', 'e') for cell in cells) or any(values[3:]):
                    raise ValueError('unexpected cells or formulas')
                row = dict(zip(header, values))
                name, url, commit = (row[k] for k in ('name', 'link', 'commit'))
                if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', name) or name == '__pycache__':
                    raise ValueError('unsafe extension name')
                # Reject case-only collisions even on case-sensitive filesystems.
                if name.casefold() in names:
                    raise ValueError('duplicate extension name')
                parsed = urlsplit(url)
                if (parsed.scheme != 'https' or not parsed.hostname
                        or parsed.username is not None or parsed.password is not None
                        or parsed.query or parsed.fragment):
                    raise ValueError('HTTPS repository URL without credentials required')
                if not re.fullmatch(r'[0-9a-fA-F]{40}', commit):
                    raise ValueError('full Git commit SHA required')
                result[name] = Extension(name, url, commit.lower())
                names.add(name.casefold())
            except ValueError as e:
                raise ValueError(f'XLSX row {number}: {e}') from None
    finally:
        workbook.close()
    # An empty catalog must not authorize deletion of every managed extension.
    if not result:
        raise ValueError('empty manifest refused as a deletion safeguard')
    return result

def git(path, *args):
    """Run Git without interactive prompts, repository hooks or local file transport."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0')
    completed = subprocess.run(
        ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'protocol.file.allow=never',
         '-C', str(path), *args],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if completed.returncode:
        raise RuntimeError(
            f'git {args[0]} failed for {path.name} (exit {completed.returncode})'
        )
    return completed.stdout.strip()

def repository(path):
    """Recognize standalone repositories with a real .git directory."""
    return (path / '.git').is_dir() and not (path / '.git').is_symlink()

def clean(path):
    """Reject destructive operations on repositories containing local or ignored files."""
    if not repository(path):
        raise ValueError(f'not a standalone Git repository: {path.name}')
    if git(path, 'status', '--porcelain', '--untracked-files=all', '--ignored'):
        raise ValueError(f'local changes or ignored files in {path.name}; refusing replacement/removal')

def origin(url):
    """Normalize trailing slashes and the conventional .git suffix for URL comparison."""
    return url.rstrip('/').removesuffix('.git')

def problem(ext, path):
    """Return the first catalog mismatch, or None for a matching checkout."""
    if not path.exists():
        return 'missing'
    if not repository(path):
        return 'not a Git repository'
    if origin(git(path, 'remote', 'get-url', 'origin')) != origin(ext.url):
        return 'origin mismatch'
    if git(path, 'rev-parse', 'HEAD') != ext.commit:
        return 'commit mismatch'
    # Verification ignores ignored files; destructive operations use stricter clean().
    if git(path, 'status', '--porcelain', '--untracked-files=all'):
        return 'local changes'
    return None

def install(ext, root):
    """Stage a pinned checkout and replace the target with rollback on rename failure."""
    target = root / ext.name
    with tempfile.TemporaryDirectory(prefix='.ext-sync-', dir=root) as tmp:
        stage = Path(tmp) / 'repo'
        git(Path(tmp), 'clone', '--no-checkout', '--', ext.url, str(stage))
        git(stage, 'fetch', '--no-tags', 'origin', ext.commit)
        git(stage, 'checkout', '--detach', ext.commit)
        if git(stage, 'rev-parse', 'HEAD') != ext.commit:
            raise ValueError('checkout commit mismatch')
        # Keep the old checkout until the replacement has been fetched and verified.
        backup = Path(tmp) / 'backup'
        if target.exists():
            clean(target)
            target.rename(backup)
        try:
            stage.rename(target)
        # Restore the previous checkout if publishing the staged repository fails.
        except BaseException:
            if backup.exists():
                backup.rename(target)
            raise

def install_requirements(ext, root, python):
    """Install an extension's declared dependencies using the selected interpreter."""
    requirements = root / ext.name / 'requirements.txt'
    if requirements.is_file():
        print(f'REQUIREMENTS {ext.name}', flush=True)
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(requirements)],
                       cwd=requirements.parent, check=True)

def record(args):
    """Capture standalone repositories into a staged XLSX catalog."""
    from openpyxl import Workbook
    destination = args.database.resolve()
    root = validate_root(args.extensions_dir, args.database)
    rows = []
    for path in sorted(root.iterdir()):
        if path.is_symlink():
            raise ValueError(f'symlink refused: {path.name}')
        if path.name in RESERVED_ENTRIES:
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
        # Validate nonempty output before atomically replacing the destination.
        with tempfile.TemporaryDirectory(prefix='.ext-record-', dir=destination.parent) as tmp:
            staged = Path(tmp) / 'catalog.xlsx'
            workbook.save(staged)
            if rows:
                load_manifest(staged)
            os.replace(staged, destination)
    finally:
        workbook.close()
    return 0


def validate_root(directory, database):
    """Refuse dangerous roots and manifests inside the managed directory."""
    if directory.is_symlink():
        raise ValueError('symlink root refused')
    root = directory.resolve(strict=True)
    if not root.is_dir() or root in (
        Path(root.anchor), Path.home().resolve(), Path.cwd().resolve()
    ):
        raise ValueError('unsafe extensions root')
    if database.resolve().is_relative_to(root):
        raise ValueError('manifest must be outside extensions root')
    return root


def scan_entries(root, catalog):
    """Inspect immediate children without following symbolic links."""
    entries = {}
    for path in root.iterdir():
        if path.is_symlink():
            raise ValueError(f'symlink refused: {path.name}')
        if path.name in RESERVED_ENTRIES:
            if path.name in catalog:
                raise ValueError(f'reserved name: {path.name}')
            continue
        entries[path.name] = path
    return entries


def make_plan(root, catalog, command):
    """Compare disk state with the catalog and select operations for the requested mode."""
    entries = scan_entries(root, catalog)
    changes = [
        (extension, reason)
        for extension in catalog.values()
        if (reason := problem(extension, root / extension.name))
    ]
    extras = sorted(set(entries) - set(catalog))
    # Add mode never deletes extras; remove mode never installs or replaces entries.
    installs = changes if command in ('update', 'add') else []
    deletes = extras if command in ('update', 'remove') else []
    additions = sum(reason == 'missing' for _, reason in installs)
    return SyncPlan(
        entries=entries,
        changes=changes,
        extras=extras,
        installs=installs,
        deletes=deletes,
        matches=len(catalog) - len(changes),
        additions=additions,
        replacements=len(installs) - additions,
    )


def show_verify(plan, catalog, dry_run):
    """Report discrepancies and return failure unless verification is clean or a dry run."""
    for extension, reason in plan.changes:
        print(f'INVALID {extension.name} ({reason})')
    for name in plan.extras:
        print(f'EXTRA {name}')
    print(
        f'{len(catalog) - len(plan.changes)} valid, '
        f'{len(plan.changes)} invalid, {len(plan.extras)} extra'
    )
    return 0 if dry_run else int(bool(plan.changes or plan.extras))


def show_plan(plan, command, root, dry_run):
    """Print the proposed operations and aggregate counts without modifying disk state."""
    suffix = ' (DRY RUN)' if dry_run else ''
    print(f'{command.upper()}{suffix}: {root}', flush=True)
    print(
        f'{plan.matches} matches, {len(plan.installs)} installations '
        f'({plan.additions} new, {plan.replacements} replacements), '
        f'{len(plan.deletes)} deletions',
        flush=True,
    )
    for extension, reason in plan.installs:
        print(f'INSTALL {extension.name} ({reason})')
    for name in plan.deletes:
        print(f'DELETE {name}')


def apply_plan(plan, root, python):
    """Preflight every affected repository, then delete, install and pip."""
    # Check every affected existing entry before the first destructive operation.
    for name in [
        extension.name for extension, _ in plan.installs
        if extension.name in plan.entries
    ] + plan.deletes:
        clean(plan.entries[name])

    deleted = installed = replaced = requirements_done = requirements_skipped = 0
    try:
        # Keep the existing eager-delete ordering to free storage.
        for name in plan.deletes:
            clean(plan.entries[name])
            shutil.rmtree(plan.entries[name])
            deleted += 1
            print(f'DELETED {name}', flush=True)

        for extension, reason in plan.installs:
            install(extension, root)
            if reason == 'missing':
                installed += 1
            else:
                replaced += 1
            print(f'COMPLETE {extension.name}', flush=True)

        # Install dependencies only after all planned checkouts have succeeded.
        for extension, _ in plan.installs:
            if (root / extension.name / 'requirements.txt').is_file():
                install_requirements(extension, root, python)
                requirements_done += 1
            else:
                requirements_skipped += 1
    finally:
        # Report completed and pending work even when execution fails partway through.
        print(
            f'{plan.matches} matches, {installed} new installations, '
            f'{replaced} replacements, {deleted} deletions, '
            f'{requirements_done} requirements installed, '
            f'{requirements_skipped} without requirements.txt; '
            f'{len(plan.installs) - installed - replaced} installations pending, '
            f'{len(plan.deletes) - deleted} deletions pending, '
            f'{len(plan.installs) - requirements_done - requirements_skipped} '
            f'requirements checks pending',
            flush=True,
        )


def run(args):
    """Dispatch catalog validation, recording, verification or synchronization."""
    if args.command == 'record':
        return record(args)

    catalog = load_manifest(args.database.resolve(strict=True))
    if args.command == 'check':
        print(f'XLSX valid: {len(catalog)} extensions')
        return 0

    root = validate_root(args.extensions_dir, args.database)
    plan = make_plan(root, catalog, args.command)
    if args.command == 'verify':
        return show_verify(plan, catalog, args.dry_run)

    show_plan(plan, args.command, root, args.dry_run)
    if not args.dry_run:
        apply_plan(plan, root, args.python)
    return 0


def resolve_defaults(args):
    """Resolve omitted CLI paths relative to the workspace and validate the catalog suffix."""
    base = Path(__file__).resolve().parent.parent.parent
    if args.database is None:
        databases = sorted(path for path in base.glob('comfy-db*') if path.is_dir())
        if len(databases) != 1:
            raise ValueError(
                f'expected exactly one comfy-db* directory; found {len(databases)}'
            )
        args.database = databases[0] / 'extension-db.xlsx'
    if args.database.suffix.lower() != '.xlsx':
        raise ValueError('manifest must have .xlsx extension')
    if args.extensions_dir is None:
        args.extensions_dir = base / 'comfy-extensions'
    args.python = (
        args.python or base / 'comfy-ui' / '.venv' / 'bin' / 'python'
    ).absolute()
    return args


def main(argv=None):
    """Parse command-line options and translate expected failures into exit status 1."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        'command',
        choices=('update', 'add', 'remove', 'verify', 'check', 'record'),
    )
    parser.add_argument(
        '--database', type=Path,
        help='XLSX manifest (default: extension-db.xlsx in unique ../comfy-db* directory relative to script)',
    )
    parser.add_argument('--directory', dest='extensions_dir', type=Path)
    parser.add_argument(
        '--python', type=Path,
        help='Python executable for extension requirements (default: ../comfy-ui/.venv/bin/python relative to script)',
    )
    parser.add_argument('--dry-run', action='store_true')

    args = parser.parse_args(argv)
    # A dry-run catalog check deliberately performs no validation or filesystem access.
    if args.command == 'check' and args.dry_run:
        return 0
    try:
        return run(resolve_defaults(args))
    except (
        OSError, ValueError, RuntimeError, BadZipFile, ParseError,
        ImportError, subprocess.CalledProcessError,
    ) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
