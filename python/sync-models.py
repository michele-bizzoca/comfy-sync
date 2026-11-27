#!/usr/bin/env python3
"""Mirror a ComfyUI models directory against a versioned XLSX manifest.

Folder + Filename is the primary key. Name + Version is a partial descriptive
identifier, may contain ? placeholders, and need not be unique. Link may be ?
when the download URL is unknown; a required download then fails explicitly.
"""
import argparse
import shutil
from contextlib import closing
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile, is_zipfile
from urllib.parse import urlsplit, urljoin

FIELDS = ('Name', 'Version', 'Folder', 'Filename', 'Size', 'Hash', 'Link', 'Enable')
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


def load_manifest(path, allow_empty=False):
    models = {}
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
                if not row['name'] or not row['filename']:
                    raise ValueError('name and filename are required')
                rel = relative_path(row['folder'], row['filename'])
                if rel in models:
                    raise ValueError('duplicate Folder + Filename')
                if not re.fullmatch(r'[0-9]+', row['size']):
                    raise ValueError('size must be an integer in bytes')
                digest = row['hash'].lower()
                if not re.fullmatch(r'[0-9a-f]{64}', digest):
                    raise ValueError('invalid SHA-256')
                if row['link'] != '?':
                    u = urlsplit(row['link'])
                    if u.scheme != 'https' or not u.hostname or u.username is not None or u.password is not None:
                        raise ValueError('a direct HTTPS download URL without userinfo is required')
                enabled = row['enable'].lower()
                if enabled not in ('x', ''):
                    raise ValueError('enabled must be X (case insensitive) or empty')
                models[rel] = Model(*key, rel, int(row['size']), digest, row['link'], enabled == 'x')
            except ValueError as e:
                raise ValueError(f'XLSX row {number}: {e}') from None
    finally:
        workbook.close()
    if not models and not allow_empty:
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
    if host in ('civitai.com', 'www.civitai.com', 'civitai.red', 'www.civitai.red'):
        return 'TOKEN_CA'
    if host in ('huggingface.co', 'www.huggingface.co'):
        return 'TOKEN_HF'
    return None


def load_token_file(script_dir):
    path = discover_db(script_dir) / 'token-db.env'
    if not path.exists():
        return {}
    tokens = {}
    with path.open(encoding='utf-8-sig') as f:
        for number, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith(('export ', 'export\t')):
                line = line[6:].lstrip()
            key, sep, value = line.partition('=')
            key, value = key.strip(), value.strip()
            if not sep or not key or not key.isascii() or not (key[0].isalpha() or key[0] == '_') or not all(c.isalnum() or c == '_' for c in key):
                raise ValueError(f'invalid environment assignment in token-db.env, line {number}')
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            tokens[key] = value
    return tokens


def auth_token(url):
    key = token_key(url)
    if key is None:
        return None
    return os.environ.get(key) or load_token_file(Path(__file__).resolve().parent.parent).get(key) or None


class DownloadError(RuntimeError):
    pass


def partial_paths(rel):
    return tuple(rel.with_name(rel.name + suffix) for suffix in
                 ('.part', '.part.json', '.part.json.tmp'))


def stream(client, url, headers):
    # Explicit redirects: never forward credentials across origins.
    for _ in range(11):
        u = urlsplit(url)
        if u.scheme != 'https' or not u.hostname or u.username is not None or u.password is not None:
            raise DownloadError('unsafe download redirect')
        response = client.send(client.build_request('GET', url, headers=headers), stream=True)
        if response.status_code not in (301, 302, 303, 307, 308):
            return response
        location = response.headers.get('location')
        response.close()
        if not location:
            raise DownloadError('redirect without location')
        next_url = urljoin(url, location)
        v = urlsplit(next_url)
        if (u.scheme, u.hostname, u.port or 443) != (v.scheme, v.hostname, v.port or 443):
            headers = {k: v for k, v in headers.items() if k.lower() != 'authorization'}
        url = next_url
    raise DownloadError('too many redirects')


