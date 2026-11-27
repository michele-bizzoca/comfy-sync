#!/usr/bin/env python3
"""Mirror a ComfyUI models directory against a versioned XLSX manifest."""
import argparse
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
from dataclasses import dataclass
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

FIELDS = ('Name', 'Version', 'Filename', 'Size', 'Hash', 'Folder', 'Link', 'Enable')
CHUNK = 1024 * 1024

@dataclass(frozen=True)
class Model:
    name: str
    version: str
    path: PurePosixPath
    size: int
    sha256: str
    url: str
    enabled: bool


def relative_path(directory, filename):
    # Only portable, relative POSIX paths. No implicit normalization.
    parts = directory.split('/') if directory else []
    if '/' in filename or not filename:
        raise ValueError('filename must be a single filename')
    parts.append(filename)
    if any(p in ('', '.', '..') or '\\' in p or ':' in p or '\x00' in p for p in parts):
        raise ValueError('unsafe subdirectory or filename')
    return PurePosixPath(*parts)


def load_manifest(path):
    models, logical = {}, set()
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise RuntimeError('XLSX support requires openpyxl; install with pip install -r requirements.txt') from None

    workbook = load_workbook(path, read_only=True, keep_links=False)
    try:
        if not workbook.worksheets:
            raise ValueError('XLSX has no worksheets')
        sheet = workbook.worksheets[0]
        rows = sheet.iter_rows(min_col=1, max_col=max(sheet.max_column or 0, len(FIELDS)))
        header = [str(cell.value).strip().casefold() if cell.value is not None else ''
                  for cell in next(rows, ())]
        while header and not header[-1]:
            header.pop()
        expected = {field.casefold() for field in FIELDS}
        if len(header) != len(FIELDS) or set(header) != expected:
            raise ValueError('XLSX must contain exactly: ' + ', '.join(FIELDS))
        for number, cells in enumerate(rows, start=2):
            values = ['' if cell.value is None else str(cell.value).strip() for cell in cells]
            if not any(values):
                continue
            try:
                if any(cell.data_type in ('f', 'e') for cell in cells):
                    raise ValueError('formulas and Excel errors are unsupported')
                if any(values[len(header):]):
                    raise ValueError('unexpected values beyond the header')
                row = dict(zip(header, values))
                key = (row['name'], row['version'])
                if not all(key):
                    raise ValueError('name and version are required')
                logical_key = tuple(value.casefold() for value in key)
                if logical_key in logical:
                    raise ValueError('duplicate name + version')
                rel = relative_path(row['folder'], row['filename'])
                if rel in models:
                    raise ValueError('duplicate subdirectory + filename')
                if not re.fullmatch(r'[0-9]+', row['size']):
                    raise ValueError('size must be an integer in bytes')
                digest = row['hash'].lower()
                if not re.fullmatch(r'[0-9a-f]{64}', digest):
                    raise ValueError('invalid SHA-256')
                u = urlsplit(row['link'])
                if u.scheme != 'https' or not u.hostname or u.username is not None or u.password is not None:
                    raise ValueError('a direct HTTPS download URL without userinfo is required')
                enabled = row['enable'].lower()
                if enabled not in ('x', ''):
                    raise ValueError('enabled must be X (case insensitive) or empty')
                models[rel] = Model(*key, rel, int(row['size']), digest, row['link'], enabled == 'x')
                logical.add(logical_key)
            except ValueError as e:
                raise ValueError(f'XLSX row {number}: {e}') from None
    finally:
        workbook.close()
    if not models:
        raise ValueError('empty manifest refused as a deletion safeguard')
    for rel in models:
        if any(parent in models for parent in rel.parents):
            raise ValueError(f'file/directory conflict in XLSX: {rel}')
    return models


def scan(root):
    files, dirs = {}, set()
    if not root.exists():
        return files, dirs
    if not root.is_dir():
        raise ValueError('models root is not a directory')
    def on_error(e):
        raise e
    for base, names, filenames in os.walk(root, followlinks=False, onerror=on_error):
        for name in names + filenames:
            p = Path(base) / name
            if p.is_symlink():
                raise ValueError(f'symbolic link inside models directory refused: {p}')
            rel = PurePosixPath(p.relative_to(root).as_posix())
            if p.is_dir():
                dirs.add(rel)
            elif p.is_file():
                files[rel] = p
            else:
                raise ValueError(f'unsupported filesystem entry: {p}')
    return files, dirs


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for data in iter(lambda: f.read(CHUNK), b''):
            h.update(data)
    return h.hexdigest()


