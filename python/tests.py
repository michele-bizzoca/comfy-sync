#!/usr/bin/env python3
"""Regression tests for the model and extension catalog synchronizers."""

import contextlib
import hashlib
import importlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from openpyxl import Workbook

# Import the hyphenated script filenames using importlib.
sys.path.insert(0, str(Path(__file__).resolve().parent))
models = importlib.import_module("sync-models")
extensions = importlib.import_module("sync-extensions")


class WorkspaceTestCase(unittest.TestCase):
    """Reusable isolated workspace and XLSX/CLI helpers."""

    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)

    @staticmethod
    def write_workbook(path, headers, rows):
        workbook = Workbook()
        try:
            sheet = workbook.active
            sheet.append(headers)
            for row in rows:
                sheet.append(row)
            workbook.save(path)
        finally:
            workbook.close()

    @staticmethod
    def invoke(module, argv):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            status = module.main(argv)
        return status, output.getvalue()


class ModelTests(WorkspaceTestCase):
    """Test model manifests, sync modes, transfers and credential handling."""

    def setUp(self):
        super().setUp()
        credentials = patch.dict(os.environ, {"TOKEN_CA": "test-token", "TOKEN_HF": "test-token"})
        credentials.start()
        self.addCleanup(credentials.stop)
        self.root = self.base / "comfy-models"
        self.root.mkdir()
        self.manifest = self.base / "models.xlsx"
        self.data = b"good"
        self.row = [
            "Model", "1", "model.bin", len(self.data),
            hashlib.sha256(self.data).hexdigest(),
            "checkpoints", "https://huggingface.co/test/resolve/main/model.bin", "X",
        ]
        self.write(self.row)
        self.target = self.root / "checkpoints/model.bin"
        self.target.parent.mkdir()
        self.extra = self.root / "extra.bin"
        self.extra.write_bytes(b"extra")

    def write(self, *rows, headers=models.FIELDS):
        # The row fixture retains its historical order, not the XLSX header order.
        order = (0, 1, 5, 2, 3, 4, 6, 7)
        values = (
            [row[index] if index < len(row) else None for index in order] if row else []
            for row in rows
        )
        self.write_workbook(self.manifest, headers, values)

    def call(self, *args):
        return self.invoke(
            models, [*args, "--database", str(self.manifest), "--directory", str(self.root)]
        )

    def fake_download(self, model, root, connections=4, progress=None):
        target = root.joinpath(*model.path.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.data)

    def test_initial_and_final_summaries_include_matches(self):
        self.target.write_bytes(self.data)
        code, output = self.call("update")
        self.assertEqual(code, 0, output)
        self.assertIn("1 matches, 0 downloads, 1 deletions", output)
        self.assertIn("1 matches, 0 downloads completed", output)

    def test_smallest_models_download_first(self):
        larger = self.row.copy()
        larger[0], larger[2] = "Larger", "larger.bin"
        payload = b"more bytes"
        larger[3], larger[4] = len(payload), hashlib.sha256(payload).hexdigest()
        self.write(larger, self.row)
        order = []

        def download(model, root, connections, progress):
            order.append(model.path.name)
            target = root.joinpath(*model.path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.data if model.path.name == "model.bin" else payload)

        with patch.object(models, "download", side_effect=download):
            code, output = self.call("update", "--parallel-downloads", "1")
        self.assertEqual(code, 0, output)
        self.assertEqual(order, ["model.bin", "larger.bin"])
        self.assertFalse(self.extra.exists())

    def test_failed_transfer_preserves_partial(self):
        model = next(iter(models.load_manifest(self.manifest).values()))

        def transfer(_model, temp, _state, _scratch, _connections, _progress):
            temp.write_bytes(b"partial")
            raise models.DownloadError("connection interrupted")

        with patch.object(models, "transfer", side_effect=transfer):
            with self.assertRaisesRegex(models.DownloadError, "connection interrupted"):
                models.download(model, self.root)
        self.assertTrue(self.target.with_name("model.bin.part").is_file())
        self.assertFalse(self.target.exists())

    def test_case_insensitive_headers_preserve_values(self):
        row = self.row.copy()
        row[0] = 'MyModel'
        row[1] = 'V1-Beta'
        row[2] = 'Model.BIN'
        row[4] = row[4].upper()
        row[5] = 'CheckPoints'
        row[6] = 'https://huggingface.co/Test/resolve/Main/Model.BIN'
        row[7] = 'x'
        self.write(row, headers=[field.upper() for field in models.FIELDS])
        model = next(iter(models.load_manifest(self.manifest).values()))
        self.assertEqual((model.name, model.version), ('MyModel', 'V1-Beta'))
        self.assertEqual(str(model.path), 'CheckPoints/Model.BIN')
        self.assertEqual(model.url, row[6])
        self.assertEqual(model.sha256, row[4].lower())
        self.assertTrue(model.enabled)
        self.assertEqual(self.call('check')[0], 0)

    def test_blank_rows_and_numeric_cells(self):
        row = self.row.copy()
        row[1] = 2.5
        row[3] = 4
        row[7] = None
        self.write([], row, [' '])
        model = next(iter(models.load_manifest(self.manifest).values()))
        self.assertEqual(model.version, '2.5')
        self.assertEqual(model.size, 4)
        self.assertFalse(model.enabled)

    def test_case_insensitive_descriptive_duplicate_allowed(self):
        first = self.row.copy()
        first[1] = 'V1-Beta'
        second = first.copy()
        second[0] = 'mODEL'
        second[1] = 'v1-beta'
        second[2] = 'MODEL.BIN'
        second[7] = ''
        self.write(first, second)
        code, out = self.call('check')
        self.assertEqual(code, 0)
        self.assertEqual(len(models.load_manifest(self.manifest)), 2)

    def test_placeholder_identity_in_distinct_folders(self):
        first = self.row.copy()
        first[0] = first[1] = first[6] = '?'
        second = first.copy()
        second[5] = 'other'
        self.write(first, second)
        self.assertEqual(self.call('check')[0], 0)
        self.assertEqual(len(models.load_manifest(self.manifest)), 2)

    def test_unknown_link_sync(self):
        row = self.row.copy()
        row[6] = '?'
        self.write(row)
        with patch.object(models, 'download') as download:
            code, out = self.call('update')
            self.assertEqual(code, 1)
            self.assertIn('checkpoints/model.bin', out)
            self.assertIn('missing download link', out)
            self.assertTrue(self.extra.exists())
            self.target.write_bytes(self.data)
            self.assertEqual(self.call('update')[0], 0)
            download.assert_not_called()

    def test_unknown_link_disabled(self):
        row = self.row.copy()
        row[6] = '?'
        row[7] = ''
        self.write(row)
        with patch.object(models, 'download') as download:
            self.assertEqual(self.call('update')[0], 0)
            download.assert_not_called()

    def test_case_sensitive_physical_paths(self):
        second = self.row.copy()
        second[0] = 'Other'
        second[2] = 'Model.bin'
        self.write(self.row, second)
        self.assertEqual(len(models.load_manifest(self.manifest)), 2)
        self.assertEqual(self.call('check')[0], 0)

    def test_duplicate_header_ignoring_case(self):
        headers = list(models.FIELDS)
        headers[-1] = 'NAME'
        self.write(self.row, headers=headers)
        self.assertEqual(self.call('check')[0], 1)

    def test_default_and_dry_run(self):
        code, out = self.call('--dry-run')
        self.assertEqual(code, 0)
        self.assertIn('UPDATE', out)
        self.assertIn('DOWNLOAD', out)
        self.assertIn('DELETE', out)
        self.assertTrue(self.extra.exists())
        self.assertFalse(self.target.exists())
    def test_add(self):
        with patch.object(models, 'download', self.fake_download):
            self.assertEqual(self.call('add')[0], 0)
        self.assertEqual(self.target.read_bytes(), self.data)
        self.assertTrue(self.extra.exists())
    def test_remove(self):
        self.assertEqual(self.call('remove')[0], 0)
        self.assertFalse(self.extra.exists())
        self.assertFalse(self.target.exists())
    def test_sync(self):
        with patch.object(models, 'download', self.fake_download):
            self.assertEqual(self.call()[0], 0)
        self.assertFalse(self.extra.exists())
        self.assertEqual(self.target.read_bytes(), self.data)
    def test_verify(self):
        self.target.write_bytes(b'evil')
        self.assertNotIn('DOWNLOAD', self.call('add', '--dry-run')[1])
        with patch.object(models, 'download') as download:
            code, out = self.call('verify')
            download.assert_not_called()
        self.assertEqual(code, 1)
        self.assertIn('SHA-256 mismatch', out)
        self.assertIn('EXTRA', out)
        self.assertEqual(self.target.read_bytes(), b'evil')
        self.assertTrue(self.extra.exists())
    def test_verify_clean(self):
        self.target.write_bytes(self.data)
        self.extra.unlink()
        code, out = self.call('verify')
        self.assertEqual(code, 0)
        self.assertIn('1 valid', out)
    def test_verify_missing(self):
        code, out = self.call('verify')
        self.assertEqual(code, 1)
        self.assertIn('(missing)', out)
        self.assertFalse(self.target.exists())
        self.assertTrue(self.extra.exists())
    def test_verify_dry_run_missing(self):
        with patch.object(models, 'problem') as problem, patch.object(models, 'sha256') as sha:
            code, out = self.call('verify', '--dry-run')
            problem.assert_not_called()
            sha.assert_not_called()
        self.assertEqual(code, 0)
        self.assertIn('MISSING  checkpoints/model.bin', out)
        self.assertIn('EXTRA    extra.bin', out)
        self.assertIn('0 files to verify', out)
        self.assertFalse(self.target.exists())
        self.assertTrue(self.extra.exists())
    def test_verify_dry_run_present(self):
        self.target.write_bytes(b'evil')
        with patch.object(models, 'problem') as problem, patch.object(models, 'sha256') as sha, patch.object(models, 'download') as download:
            code, out = self.call('verify', '--dry-run')
            problem.assert_not_called()
            sha.assert_not_called()
            download.assert_not_called()
        self.assertEqual(code, 0)
        self.assertIn('VERIFY (DRY RUN)', out)
        self.assertIn('VERIFY   checkpoints/model.bin', out)
        self.assertIn('1 files to verify', out)
        self.assertNotIn('SHA-256 mismatch', out)
        self.assertEqual(self.target.read_bytes(), b'evil')
        self.assertTrue(self.extra.exists())
    def test_failed_download_deletes_extras_first(self):
        with patch.object(models, "download", side_effect=ValueError("failure")):
            code, output = self.call("update")
        self.assertEqual(code, 1, output)
        self.assertFalse(self.extra.exists())
        self.assertFalse(self.target.exists())
    def test_bad_manifest(self):
        for rows in ([], [self.row, self.row], [self.row[:5]+['../escape', *self.row[6:]]]):
            self.write(*rows)
            self.assertEqual(self.call('remove')[0], 1)
            self.assertTrue(self.extra.exists())
    def test_download_atomic(self):
        model = next(iter(models.load_manifest(self.manifest).values()))
        self.target.write_bytes(b"old")

        def transfer(payload):
            def write_partial(_model, temp, _state, _scratch, _connections, _progress):
                temp.write_bytes(payload)
            return write_partial

        with patch.object(models, "transfer", side_effect=transfer(b"bad!")):
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                models.download(model, self.root)
        self.assertEqual(self.target.read_bytes(), b"old")

        with patch.object(models, "transfer", side_effect=transfer(self.data)):
            models.download(model, self.root)
        self.assertEqual(self.target.read_bytes(), self.data)
        self.assertEqual(list(self.target.parent.iterdir()), [self.target])
    def test_direct_download_validates_bytes(self):
        model = next(iter(models.load_manifest(self.manifest).values()))

        def transfer(_model, temp, _state, _scratch, _connections, _progress):
            temp.write_bytes(self.data)

        with patch.object(models, "transfer", side_effect=transfer):
            models.download(model, self.root)
        self.assertEqual(self.target.read_bytes(), self.data)
    def test_zip_downloads_select_by_size_and_hash(self):
        model = next(iter(models.load_manifest(self.manifest).values()))
        cases = [
            ([("nested/unrelated-name.bin", self.data)], True),
            ([("wrong.bin", self.data)], True),
            ([("a/evil.bin", b"evil"), ("b/model.bin", self.data)], True),
            ([("model.bin", b"evil")], False),
            ([("model.bin", b"oversize")], False),
        ]
        for entries, valid in cases:
            with self.subTest(entries=entries):
                archive = io.BytesIO()
                with ZipFile(archive, "w") as bundle:
                    for name, payload in entries:
                        bundle.writestr(name, payload)
                self.target.write_bytes(b"old")

                def transfer(_model, temp, _state, _scratch, _connections, _progress):
                    temp.write_bytes(archive.getvalue())

                with patch.object(models, "transfer", side_effect=transfer):
                    if valid:
                        models.download(model, self.root)
                    else:
                        with self.assertRaises(ValueError):
                            models.download(model, self.root)
                self.assertEqual(self.target.read_bytes(), self.data if valid else b"old")
                self.assertEqual(list(self.target.parent.iterdir()), [self.target])
    def test_composite_identity_and_blank_version(self):
        first = self.row.copy()
        first[1] = ''
        second = first.copy()
        second[2] = 'other.bin'
        self.write(first, second)
        self.assertEqual(len(models.load_manifest(self.manifest)), 2)

    def test_symlink(self):
        (self.root / 'link').symlink_to(self.manifest)
        self.assertEqual(self.call('remove')[0], 1)
        self.assertTrue(self.extra.exists())

    def automatic_layout(self):
        script_dir = self.base / 'model-sync' / 'python'
        script_dir.mkdir(parents=True)
        db = self.base / 'comfy-db-personal'
        db.mkdir()
        xlsx_path = db / 'model-db.xlsx'
        xlsx_path.write_bytes(self.manifest.read_bytes())
        return script_dir / 'model_sync.py', db, xlsx_path

    def auto_call(self, script, *args):
        out = io.StringIO()
        with patch.object(models, '__file__', str(script)), contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            status = models.main(list(args))
        return status, out.getvalue()

    def test_automatic_paths(self):
        script, db, _ = self.automatic_layout()
        (db / 'README.md').write_text('ignored')
        (self.base / 'comfy-db-file').write_text('not a directory')
        (db / 'nested.xlsx').mkdir()
        (db / '~$model-db.xlsx').touch()
        code, out = self.auto_call(script, 'update', '--dry-run')
        self.assertEqual(code, 0, out)
        self.assertIn('DOWNLOAD', out)
        self.assertIn('DELETE', out)
        self.assertTrue(self.extra.exists())

    def test_missing_database(self):
        script, db, path = self.automatic_layout()
        path.unlink()
        db.rmdir()
        code, out = self.auto_call(script, 'remove')
        self.assertEqual(code, 1)
        self.assertIn('found 0', out)
        self.assertTrue(self.extra.exists())

    def test_multiple_databases(self):
        script, _, _ = self.automatic_layout()
        (self.base / 'comfy-db-other').mkdir()
        code, out = self.auto_call(script, 'remove')
        self.assertEqual(code, 1)
        self.assertIn('found 2', out)
        self.assertTrue(self.extra.exists())

    def test_missing_xlsx(self):
        script, _, path = self.automatic_layout()
        path.unlink()
        code, out = self.auto_call(script, 'remove')
        self.assertEqual(code, 1)
        self.assertIn('model-db.xlsx', out)
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
        code, out = self.auto_call(script, '--database', str(self.manifest), '--dry-run')
        self.assertEqual(code, 0, out)

    def test_explicit_models_dir_overrides_default(self):
        script, _, _ = self.automatic_layout()
        other = self.base / 'other-models'
        code, out = self.auto_call(script, '--directory', str(other), '--dry-run')
        self.assertEqual(code, 0, out)
        self.assertIn(str(other), out)
        self.assertFalse(other.exists())

    def test_missing_default_models_dir(self):
        script, _, _ = self.automatic_layout()
        self.root.rename(self.base / 'moved-models')
        code, out = self.auto_call(script, 'update')
        self.assertEqual(code, 1)
        self.assertIn('default models directory', out)
        self.assertFalse(self.root.exists())

    def test_token_file_and_environment_precedence(self):
        script, folder, _ = self.automatic_layout()
        (folder / "token-db.env").write_text(
            '# credentials\nTOKEN_CA=ca-file\nTOKEN_HF="hf-file"\n'
        )
        with patch.object(models, "__file__", str(script)), patch.dict(
            os.environ, {"TOKEN_CA": "ca-env"}, clear=True
        ):
            self.assertEqual(models.auth_token("https://civitai.com/api/download/1"), "ca-env")
            self.assertEqual(models.auth_token("https://huggingface.co/test"), "hf-file")
            self.assertIsNone(models.auth_token("https://example.com/test"))
        with patch.object(models, "__file__", str(script)), patch.dict(
            os.environ, {}, clear=True
        ):
            self.assertEqual(models.auth_token("https://civitai.red/api/download/1"), "ca-file")

    def test_token_parser_accepts_duplicates_and_generic_variables(self):
        script, folder, _ = self.automatic_layout()
        (folder / "token-db.env").write_text(
            'OTHER_KEY=any value\nexport TOKEN_CA=first\nTOKEN_CA="second"\n'
        )
        with patch.object(models, "__file__", str(script)), patch.dict(
            os.environ, {}, clear=True
        ):
            tokens = models.load_token_file(script.parent.parent)
            self.assertEqual(tokens["OTHER_KEY"], "any value")
            self.assertEqual(tokens["TOKEN_CA"], "second")
            self.assertEqual(models.auth_token("https://www.civitai.red/api/download/1"), "second")

    def test_redirect_strips_cross_origin_authorization(self):
        class Response:
            def __init__(self, status, headers=None):
                self.status_code = status
                self.headers = headers or {}
                self.closed = False

            def close(self):
                self.closed = True

        class Client:
            def __init__(self, responses):
                self.responses = iter(responses)
                self.requests = []

            def build_request(self, method, url, headers):
                return method, url, dict(headers)

            def send(self, request, stream):
                self.requests.append(request)
                return next(self.responses)

        redirected = Response(302, {"location": "https://cdn.example.org/file"})
        final = Response(200)
        client = Client([redirected, final])
        headers = {"Authorization": "Bearer secret", "Range": "bytes=0-0"}
        self.assertIs(models.stream(client, "https://civitai.red/file", headers), final)
        self.assertTrue(redirected.closed)
        self.assertEqual(client.requests[0][2]["Authorization"], "Bearer secret")
        self.assertNotIn("Authorization", client.requests[1][2])
        self.assertEqual(client.requests[1][2]["Range"], "bytes=0-0")

        same_origin = Client([Response(302, {"location": "/another"}), Response(200)])
        models.stream(same_origin, "https://civitai.red/file", headers)
        self.assertEqual(same_origin.requests[1][2]["Authorization"], "Bearer secret")

    def test_authentication_restricted_to_known_hosts(self):
        for url, expected in (
            ("https://civitai.com/models/1", "TOKEN_CA"),
            ("https://www.civitai.red/models/1", "TOKEN_CA"),
            ("https://huggingface.co/test", "TOKEN_HF"),
            ("https://example.com/test", None),
            ("https://civitai.com.evil.example/test", None),
        ):
            with self.subTest(url=url):
                self.assertEqual(models.token_key(url), expected)
    def test_missing_token_file_is_optional(self):
        script, _, _ = self.automatic_layout()
        with patch.object(models, '__file__', str(script)), patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(models.auth_token('https://huggingface.co/test'))
            (self.base / 'token-db-local').mkdir()
            self.assertIsNone(models.auth_token('https://huggingface.co/test'))

    def test_ambiguous_token_dirs_refused_before_mutation(self):
        script, _, _ = self.automatic_layout()
        (self.base / 'comfy-db-one').mkdir()
        (self.base / 'comfy-db-two').mkdir()
        with patch.dict(os.environ, {}, clear=True), patch.object(models, 'download') as download:
            code, out = self.auto_call(script, 'update')
            self.assertEqual(code, 1)
            self.assertIn('found 3', out)
            download.assert_not_called()
            self.assertTrue(self.extra.exists())
            self.assertEqual(self.auto_call(script, 'update', '--dry-run')[0], 1)

    def test_malformed_token_file_does_not_leak_secret(self):
        script, folder, _ = self.automatic_layout()
        (folder / "token-db.env").write_text(
            "TOKEN_HF=secret-one\nINVALID-NAME=secret-two\n"
        )
        with patch.dict(os.environ, {}, clear=True):
            code, output = self.auto_call(script, "update")
        self.assertEqual(code, 1)
        self.assertIn("line 2", output)
        self.assertNotIn("secret-", output)
        self.assertTrue(self.extra.exists())
    def test_disabled_model_sync(self):
        self.target.write_bytes(self.data)
        self.write(self.row[:-1] + [''])
        with patch.object(models, 'download') as download:
            code, out = self.call('update', '--dry-run')
            self.assertEqual(code, 0, out)
            self.assertIn('DELETE   checkpoints/model.bin', out)
            self.assertTrue(self.target.exists())
            self.assertEqual(self.call('update')[0], 0)
            download.assert_not_called()
        self.assertFalse(self.target.exists())
        self.assertFalse(self.extra.exists())

    def test_disabled_model_add(self):
        self.write(self.row[:-1] + [''])
        with patch.object(models, 'download') as download:
            self.assertEqual(self.call('add')[0], 0)
            self.assertFalse(self.target.exists())
            self.target.write_bytes(self.data)
            self.assertEqual(self.call('add')[0], 0)
            download.assert_not_called()
        self.assertTrue(self.target.exists())
        self.assertTrue(self.extra.exists())

    def test_disabled_model_remove(self):
        self.target.write_bytes(self.data)
        self.write(self.row[:-1] + [''])
        self.assertEqual(self.call('remove')[0], 0)
        self.assertFalse(self.target.exists())

    def test_disabled_model_verify(self):
        self.target.write_bytes(self.data)
        self.extra.unlink()
        self.write(self.row[:-1] + [''])
        with patch.object(models, 'sha256') as sha:
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
                model = next(iter(models.load_manifest(self.manifest).values()))
                self.assertEqual(model.enabled, expected)
                self.assertEqual(self.call('check')[0], 0)

    def test_enabled_rejects_other_markers(self):
        for value in ('true', 'false', '1', '0', 'XX', 'yes'):
            with self.subTest(value=value):
                self.write(self.row[:-1] + [value])
                code, out = self.call('update')
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
        with patch.object(models, 'download', self.fake_download):
            self.assertEqual(self.call('update')[0], 0)
        self.assertTrue(self.target.exists())
        self.assertFalse(other.exists())

    def test_record_models(self):
        old = self.row.copy()
        old[7] = ''
        missing = self.row.copy()
        missing[2] = 'missing.bin'
        self.write(old, missing)
        self.target.write_bytes(b'changed')
        self.assertEqual(self.call('record')[0], 0)
        catalog = models.load_manifest(self.manifest)
        self.assertEqual(len(catalog), 2)
        known = catalog[models.PurePosixPath('checkpoints/model.bin')]
        self.assertTrue(known.enabled)
        self.assertEqual((known.name, known.version, known.size, known.sha256, known.url),
                         (old[0], old[1], int(old[3]), old[4], old[6]))
        new = catalog[models.PurePosixPath('extra.bin')]
        self.assertEqual((new.name, new.version, new.url), ('?', '?', '?'))
        self.assertEqual(new.size, len(b'extra'))
        self.assertEqual(new.sha256, hashlib.sha256(b'extra').hexdigest())
        self.assertTrue(new.enabled)
        self.assertEqual(self.target.read_bytes(), b'changed')

    def test_record_new_and_empty_database(self):
        self.manifest.unlink()
        self.assertEqual(self.call('record')[0], 0)
        self.extra.unlink()
        self.assertEqual(self.call('record')[0], 0)
        self.assertEqual(models.load_manifest(self.manifest, allow_empty=True), {})
        self.assertEqual(self.call('record')[0], 0)

    def test_record_dry_run(self):
        original = self.manifest.read_bytes()
        with patch.object(models, 'sha256') as sha:
            self.assertEqual(self.call('record', '--dry-run')[0], 0)
            sha.assert_not_called()
        self.assertEqual(self.manifest.read_bytes(), original)

    def test_check_valid_without_models(self):
        self.write(self.row, ['Other', '1', 'other.bin', *self.row[3:-1], ''])
        with patch.object(models, 'scan') as scan, patch.object(models, 'download') as download, patch.object(models, 'auth_token') as token:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = models.main(['check', '--database', str(self.manifest)])
            self.assertEqual(code, 0)
            self.assertIn('2 entries, 1 enabled, 1 disabled', out.getvalue())
            scan.assert_not_called()
            download.assert_not_called()
            token.assert_not_called()
        self.assertTrue(self.extra.exists())

    def test_check_invalid(self):
        self.write(self.row, self.row)
        code, out = self.call('check')
        self.assertEqual(code, 1)
        self.assertIn('duplicate Folder + Filename', out)
        self.assertTrue(self.extra.exists())

    def test_check_dry_run_does_nothing(self):
        out = io.StringIO()
        with patch.object(models, 'resolve_defaults') as defaults, patch.object(models, 'load_manifest') as load, patch.object(models, 'scan') as scan:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                self.assertEqual(models.main(['check', '--dry-run']), 0)
                self.assertEqual(models.main(['check', '--dry-run', '--database', '/missing.xlsx']), 0)
            defaults.assert_not_called()
            load.assert_not_called()
            scan.assert_not_called()
        self.assertEqual(out.getvalue(), '')