def download_segment(client, model, temp, headers, etag, total, block, index):
    """Retry one exact byte range and flush its bytes before acknowledging it."""
    import httpx

    lo, hi = index * block, min(total, (index + 1) * block) - 1
    request_headers = dict(headers, Range=f'bytes={lo}-{hi}')
    if etag:
        request_headers['If-Range'] = etag
    for attempt in range(6):
        try:
            with closing(stream(client, model.url, request_headers)) as response:
                if response.status_code != 206 or response.headers.get('content-range') != f'bytes {lo}-{hi}/{total}':
                    actual = response.headers.get('content-range', '')
                    safe_actual = actual if re.fullmatch(r'bytes (?:[0-9]+-[0-9]+|\*)/(?:[0-9]+|\*)', actual) else 'missing or invalid'
                    raise DownloadError(f'range response mismatch: expected bytes {lo}-{hi}/{total}; HTTP {response.status_code}; received {safe_actual}')
                if etag and response.headers.get('etag') not in (None, etag):
                    raise DownloadError('remote entity changed')
                offset = lo
                with temp.open('r+b', buffering=0) as f:
                    f.seek(lo)
                    for data in response.iter_raw(CHUNK):
                        if offset + len(data) > hi + 1:
                            raise DownloadError('range response too large')
                        f.write(data)
                        offset += len(data)
                    if offset != hi + 1:
                        raise DownloadError('incomplete range')
                    os.fsync(f.fileno())
            return index, hi - lo + 1
        except (httpx.HTTPError, OSError, DownloadError):
            if attempt == 5:
                raise
            time.sleep(min(2 ** attempt, 16))