def problem(model, path, verify):
    if path is None:
        return 'missing'
    if path.stat().st_size != model.size:
        return 'size mismatch'
    if verify and sha256(path) != model.sha256:
        return 'SHA-256 mismatch'
    return None


def token_key(url):
    host = urlsplit(url).hostname
    if host in ('civitai.com', 'www.civitai.com'):
        return 'CA_TOKEN'
    if host in ('huggingface.co', 'www.huggingface.co'):
        return 'HF_TOKEN'
    return None


def load_token_file(script_dir):
    directories = sorted(p for p in script_dir.parent.glob('token-db*') if p.is_dir())
    if not directories:
        return {}
    if len(directories) != 1:
        raise ValueError(f'expected at most one token-db* directory; found {len(directories)}')
    path = directories[0] / 'tokens.env'
    if not path.exists():
        return {}
    tokens = {}
    with path.open(encoding='utf-8-sig') as f:
        for number, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key, sep, value = line.partition('=')
            key, value = key.strip(), value.strip()
            if not sep or key not in ('CA_TOKEN', 'HF_TOKEN') or key in tokens:
                raise ValueError(f'invalid or duplicate token entry in tokens.env, line {number}')
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            if any(c.isspace() for c in value) or any(c in value for c in ('"', "'")):
                raise ValueError(f'invalid token value in tokens.env, line {number}')
            tokens[key] = value
    return tokens


def auth_token(url):
    key = token_key(url)
    if key is None:
        return None
    return os.environ.get(key) or load_token_file(Path(__file__).resolve().parent).get(key) or None


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if target.scheme != 'https' or not target.hostname or target.username is not None or target.password is not None:
            raise ValueError('unsafe download redirect')
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            # Never forward credentials to a different origin (e.g. a CDN).
            old = urlsplit(req.full_url)
            if (old.scheme, old.hostname, old.port or 443) != (target.scheme, target.hostname, target.port or 443):
                redirected.remove_header('Authorization')
        return redirected