class ExtensionTests(WorkspaceTestCase):
    """Test extension manifests, safe Git operations and requirements."""

    def setUp(self):
        super().setUp()
        self.root = self.base / "custom_nodes"
        self.root.mkdir()
        self.xlsx = self.base / "extension-db.xlsx"
        self.row = ["Example", "https://github.com/example/node.git", "a" * 40]
        self.write(self.row)

    def write(self, *rows, header=("Name", "Link", "Commit")):
        self.write_workbook(self.xlsx, header, rows)

    def call(self, *args):
        return self.invoke(
            extensions, [*args, "--database", str(self.xlsx), "--directory", str(self.root)]
        )

    def test_summary_before_plan(self):
        code, output = self.call("update", "--dry-run")
        self.assertEqual(code, 0, output)
        summary = "0 matches, 1 installations (1 new, 0 replacements), 0 deletions"
        self.assertIn(summary, output)
        self.assertLess(output.index(summary), output.index("INSTALL Example"))

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
        self.assertEqual(self.call('update')[0], 1)

    def test_bad_header(self):
        self.write(self.row, header=('Name', 'URL', 'Commit'))
        self.assertEqual(self.call('check')[0], 1)

    def test_dry_run(self):
        with patch.object(extensions, 'install') as install:
            code, out = self.call('update', '--dry-run')
            self.assertEqual(code, 0)
            self.assertIn('INSTALL Example', out)
            install.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_verify_missing(self):
        self.assertEqual(self.call('verify')[0], 1)
        self.assertEqual(self.call('verify', '--dry-run')[0], 0)

    def test_symlink_refused(self):
        (self.root / 'Example').symlink_to(self.base)
        self.assertEqual(self.call('update')[0], 1)

    def test_nonrepo_preserved(self):
        extra = self.root / 'unknown'
        extra.mkdir()
        (extra / 'data').write_text('keep')
        self.assertEqual(self.call('remove')[0], 1)
        self.assertTrue((extra / 'data').exists())

    def test_add_preserves_extra(self):
        extra = self.root / 'unknown'
        extra.mkdir()
        with patch.object(extensions, 'install') as install:
            self.assertEqual(self.call('add')[0], 0)
            install.assert_called_once()
        self.assertTrue(extra.exists())

    def test_failed_install_after_eager_deletion(self):
        extra = self.root / "unknown"
        extra.mkdir()
        with patch.object(extensions, "clean"), patch.object(
            extensions, "install", side_effect=RuntimeError("failed")
        ):
            code, output = self.call("update")
        self.assertEqual(code, 1, output)
        self.assertFalse(extra.exists())
        self.assertIn("installations pending", output)
    def test_builtin_preserved(self):
        builtin = self.root / 'websocket_image_save.py'
        builtin.write_text('keep')
        self.assertEqual(self.call('remove')[0], 0)
        self.assertTrue(builtin.exists())

    def test_dirty_repo_refused(self):
        target = self.root / 'Example'
        target.mkdir()
        (target / '.git').mkdir()
        with patch.object(extensions, 'git', side_effect=[' M file']):
            with self.assertRaises(ValueError):
                extensions.clean(target)

    def test_origin_normalization(self):
        self.assertEqual(extensions.origin(self.row[1]), extensions.origin(self.row[1][:-4]))

    def test_commit_mismatch(self):
        target = self.root / 'Example'
        target.mkdir()
        (target / '.git').mkdir()
        ext = extensions.load_manifest(self.xlsx)['Example']
        with patch.object(extensions, 'git', side_effect=[ext.url, 'b' * 40]):
            self.assertEqual(extensions.problem(ext, target), 'commit mismatch')

    def test_matching_repo(self):
        target = self.root / 'Example'
        target.mkdir()
        (target / '.git').mkdir()
        ext = extensions.load_manifest(self.xlsx)['Example']
        with patch.object(extensions, 'git', side_effect=[ext.url, ext.commit, '']):
            self.assertIsNone(extensions.problem(ext, target))

    def test_record(self):
        target = self.root / 'Example'
        target.mkdir()
        (target / '.git').mkdir()
        (self.root / 'nonrepo').mkdir()
        with patch.object(extensions, 'git', side_effect=[self.row[1], self.row[2]]):
            self.assertEqual(self.call('record')[0], 0)
        model = extensions.load_manifest(self.xlsx)['Example']
        self.assertEqual((model.name, model.url, model.commit), tuple(self.row))

    def test_record_dry_run(self):
        original = self.xlsx.read_bytes()
        target = self.root / 'Example'
        target.mkdir()
        (target / '.git').mkdir()
        with patch.object(extensions, 'git', side_effect=[self.row[1], self.row[2]]):
            self.assertEqual(self.call('record', '--dry-run')[0], 0)
        self.assertEqual(self.xlsx.read_bytes(), original)

    def test_record_new_database(self):
        self.xlsx.unlink()
        target = self.root / 'Example'
        target.mkdir()
        (target / '.git').mkdir()
        with patch.object(extensions, 'git', side_effect=[self.row[1], self.row[2]]):
            self.assertEqual(self.call('record')[0], 0)
        self.assertTrue(self.xlsx.is_file())

    def test_requirements_python(self):
        target = self.root / 'Example'
        target.mkdir()
        requirements = target / 'requirements.txt'
        requirements.touch()
        explicit = self.base / 'other-venv' / 'bin' / 'python'
        default = Path(extensions.__file__).resolve().parent.parent.parent / 'comfy-ui' / '.venv' / 'bin' / 'python'
        for options, expected in (([], default), (['--python', str(explicit)], explicit)):
            with self.subTest(options=options), patch.object(extensions, 'problem', return_value='missing'), patch.object(extensions, 'clean'), patch.object(extensions, 'install'), patch.object(extensions.subprocess, 'run') as run:
                self.assertEqual(self.call('add', *options)[0], 0)
                run.assert_called_once_with([str(expected), '-m', 'pip', 'install', '-r', str(requirements)], cwd=target, check=True)

    def test_missing_python_fails_requirements(self):
        target = self.root / 'Example'
        target.mkdir()
        (target / 'requirements.txt').touch()
        with patch.object(extensions, 'problem', return_value='missing'), patch.object(extensions, 'clean'), patch.object(extensions, 'install'):
            code, out = self.call('add', '--python', str(self.base / 'missing-python'))
        self.assertEqual(code, 1)
        self.assertIn('missing-python', out)

    def test_check_dry_run(self):
        with patch.object(extensions, 'load_manifest') as load:
            self.assertEqual(extensions.main(['check', '--dry-run']), 0)
            load.assert_not_called()

if __name__ == "__main__":
    unittest.main()