def save_download_state(scratch, state_path, state):
    """Atomically persist resumable transfer metadata."""
    with scratch.open('w') as handle:
        json.dump(state, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(scratch, state_path)


def load_download_state(state_path, identity, temp):
    try:
        state = json.loads(state_path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(state, dict) or state.get('identity') != identity or not temp.is_file():
        return {}
    return state


def probe_range_support(client, url, headers):
    """Detect HTTP range support and ignore weak ETags for resumption."""
    with closing(stream(client, url, dict(headers, Range='bytes=0-0'))) as response:
        match = re.fullmatch(
            r'bytes 0-0/([0-9]+)', response.headers.get('content-range', '')
        )
        etag = response.headers.get('etag')
        if etag and etag.startswith('W/'):
            etag = None
        if response.status_code == 206 and match and int(match[1]) > 0:
            total = int(match[1])
        elif response.status_code in (200, 416):
            total = None
        else:
            raise DownloadError(f'HTTP {response.status_code}')
    return total, etag


def transfer(model, temp, state_path, scratch, connections, progress=None):
    import httpx
    headers = {'User-Agent': 'comfy-model-sync/0.1', 'Accept-Encoding': 'identity'}
    token = auth_token(model.url)
    if token:
        headers['Authorization'] = f'Bearer {token}'
    identity = {'hash': model.sha256, 'size': model.size,
                'url_hash': hashlib.sha256(model.url.encode()).hexdigest()}
    started = time.monotonic()
    received = 0

    def report(done, total):
        speed = received / max(time.monotonic() - started, .001) / 1048576
        fraction = min(done / total, 1) if total else 0
        bar = '#' * int(fraction * 20) + '-' * (20 - int(fraction * 20))
        bar_text = f'[{bar}] {fraction:.1%} ' if total else ''
        line = f'{model.path}: {bar_text}{done / 1048576:.1f} MiB  {speed:.1f} MiB/s'
        if progress is None:
            print(line, file=sys.stderr, flush=True)
        else:
            progress(model.path, line, fraction, speed)

    with httpx.Client(timeout=httpx.Timeout(connect=10.0, read=15.0, write=10.0, pool=10.0), follow_redirects=False, trust_env=False) as client:
        state = load_download_state(state_path, identity, temp)
        if state.get('complete'):
            return
        total, etag = probe_range_support(client, model.url, headers)
        if total is None:
            # No ranges: restart this transfer, never append an unverified response.
            save_download_state(scratch, state_path, {'identity': identity})
            with closing(stream(client, model.url, headers)) as response:
                if response.status_code != 200:
                    raise DownloadError(f'HTTP {response.status_code}')
                length = response.headers.get('content-length', '')
                length = int(length) if length.isdecimal() else 0
                last = started
                with temp.open('wb') as f:
                    for data in response.iter_raw(CHUNK):
                        f.write(data)
                        received += len(data)
                        if time.monotonic() - last >= 2:
                            report(received, length)
                            last = time.monotonic()
                    f.flush()
                    os.fsync(f.fileno())
                if length and received != length:
                    raise DownloadError('incomplete response')
                report(received, length)
        else:
            # Fixed chunks allow resumption even when concurrency changes.
            block = 16 * 1024 * 1024
            count = (total + block - 1) // block
            resumable = (
                bool(state) and temp.exists() and bool(etag)
                and state.get('etag') == etag
                and state.get('total') == total
                and temp.stat().st_size == total
            )
            if not resumable:
                state = {}
            completed = {i for i in state.get('completed', []) if isinstance(i, int) and 0 <= i < count}
            with temp.open('r+b' if state else 'w+b') as f:
                f.truncate(total)
            state = {'identity': identity, 'etag': etag, 'total': total, 'completed': sorted(completed)}
            save_download_state(scratch, state_path, state)

            failures = []
            done = sum(min(block, total - i * block) for i in completed)
            last = started
            with ThreadPoolExecutor(max_workers=connections) as pool:
                futures = [
                    pool.submit(
                        download_segment, client, model, temp, headers,
                        etag, total, block, index,
                    )
                    for index in range(count) if index not in completed
                ]
                for future in as_completed(futures):
                    try:
                        i, size = future.result()
                    except Exception as e:
                        failures.append(e)
                        continue
                    completed.add(i)
                    received += size
                    done += size
                    state['completed'] = sorted(completed)
                    save(state)
                    if time.monotonic() - last >= 2:
                        report(done, total)
                        last = time.monotonic()
            report(done, total)
            if failures:
                raise failures[0]
        save_download_state(scratch, state_path, {'identity': identity, 'complete': True})


def download(model, root, connections=4, progress=None):
    target = root.joinpath(*model.path.parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp, state_path, scratch = (root.joinpath(*p.parts) for p in partial_paths(model.path))
    extracted = None
    try:
        transfer(model, temp, state_path, scratch, connections, progress)
        candidate = temp
        if not (temp.stat().st_size == model.size and sha256(temp) == model.sha256) and is_zipfile(temp):
            with ZipFile(temp) as archive:
                matches = [entry for entry in archive.infolist()
                           if not entry.is_dir() and entry.file_size == model.size]
                if not matches:
                    raise ValueError('ZIP contains no file of expected size')
                for entry in matches:
                    with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.model-sync-', suffix='.part', delete=False) as f:
                        extracted = Path(f.name)
                        count = 0
                        with archive.open(entry) as source:
                            while True:
                                data = source.read(CHUNK)
                                if not data:
                                    break
                                count += len(data)
                                if count > model.size:
                                    raise ValueError('extracted model larger than expected')
                                f.write(data)
                    if count == model.size and sha256(extracted) == model.sha256:
                        candidate = extracted
                        break
                    extracted.unlink()
                    extracted = None
                else:
                    raise ValueError('ZIP contains no file with expected SHA-256')
        if candidate.stat().st_size != model.size:
            raise ValueError(f'download size mismatch (expected {model.size} bytes, got {candidate.stat().st_size} bytes)')
        if sha256(candidate) != model.sha256:
            raise ValueError('download SHA-256 mismatch')
        os.replace(candidate, target)
        temp.unlink(missing_ok=True)
        state_path.unlink(missing_ok=True)
    except (ValueError, BadZipFile):
        # Invalid completed content must not be resumed indefinitely.
        temp.unlink(missing_ok=True)
        state_path.unlink(missing_ok=True)
        raise
    finally:
        for item in (scratch, extracted):
            if item is not None:
                item.unlink(missing_ok=True)


def record(args):
    from openpyxl import Workbook, load_workbook
    destination = args.database.resolve()
    root = args.models_dir.resolve(strict=True)
    if not root.is_dir() or root in (Path(root.anchor), Path.home().resolve(), Path.cwd().resolve()):
        raise ValueError('unsafe models root')
    if destination.is_relative_to(root):
        raise ValueError('XLSX must be outside the models directory')
    existing = {}
    if destination.exists():
        load_manifest(destination, allow_empty=True)
        workbook = load_workbook(destination, read_only=True, keep_links=False)
        try:
            rows = workbook.worksheets[0].values
            header = [str(value or '').strip().casefold() for value in next(rows)]
            for values in rows:
                if not any(value is not None and str(value).strip() for value in values):
                    continue
                row = dict(zip(header, values))
                rel = relative_path(str(row['folder'] or '').strip(), str(row['filename']).strip())
                existing[rel] = [row[field.casefold()] for field in FIELDS]
        finally:
            workbook.close()
    files, _ = scan(root)
    rows = []
    for rel, path in sorted(files.items()):
        if rel.name.endswith(('.part', '.part.json', '.part.json.tmp')):
            continue
        folder = rel.parent.as_posix() if rel.parent != PurePosixPath('.') else ''
        relative_path(folder, rel.name)
        if rel in existing:
            row = existing[rel]
            row[FIELDS.index('Enable')] = 'X'
        else:
            row = ['?', '?', folder, rel.name, path.stat().st_size,
                   sha256(path) if not args.dry_run else None, '?', 'X']
        rows.append(row)
        print(f'RECORD {rel}')
    for rel in sorted(set(existing) - set(files)):
        print(f'OMIT {rel}')
    if args.dry_run:
        return 0
    workbook = Workbook()
    try:
        sheet = workbook.active
        sheet.append(FIELDS)
        for row in rows:
            sheet.append(row)
            # Preserve literal text even when a filename starts with "=".
            for cell in sheet[sheet.max_row]:
                if isinstance(cell.value, str):
                    cell.data_type = 's'
        with tempfile.TemporaryDirectory(prefix='.model-record-', dir=destination.parent) as tmp:
            staged = Path(tmp) / 'catalog.xlsx'
            workbook.save(staged)
            load_manifest(staged, allow_empty=True)
            os.replace(staged, destination)
    finally:
        workbook.close()
    return 0


@dataclass(frozen=True)
class ModelPlan:
    downloads: list[tuple[Model, str]]
    deletions: list[PurePosixPath]
    matches: int


def preflight_downloads(downloads):
    """Check optional dependencies and credentials before deletions."""
    if downloads:
        try:
            import httpx
        except ImportError:
            raise RuntimeError('downloads require httpx; install it in the venv') from None
    # Validate fallback credentials before changing files. Never print token values.
    for key in {token_key(model.url) for model, _ in downloads} - {None}:
        if not os.environ.get(key):
            load_token_file(Path(__file__).resolve().parent.parent)
            break


def validate_disk_layout(catalog, enabled_models, files, directories):
    """Guard against partial-file, directory and filename collisions."""
    reserved = {
        partial
        for relative in enabled_models
        for partial in partial_paths(relative)
    }
    if (
        reserved & set(catalog)
        or reserved & directories
        or any(parent in reserved for relative in catalog for parent in relative.parents)
    ):
        raise ValueError('partial download path conflicts with catalog or directory')
    for relative in enabled_models:
        if relative in directories or any(parent in files for parent in relative.parents):
            raise ValueError(f'file/directory conflict on disk: {relative}')


def verify_models(root, models, files, dry_run):
    """Read-only verification, including the no-hash dry-run mode."""
    # Dry run lists the plan without reading or validating file contents.
    if dry_run:
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

def plan_changes(args, models, files):
    """Determine downloads and extras without modifying the filesystem."""
    downloads, deletions = [], []
    matches = sum(not problem(model, files.get(rel), False) for rel, model in models.items())
    for rel, model in sorted(models.items()):
        if args.command in ('update', 'add'):
            reason = problem(model, files.get(rel), False)
            if reason:
                if model.url == '?':
                    raise ValueError(f'{rel}: {reason}; missing download link (Link = ?)')
                downloads.append((model, reason))
    if args.command in ('update', 'remove'):
        keep = {p for model, _ in downloads for p in partial_paths(model.path)}
        deletions = sorted(set(files) - set(models) - keep)
    downloads.sort(key=lambda item: (item[0].size, item[0].path))
    return ModelPlan(downloads, deletions, matches)


def show_plan(args, root, plan):
    downloads, deletions, matches = plan.downloads, plan.deletions, plan.matches
    print(f'{args.command.upper()}{" (DRY RUN)" if args.dry_run else ""}: {root}', flush=True)
    print(f'{matches} matches, {len(downloads)} downloads, {len(deletions)} deletions', flush=True)
    for model, reason in downloads:
        print(f'DOWNLOAD {model.path} ({reason})', flush=True)
    for rel in deletions:
        print(f'DELETE   {rel}', flush=True)

def run_downloads(args, root, plan):
    """Execute concurrent downloads with the existing progress reporting."""
    downloads, deletions, matches = plan.downloads, plan.deletions, plan.matches
    import threading
    lock = threading.Lock()
    active = {}
    finished = 0
    successful_bytes = 0
    planned_bytes = sum(model.size for model, _ in downloads)
    tty = sys.stderr.isatty()
    drawn = 0

    def clear():
        nonlocal drawn
        if tty and drawn:
            sys.stderr.write(f'\x1b[{drawn}A\x1b[J')
            drawn = 0

    def render():
        nonlocal drawn
        if not tty:
            return
        lines = [entry[0] for entry in active.values()]
        estimated = successful_bytes + sum(entry[1] * entry[3] for entry in active.values())
        speed = sum(entry[2] for entry in active.values())
        fraction = min(estimated / planned_bytes, 1) if planned_bytes else 1
        lines.append(f'TOTAL (estimated): {fraction:.1%} | {speed:.1f} MiB/s | {finished}/{len(downloads)} finished')
        width = shutil.get_terminal_size((100, 24)).columns
        for line in lines:
            sys.stderr.write(line[:max(1, width - 1)] + '\n')
        sys.stderr.flush()
        drawn = len(lines)

    sizes = {model.path: model.size for model, _ in downloads}
    def progress(path, line, fraction, speed):
        with lock:
            if not tty:
                print(line, file=sys.stderr, flush=True)
                return
            clear()
            active[path] = (line, fraction, speed, sizes[path])
            render()

    def finish(model, message, success):
        nonlocal finished, successful_bytes
        with lock:
            clear()
            active.pop(model.path, None)
            finished += 1
            if success:
                successful_bytes += model.size
            print(message, file=sys.stderr, flush=True)
            if finished < len(downloads):
                render()

    failures = 0
    with ThreadPoolExecutor(max_workers=args.parallel_downloads) as pool:
        futures = {pool.submit(download, model, root, args.connections_per_download, progress): model
                   for model, _ in downloads}
        for future in as_completed(futures):
            model = futures[future]
            try:
                future.result()
                finish(model, f'COMPLETE {model.path}', True)
                continue
            except Exception as e:
                failures += 1
                # Avoid printing request URLs (which could contain credentials).
                detail = type(e).__name__
                if isinstance(e, ValueError):
                    message = str(e)
                    if message in ["unsafe download redirect","ZIP contains no file of expected size","ZIP contains no file with expected SHA-256","extracted model larger than expected","download SHA-256 mismatch"]:
                        detail += f': {message}'
                    elif message.startswith('download size mismatch (expected ') and message.endswith(' bytes)') and message.removeprefix('download size mismatch (expected ').removesuffix(' bytes)').replace(' bytes, got ', '').isdigit():
                        detail += f': {message}'
                elif isinstance(e, DownloadError):
                    detail += f': {e}'
                elif isinstance(getattr(e, 'code', None), int):
                    detail += f': HTTP {e.code}'
                finish(model, f'Error: download failed for {model.path}: {detail}', False)
    print(f'{matches} matches, {len(downloads) - failures} downloads completed, {failures} failed, {len(deletions)} deletions', flush=True)
    if failures:
        return 1
    return 0



def run(args):
    if args.command == 'check' and args.dry_run:
        return 0
    if args.database.suffix.lower() != '.xlsx':
        raise ValueError('manifest must have the .xlsx extension')
    if args.command == 'record':
        return record(args)
    manifest = args.database.resolve(strict=True)
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
    validate_disk_layout(catalog, models, files, dirs)
    if args.command == 'verify':
        return verify_models(root, models, files, args.dry_run)

    plan = plan_changes(args, models, files)
    show_plan(args, root, plan)
    if args.dry_run:
        return 0
    preflight_downloads(plan.downloads)
    # Free space by deleting extras before starting downloads.
    for rel in plan.deletions:
        files[rel].unlink()
    return run_downloads(args, root, plan)


def discover_db(script_dir):
    databases = sorted(p for p in script_dir.parent.glob('comfy-db*') if p.is_dir())
    if len(databases) != 1:
        raise ValueError(f'expected exactly one comfy-db* directory; found {len(databases)}')
    return databases[0]


def discover_database(script_dir):
    return discover_db(script_dir) / 'model-db.xlsx'


def resolve_defaults(args):
    script_dir = Path(__file__).resolve().parent.parent
    if args.database is None:
        args.database = discover_database(script_dir)
    if args.command != 'check' and args.models_dir is None:
        args.models_dir = script_dir.parent / 'comfy-models'
        if not args.models_dir.is_dir():
            raise ValueError(f'default models directory does not exist or is not a directory: {args.models_dir}; specify --directory')
    return args


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, epilog="Credentials: TOKEN_CA and TOKEN_HF in the environment, or token-db.env in a unique ../comfy-db* directory relative to the script.")
    parser.add_argument('command', nargs='?', choices=('update', 'add', 'remove', 'verify', 'check', 'record'), default='update', help='default: update; verify checks model files; check validates the XLSX only')
    parser.add_argument('--database', type=Path, help='XLSX manifest (default: model-db.xlsx in unique ../comfy-db* directory relative to script)')
    parser.add_argument('--directory', dest='models_dir', type=Path, help='dedicated models root (default: ../comfy-models relative to script)')
    parser.add_argument('--dry-run', action='store_true', help='print the plan without writes, downloads or hash checks; check --dry-run does nothing')
    parser.add_argument('--parallel-downloads', type=int, default=8)
    parser.add_argument('--connections-per-download', type=int, default=4)
    args = parser.parse_args(argv)
    if min(args.parallel_downloads, args.connections_per_download) < 1:
        parser.error('download concurrency must be positive')
    if args.command == 'check' and args.dry_run:
        return 0
    try:
        return run(resolve_defaults(args))
    except (OSError, ValueError, BadZipFile, ParseError, RuntimeError) as e:
        print(f'Error: {e}', file=sys.stderr)
        return 1

if __name__ == '__main__':
    sys.exit(main())