def download(model, root):
    target = root.joinpath(*model.path.parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    token = auth_token(model.url)
    headers = {'User-Agent': 'comfy-model-sync/0.1', 'Accept-Encoding': 'identity'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    request = Request(model.url, headers=headers)
    opener = build_opener(SafeRedirect())
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.model-sync-', suffix='.part', delete=False) as f:
            temp = Path(f.name)
            h = hashlib.sha256()
            count = 0
            with opener.open(request, timeout=60) as response:
                while True:
                    data = response.read(CHUNK)
                    if not data:
                        break
                    count += len(data)
                    if count > model.size:
                        raise ValueError('download larger than expected')
                    h.update(data)
                    f.write(data)
            if count != model.size or h.hexdigest() != model.sha256:
                raise ValueError('download failed size or SHA-256 verification')
        os.replace(temp, target)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def run(args):
    if args.command == 'check' and args.dry_run:
        return 0
    if args.xlsx.suffix.lower() != '.xlsx':
        raise ValueError('manifest must have the .xlsx extension')
    manifest = args.xlsx.resolve(strict=True)
    if args.command == 'check':
        catalog = load_manifest(manifest)
        enabled = sum(model.enabled for model in catalog.values())
        print(f'CHECK: {manifest}', flush=True)
        print(f'XLSX valid: {len(catalog)} entries, {enabled} enabled, {len(catalog) - enabled} disabled', flush=True)
        return 0
    root = args.models_dir.resolve()
    if root == Path(root.anchor) or root == Path.home().resolve() or root == Path.cwd().resolve():
        raise ValueError('refusing root, home or current working directory as models root')
    if manifest.is_relative_to(root):
        raise ValueError('XLSX must be outside the models directory')
    catalog = load_manifest(manifest)
    models = {rel: model for rel, model in catalog.items() if model.enabled}
    files, dirs = scan(root)
    for rel in models:
        if rel in dirs or any(parent in files for parent in rel.parents):
            raise ValueError(f'file/directory conflict on disk: {rel}')
    if args.command == 'verify':
        # Dry run lists the plan without reading or validating file contents.
        if args.dry_run:
            print(f'VERIFY (DRY RUN): {root}', flush=True)
            present = sorted(set(models) & set(files))
            missing = sorted(set(models) - set(files))
            extras = sorted(set(files) - set(models))
            for rel in present:
                print(f'VERIFY   {rel}', flush=True)
            for rel in missing:
                print(f'MISSING  {rel}', flush=True)
            for rel in extras:
                print(f'EXTRA    {rel}', flush=True)
            print(f'{len(present)} files to verify, {len(missing)} missing, {len(extras)} extra files', flush=True)
            return 0
        # Read-only check of the entire mirror, including unexpected files.
        print(f'VERIFY: {root}', flush=True)
        invalid = 0
        for rel, model in sorted(models.items()):
            reason = problem(model, files.get(rel), True)
            if reason:
                print(f'INVALID  {rel} ({reason})', flush=True)
                invalid += 1
            else:
                print(f'OK       {rel}', flush=True)
        extras = sorted(set(files) - set(models))
        for rel in extras:
            print(f'EXTRA    {rel}', flush=True)
        print(f'{len(models) - invalid} valid, {invalid} missing or invalid, {len(extras)} extra files', flush=True)
        return 1 if invalid or extras else 0
    downloads, deletions = [], []
    for rel, model in sorted(models.items()):
        if args.command in ('sync', 'add'):
            reason = problem(model, files.get(rel), False)
            if reason:
                downloads.append((model, reason))
    if args.command in ('sync', 'remove'):
        deletions = sorted(set(files) - set(models))
    print(f'{args.command.upper()}{" (DRY RUN)" if args.dry_run else ""}: {root}', flush=True)
    for model, reason in downloads:
        print(f'DOWNLOAD {model.path} ({reason})', flush=True)
    for rel in deletions:
        print(f'DELETE   {rel}', flush=True)
    print(f'{len(downloads)} downloads, {len(deletions)} deletions', flush=True)
    if args.dry_run:
        return 0
    # Validate fallback credentials before changing files. Never print token values.
    for key in {token_key(model.url) for model, _ in downloads} - {None}:
        if not os.environ.get(key):
            load_token_file(Path(__file__).resolve().parent)
            break
    # Never delete extras until every required download has succeeded.
    for model, _ in downloads:
        print(f'Downloading {model.path}...', flush=True)
        try:
            download(model, root)
        except Exception as e:
            # Avoid printing request URLs (which could contain credentials).
            raise RuntimeError(f'download failed for {model.path}: {type(e).__name__}: {getattr(e, "code", "")}; no extra files deleted') from None
    for rel in deletions:
        files[rel].unlink()
    return 0


def discover_db(script_dir):
    databases = sorted(p for p in script_dir.parent.glob('comfy-db*') if p.is_dir())
    if len(databases) != 1:
        raise ValueError(f'expected exactly one comfy-db* directory; found {len(databases)}')
    return databases[0]


def discover_xlsx(script_dir):
    return discover_db(script_dir) / 'model-db.xlsx'


def resolve_defaults(args):
    script_dir = Path(__file__).resolve().parent
    if args.xlsx is None:
        args.xlsx = discover_xlsx(script_dir)
    if args.command != 'check' and args.models_dir is None:
        args.models_dir = script_dir.parent / 'comfy-models'
        if not args.models_dir.is_dir():
            raise ValueError(f'default models directory does not exist or is not a directory: {args.models_dir}; specify --models-dir')
    return args


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, epilog="Credentials: CA_TOKEN and HF_TOKEN in the environment, or tokens.env in a unique ../token-db* directory relative to the script.")
    parser.add_argument('command', nargs='?', choices=('sync', 'add', 'remove', 'verify', 'check'), default='sync', help='default: sync; verify checks model files; check validates the XLSX only')
    parser.add_argument('--xlsx', type=Path, help='XLSX manifest (default: model-db.xlsx in unique ../comfy-db* directory relative to script)')
    parser.add_argument('--models-dir', '--models-directory', type=Path, help='dedicated models root (default: ../comfy-models relative to script)')
    parser.add_argument('--dry-run', action='store_true', help='print the plan without writes, downloads or hash checks; check --dry-run does nothing')
    args = parser.parse_args(argv)
    if args.command == 'check' and args.dry_run:
        return 0
    try:
        return run(resolve_defaults(args))
    except (OSError, ValueError, BadZipFile, ParseError, RuntimeError) as e:
        print(f'Error: {e}', file=sys.stderr)
        return 1

if __name__ == '__main__':
    sys.exit(main())
