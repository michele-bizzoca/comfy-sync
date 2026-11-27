import contextlib
import importlib
import sys
from email.message import Message
from zipfile import ZipFile
import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parent))
m = importlib.import_module('comfy-mod-sync')

class Response(io.BytesIO):
    def __init__(self, data, filename='model.bin'):
        super().__init__(data)
        self.headers = Message()
        if filename is not None:
            self.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    def geturl(self):
        return 'https://example.com/model.bin'

class Tests(unittest.TestCase):
    def setUp(self):
        credentials = patch.dict(os.environ, {'HF_TOKEN': 'test-token', 'CA_TOKEN': 'test-token'})
        credentials.start()
        self.addCleanup(credentials.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'comfy-models'
        self.root.mkdir()
        self.manifest = self.base / 'models.xlsx'
        self.data = b'good'
        self.row = ['Model', '1', 'model.bin', '4', hashlib.sha256(self.data).hexdigest(), 'checkpoints', 'https://huggingface.co/test/resolve/main/model.bin', 'X']
        self.write(self.row)
        self.target = self.root / 'checkpoints/model.bin'
        self.target.parent.mkdir()
        self.extra = self.root / 'extra.bin'
        self.extra.write_bytes(b'extra')
    def write(self, *rows, headers=m.FIELDS):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(headers)
        for row in rows:
            sheet.append([row[i] if i < len(row) else None for i in (0, 1, 5, 2, 3, 4, 6, 7)] if row else [])
        workbook.save(self.manifest)
        workbook.close()
    def call(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            status = m.main([*args, '--xlsx', str(self.manifest), '--directory', str(self.root)])
        return status, out.getvalue()
    def fake_download(self, model, root):
        root.joinpath(*model.path.parts).write_bytes(self.data)
    def test_case_insensitive_headers_preserve_values(self):
        row = self.row.copy()
        row[0] = 'MyModel'; row[1] = 'V1-Beta'
        row[2] = 'Model.BIN'; row[4] = row[4].upper()
        row[5] = 'CheckPoints'; row[6] = 'https://huggingface.co/Test/resolve/Main/Model.BIN'; row[7] = 'x'
        self.write(row, headers=[field.upper() for field in m.FIELDS])
        model = next(iter(m.load_manifest(self.manifest).values()))
        self.assertEqual((model.name, model.version), ('MyModel', 'V1-Beta'))
        self.assertEqual(str(model.path), 'CheckPoints/Model.BIN')
        self.assertEqual(model.url, row[6]); self.assertEqual(model.sha256, row[4].lower())
        self.assertTrue(model.enabled)
        self.assertEqual(self.call('check')[0], 0)

    def test_blank_rows_and_numeric_cells(self):
        row = self.row.copy()
        row[1] = 2.5
        row[3] = 4
        row[7] = None
        self.write([], row, [' '])
        model = next(iter(m.load_manifest(self.manifest).values()))
        self.assertEqual(model.version, '2.5')
        self.assertEqual(model.size, 4)
        self.assertFalse(model.enabled)

    def test_case_insensitive_logical_duplicate(self):
        first = self.row.copy(); first[1] = 'V1-Beta'
        second = first.copy(); second[0] = 'mODEL'; second[1] = 'v1-beta'; second[2] = 'MODEL.BIN'; second[7] = ''
        self.write(first, second)
        code, out = self.call('check')
        self.assertEqual(code, 1); self.assertIn('duplicate name + version', out)

    def test_case_sensitive_physical_paths(self):
        second = self.row.copy(); second[0] = 'Other'; second[2] = 'Model.bin'
        self.write(self.row, second)
        self.assertEqual(len(m.load_manifest(self.manifest)), 2)
        self.assertEqual(self.call('check')[0], 0)

    def test_duplicate_header_ignoring_case(self):
        headers = list(m.FIELDS); headers[-1] = 'NAME'
        self.write(self.row, headers=headers)
        self.assertEqual(self.call('check')[0], 1)

    def test_default_and_dry_run(self):
        code, out = self.call('--dry-run')
        self.assertEqual(code, 0); self.assertIn('SYNC', out); self.assertIn('DOWNLOAD', out); self.assertIn('DELETE', out)
        self.assertTrue(self.extra.exists()); self.assertFalse(self.target.exists())
    def test_add(self):
        with patch.object(m, 'download', self.fake_download): self.assertEqual(self.call('add')[0], 0)
        self.assertEqual(self.target.read_bytes(), self.data); self.assertTrue(self.extra.exists())
    def test_remove(self):
        self.assertEqual(self.call('remove')[0], 0); self.assertFalse(self.extra.exists()); self.assertFalse(self.target.exists())
    def test_sync(self):
        with patch.object(m, 'download', self.fake_download): self.assertEqual(self.call()[0], 0)
        self.assertFalse(self.extra.exists()); self.assertEqual(self.target.read_bytes(), self.data)
    def test_verify(self):
        self.target.write_bytes(b'evil')
        self.assertNotIn('DOWNLOAD', self.call('add', '--dry-run')[1])
        with patch.object(m, 'download') as download:
            code, out = self.call('verify')
            download.assert_not_called()
        self.assertEqual(code, 1); self.assertIn('SHA-256 mismatch', out)
        self.assertIn('EXTRA', out); self.assertEqual(self.target.read_bytes(), b'evil'); self.assertTrue(self.extra.exists())
    def test_verify_clean(self):
        self.target.write_bytes(self.data); self.extra.unlink()
        code, out = self.call('verify')
        self.assertEqual(code, 0); self.assertIn('1 valid', out)
    def test_verify_missing(self):
        code, out = self.call('verify')
        self.assertEqual(code, 1); self.assertIn('(missing)', out)
        self.assertFalse(self.target.exists()); self.assertTrue(self.extra.exists())
    def test_verify_dry_run_missing(self):
        with patch.object(m, 'problem') as problem, patch.object(m, 'sha256') as sha:
            code, out = self.call('verify', '--dry-run')
            problem.assert_not_called(); sha.assert_not_called()
        self.assertEqual(code, 0)
        self.assertIn('MISSING  checkpoints/model.bin', out)
        self.assertIn('EXTRA    extra.bin', out)
        self.assertIn('0 files to verify', out)
        self.assertFalse(self.target.exists()); self.assertTrue(self.extra.exists())
    def test_verify_dry_run_present(self):
        self.target.write_bytes(b'evil')
        with patch.object(m, 'problem') as problem, patch.object(m, 'sha256') as sha, patch.object(m, 'download') as download:
            code, out = self.call('verify', '--dry-run')
            problem.assert_not_called(); sha.assert_not_called(); download.assert_not_called()
        self.assertEqual(code, 0)
        self.assertIn('VERIFY (DRY RUN)', out)
        self.assertIn('VERIFY   checkpoints/model.bin', out)
        self.assertIn('1 files to verify', out)
        self.assertNotIn('SHA-256 mismatch', out)
        self.assertEqual(self.target.read_bytes(), b'evil'); self.assertTrue(self.extra.exists())
    def test_failed_download_preserves_extras(self):
        with patch.object(m, 'download', side_effect=ValueError('failure')): self.assertEqual(self.call()[0], 1)
        self.assertTrue(self.extra.exists())
    def test_bad_manifest(self):
        for rows in ([], [self.row, self.row], [self.row[:5]+['../escape', *self.row[6:]]]):
            self.write(*rows); self.assertEqual(self.call('remove')[0], 1); self.assertTrue(self.extra.exists())
    def test_download_atomic(self):
        model = next(iter(m.load_manifest(self.manifest).values()))
        self.target.write_bytes(b'old')
        with patch.object(m, 'auth_token', return_value=None), patch.object(m, 'build_opener') as opener:
            opener.return_value.open.return_value = Response(b'bad!')
            with self.assertRaises(ValueError): m.download(model, self.root)
            self.assertEqual(self.target.read_bytes(), b'old')
            opener.return_value.open.return_value = Response(self.data)
            m.download(model, self.root)
            self.assertEqual(self.target.read_bytes(), self.data)
        self.assertEqual(list(self.target.parent.iterdir()), [self.target])
    def test_direct_filename(self):
        model = next(iter(m.load_manifest(self.manifest).values()))
        with patch.object(m, 'auth_token', return_value=None), patch.object(m, 'build_opener') as opener:
            for filename in ('wrong.bin', 'model.bin', None):
                opener.return_value.open.return_value = Response(self.data, filename)
                if filename == 'wrong.bin':
                    with self.assertRaisesRegex(ValueError, 'filename'):
                        m.download(model, self.root)
                    self.assertFalse(self.target.exists())
                else:
                    m.download(model, self.root)
                    self.assertEqual(self.target.read_bytes(), self.data)

    def test_zip_downloads(self):
        model = next(iter(m.load_manifest(self.manifest).values()))
        for entries, valid in [
            ([('nested/model.bin', self.data)], True),
            ([('wrong.bin', self.data)], False),
            ([('a/model.bin', self.data), ('b/model.bin', self.data)], False),
            ([('model.bin', b'evil')], False),
            ([('model.bin', b'oversize')], False),
        ]:
            with self.subTest(entries=entries):
                archive = io.BytesIO()
                with ZipFile(archive, 'w') as z:
                    for name, data in entries:
                        z.writestr(name, data)
                self.target.write_bytes(b'old')
                with patch.object(m, 'auth_token', return_value=None), patch.object(m, 'build_opener') as opener:
                    opener.return_value.open.return_value = Response(archive.getvalue(), 'archive.zip')
                    if valid:
                        m.download(model, self.root)
                    else:
                        with self.assertRaises(ValueError):
                            m.download(model, self.root)
                self.assertEqual(self.target.read_bytes(), self.data if valid else b'old')
                self.assertEqual(list(self.target.parent.iterdir()), [self.target])

    def test_composite_identity_and_blank_version(self):
        first = self.row.copy()
        first[1] = ''
        second = first.copy()
        second[2] = 'other.bin'
        self.write(first, second)
        self.assertEqual(len(m.load_manifest(self.manifest)), 2)

    def test_symlink(self):
        (self.root / 'link').symlink_to(self.manifest)
        self.assertEqual(self.call('remove')[0], 1); self.assertTrue(self.extra.exists())

    def automatic_layout(self):
        script_dir = self.base / 'model-sync'
        script_dir.mkdir()
        db = self.base / 'comfy-db-personal'
        db.mkdir()
        xlsx_path = db / 'model-db.xlsx'
        xlsx_path.write_bytes(self.manifest.read_bytes())
        return script_dir / 'model_sync.py', db, xlsx_path

    def auto_call(self, script, *args):
        out = io.StringIO()
        with patch.object(m, '__file__', str(script)), contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            status = m.main(list(args))
        return status, out.getvalue()

    def test_automatic_paths(self):
        script, db, _ = self.automatic_layout()
        (db / 'README.md').write_text('ignored')
        (self.base / 'comfy-db-file').write_text('not a directory')
        (db / 'nested.xlsx').mkdir()
        (db / '~$model-db.xlsx').touch()
        code, out = self.auto_call(script, 'sync', '--dry-run')
        self.assertEqual(code, 0, out)
        self.assertIn('DOWNLOAD', out); self.assertIn('DELETE', out)
        self.assertTrue(self.extra.exists())

    def test_missing_database(self):
        script, db, path = self.automatic_layout()
        path.unlink(); db.rmdir()
        code, out = self.auto_call(script, 'remove')
        self.assertEqual(code, 1); self.assertIn('found 0', out)
        self.assertTrue(self.extra.exists())

    def test_multiple_databases(self):
        script, _, _ = self.automatic_layout()
        (self.base / 'comfy-db-other').mkdir()
        code, out = self.auto_call(script, 'remove')
        self.assertEqual(code, 1); self.assertIn('found 2', out)
        self.assertTrue(self.extra.exists())

    def test_missing_xlsx(self):
        script, _, path = self.automatic_layout()
        path.unlink()
        code, out = self.auto_call(script, 'remove')
        self.assertEqual(code, 1); self.assertIn('model-db.xlsx', out)
        self.assertTrue(self.extra.exists())

    def test_multiple_xlsxs(self):
        script, db, _ = self.automatic_layout()
        (db / 'other.xlsx').write_bytes(self.manifest.read_bytes())
        code, out = self.auto_call(script, 'remove', '--dry-run')
        self.assertEqual(code, 0, out)
        self.assertTrue(self.extra.exists())

    def test_explicit_xlsx_overrides_discovery(self):
        script, db, _ = self.automatic_layout()
        (db / 'other.xlsx').write_bytes(self.manifest.read_bytes())
        code, out = self.auto_call(script, '--xlsx', str(self.manifest), '--dry-run')
        self.assertEqual(code, 0, out)

    def test_explicit_models_dir_overrides_default(self):
        script, _, _ = self.automatic_layout()
        other = self.base / 'other-models'
        code, out = self.auto_call(script, '--directory', str(other), '--dry-run')
        self.assertEqual(code, 0, out); self.assertIn(str(other), out)
        self.assertFalse(other.exists())

    def test_missing_default_models_dir(self):
        script, _, _ = self.automatic_layout()
        self.root.rename(self.base / 'moved-models')
        code, out = self.auto_call(script, 'sync')
        self.assertEqual(code, 1); self.assertIn('default models directory', out)
        self.assertFalse(self.root.exists())

    def test_token_file_and_environment_precedence(self):
        script, folder, _ = self.automatic_layout()
        (folder / 'token-db.env').write_text('# credentials\nCA_TOKEN=ca-file\nHF_TOKEN="hf-file"\n')
        with patch.object(m, '__file__', str(script)), patch.dict(os.environ, {'CA_TOKEN': 'ca-env'}, clear=True):
            self.assertEqual(m.auth_token('https://civitai.com/api/download/1'), 'ca-env')
            self.assertEqual(m.auth_token('https://huggingface.co/test'), 'hf-file')
            self.assertIsNone(m.auth_token('https://example.com/test'))
        with patch.object(m, '__file__', str(script)), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(m.auth_token('https://civitai.com/api/download/1'), 'ca-file')

    def test_missing_token_file_is_optional(self):
        script, _, _ = self.automatic_layout()
        with patch.object(m, '__file__', str(script)), patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(m.auth_token('https://huggingface.co/test'))
            (self.base / 'token-db-local').mkdir()
            self.assertIsNone(m.auth_token('https://huggingface.co/test'))

    def test_ambiguous_token_dirs_refused_before_mutation(self):
        script, _, _ = self.automatic_layout()
        (self.base / 'comfy-db-one').mkdir()
        (self.base / 'comfy-db-two').mkdir()
        with patch.dict(os.environ, {}, clear=True), patch.object(m, 'download') as download:
            code, out = self.auto_call(script, 'sync')
            self.assertEqual(code, 1); self.assertIn('found 3', out)
            download.assert_not_called()
            self.assertTrue(self.extra.exists())
            self.assertEqual(self.auto_call(script, 'sync', '--dry-run')[0], 1)

    def test_malformed_token_file_does_not_leak_secret(self):
        script, folder, _ = self.automatic_layout()
        (folder / 'token-db.env').write_text('HF_TOKEN=secret-one\nHF_TOKEN=secret-two\n')
        with patch.dict(os.environ, {}, clear=True):
            code, out = self.auto_call(script, 'sync')
            self.assertEqual(code, 1); self.assertIn('line 2', out)
            self.assertNotIn('secret-', out)
            self.assertTrue(self.extra.exists())

    def test_disabled_model_sync(self):
        self.target.write_bytes(self.data)
        self.write(self.row[:-1] + [''])
        with patch.object(m, 'download') as download:
            code, out = self.call('sync', '--dry-run')
            self.assertEqual(code, 0, out)
            self.assertIn('DELETE   checkpoints/model.bin', out)
            self.assertTrue(self.target.exists())
            self.assertEqual(self.call('sync')[0], 0)
            download.assert_not_called()
        self.assertFalse(self.target.exists()); self.assertFalse(self.extra.exists())

    def test_disabled_model_add(self):
        self.write(self.row[:-1] + [''])
        with patch.object(m, 'download') as download:
            self.assertEqual(self.call('add')[0], 0)
            self.assertFalse(self.target.exists())
            self.target.write_bytes(self.data)
            self.assertEqual(self.call('add')[0], 0)
            download.assert_not_called()
        self.assertTrue(self.target.exists()); self.assertTrue(self.extra.exists())

    def test_disabled_model_remove(self):
        self.target.write_bytes(self.data)
        self.write(self.row[:-1] + [''])
        self.assertEqual(self.call('remove')[0], 0)
        self.assertFalse(self.target.exists())

    def test_disabled_model_verify(self):
        self.target.write_bytes(self.data)
        self.extra.unlink()
        self.write(self.row[:-1] + [''])
        with patch.object(m, 'sha256') as sha:
            code, out = self.call('verify')
            self.assertEqual(code, 1)
            self.assertIn('EXTRA    checkpoints/model.bin', out)
            code, out = self.call('verify', '--dry-run')
            self.assertEqual(code, 0)
            self.assertIn('EXTRA    checkpoints/model.bin', out)
            sha.assert_not_called()
        self.target.unlink()
        self.assertEqual(self.call('verify')[0], 0)

    def test_enabled_marker_cases_and_empty(self):
        for value, expected in [('X', True), ('x', True), ('', False)]:
            with self.subTest(value=value):
                self.write(self.row[:-1] + [value])
                model = next(iter(m.load_manifest(self.manifest).values()))
                self.assertEqual(model.enabled, expected)
                self.assertEqual(self.call('check')[0], 0)

    def test_enabled_rejects_other_markers(self):
        for value in ('true', 'false', '1', '0', 'XX', 'yes'):
            with self.subTest(value=value):
                self.write(self.row[:-1] + [value])
                code, out = self.call('sync')
                self.assertEqual(code, 1)
                self.assertIn('enabled must be X', out)
                self.assertTrue(self.extra.exists())

    def test_enabled_validation_and_uniqueness(self):
        for rows in ([self.row[:-1] + ['maybe']], [self.row, self.row[:-1] + ['']]):
            self.write(*rows)
            self.assertEqual(self.call('remove')[0], 1)
            self.assertTrue(self.extra.exists())

    def test_mixed_catalog(self):
        disabled = ['Other', '1', 'other.bin', *self.row[3:-1], '']
        self.write(self.row, disabled)
        other = self.target.parent / 'other.bin'
        other.write_bytes(self.data)
        with patch.object(m, 'download', self.fake_download):
            self.assertEqual(self.call('sync')[0], 0)
        self.assertTrue(self.target.exists()); self.assertFalse(other.exists())

    def test_check_valid_without_models(self):
        self.write(self.row, ['Other', '1', 'other.bin', *self.row[3:-1], ''])
        with patch.object(m, 'scan') as scan, patch.object(m, 'download') as download, patch.object(m, 'auth_token') as token:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = m.main(['check', '--xlsx', str(self.manifest)])
            self.assertEqual(code, 0)
            self.assertIn('2 entries, 1 enabled, 1 disabled', out.getvalue())
            scan.assert_not_called(); download.assert_not_called(); token.assert_not_called()
        self.assertTrue(self.extra.exists())

    def test_check_invalid(self):
        self.write(self.row, self.row)
        code, out = self.call('check')
        self.assertEqual(code, 1)
        self.assertIn('duplicate name + version', out)
        self.assertTrue(self.extra.exists())

    def test_check_dry_run_does_nothing(self):
        out = io.StringIO()
        with patch.object(m, 'resolve_defaults') as defaults, patch.object(m, 'load_manifest') as load, patch.object(m, 'scan') as scan:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                self.assertEqual(m.main(['check', '--dry-run']), 0)
                self.assertEqual(m.main(['check', '--dry-run', '--xlsx', '/missing.xlsx']), 0)
            defaults.assert_not_called(); load.assert_not_called(); scan.assert_not_called()
        self.assertEqual(out.getvalue(), '')


class ExtensionTests(unittest.TestCase):
    def setUp(self):
        self.ext = importlib.import_module('comfy-ext-sync')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'custom_nodes'
        self.root.mkdir()
        self.xlsx = self.base / 'extension-db.xlsx'
        self.row = ['Example', 'https://github.com/example/node.git', 'a' * 40]
        self.write(self.row)

    def write(self, *rows, header=('Name', 'Link', 'Commit')):
        w = Workbook()
        w.active.append(header)
        for row in rows:
            w.active.append(row)
        w.save(self.xlsx)
        w.close()

    def call(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = self.ext.main([*args, '--xlsx', str(self.xlsx), '--directory', str(self.root)])
        return code, out.getvalue()

    def test_check(self):
        self.assertEqual(self.call('check')[0], 0)

    def test_header_order(self):
        self.write(self.row[::-1], header=('Commit', 'Link', 'Name'))
        self.assertEqual(self.call('check')[0], 0)

    def test_invalid_rows(self):
        for row in (['../escape', *self.row[1:]], ['a/b', *self.row[1:]],
                    ['Example', 'http://example.org/repo', self.row[2]],
                    ['Example', 'https://user@example.org/repo', self.row[2]],
                    ['Example', self.row[1], 'main'],
                    ['Example', self.row[1], '=1+1'],
                    [*self.row, 'unexpected']):
            with self.subTest(row=row):
                self.write(row)
                self.assertEqual(self.call('check')[0], 1)

    def test_duplicate(self):
        self.write(self.row, ['example', *self.row[1:]])
        self.assertEqual(self.call('check')[0], 1)

    def test_empty_manifest(self):
        self.write()
        self.assertEqual(self.call('sync')[0], 1)

    def test_bad_header(self):
        self.write(self.row, header=('Name', 'URL', 'Commit'))
        self.assertEqual(self.call('check')[0], 1)

    def test_dry_run(self):
        with patch.object(self.ext, 'install') as install:
            code, out = self.call('sync', '--dry-run')
            self.assertEqual(code, 0)
            self.assertIn('INSTALL Example', out)
            install.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_verify_missing(self):
        self.assertEqual(self.call('verify')[0], 1)
        self.assertEqual(self.call('verify', '--dry-run')[0], 0)

    def test_symlink_refused(self):
        (self.root / 'Example').symlink_to(self.base)
        self.assertEqual(self.call('sync')[0], 1)

    def test_nonrepo_preserved(self):
        extra = self.root / 'unknown'
        extra.mkdir()
        (extra / 'data').write_text('keep')
        self.assertEqual(self.call('remove')[0], 1)
        self.assertTrue((extra / 'data').exists())

    def test_add_preserves_extra(self):
        extra = self.root / 'unknown'
        extra.mkdir()
        with patch.object(self.ext, 'install') as install:
            self.assertEqual(self.call('add')[0], 0)
            install.assert_called_once()
        self.assertTrue(extra.exists())

    def test_failed_install_preserves_extra(self):
        extra = self.root / 'unknown'
        extra.mkdir()
        with patch.object(self.ext, 'clean'), patch.object(self.ext, 'install', side_effect=RuntimeError('failed')):
            self.assertEqual(self.call('sync')[0], 1)
        self.assertTrue(extra.exists())

    def test_builtin_preserved(self):
        builtin = self.root / 'websocket_image_save.py'
        builtin.write_text('keep')
        self.assertEqual(self.call('remove')[0], 0)
        self.assertTrue(builtin.exists())

    def test_dirty_repo_refused(self):
        target = self.root / 'Example'
        target.mkdir()
        (target / '.git').mkdir()
        with patch.object(self.ext, 'git', side_effect=[' M file']):
            with self.assertRaises(ValueError):
                self.ext.clean(target)

    def test_origin_normalization(self):
        self.assertEqual(self.ext.origin(self.row[1]), self.ext.origin(self.row[1][:-4]))

    def test_commit_mismatch(self):
        target = self.root / 'Example'
        target.mkdir()
        (target / '.git').mkdir()
        ext = self.ext.load_manifest(self.xlsx)['Example']
        with patch.object(self.ext, 'git', side_effect=[ext.url, 'b' * 40]):
            self.assertEqual(self.ext.problem(ext, target), 'commit mismatch')

    def test_matching_repo(self):
        target = self.root / 'Example'
        target.mkdir()
        (target / '.git').mkdir()
        ext = self.ext.load_manifest(self.xlsx)['Example']
        with patch.object(self.ext, 'git', side_effect=[ext.url, ext.commit, '']):
            self.assertIsNone(self.ext.problem(ext, target))

    def test_check_dry_run(self):
        with patch.object(self.ext, 'load_manifest') as load:
            self.assertEqual(self.ext.main(['check', '--dry-run']), 0)
            load.assert_not_called()

if __name__ == '__main__': unittest.main()
