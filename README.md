# Comfy Managers

## Layout

Place this repository alongside a single `comfy-db*` directory (for example `comfy-db-main`), `comfy-ui`, `comfy-models` and `comfy-extensions`. Install Python dependencies with `python -m pip install -r requirements.txt`.

## Sync managers

Both `comfy-mod-sync.py` and `comfy-ext-sync.py` accept:

- `--database PATH`: XLSX catalog; defaults to model-db.xlsx or extension-db.xlsx in the unique sibling comfy-db* directory.
- `--directory PATH`: managed directory; defaults to sibling comfy-models or comfy-extensions.
- `--dry-run`: plan without changes.

Commands: `update` (default, reconcile), `add` (install missing or mismatched entries), `remove` (remove extras), `verify` (inspect managed files), `check` (validate catalog only), `record` (rebuild catalog from installed files). A dry-run check does nothing.

Ext Sync additionally accepts `--python PATH` for installing requirements of installed or replaced extensions. Default: sibling comfy-ui/.venv/bin/python.

### Model catalog

Columns: Name, Version, Folder, Filename, Size, Hash, Link, Enable. Folder + Filename is the case-sensitive primary key. Name + Version is a partial descriptive identifier, may contain ? placeholders, and is not unique. Size is bytes; Hash is SHA-256; Enable is X or blank. Link is a direct HTTPS URL or ?. A required download with Link = ? fails before changes.

Record preserves existing cell values for matching paths except Enable (set to X), omits absent paths, and records new files with size, SHA-256 and ? for Name, Version and Link.

### Extension catalog

Columns: Name, Link, Commit. Record uses directory name, Git origin and full HEAD SHA. Non-repositories and unreadable origins are skipped with a warning. Catalog validation requires HTTPS URLs without credentials and full commit SHAs.

Both record commands replace the workbook atomically; formatting is not preserved. Empty recorded catalogs cannot be used for update until populated. Dry-run record does not write or hash model files.

## Tests

Run `python -m unittest -v test`. To validate real catalogs without modifying them:

```sh
python comfy-mod-sync.py check --database ../comfy-db-main/model-db.xlsx
python comfy-ext-sync.py check --database ../comfy-db-main/extension-db.xlsx
```

## Record installation

Run `./comfy-record.sh` to record installed extensions and models, copy ComfyUI settings back to config-db.json, and write installed ComfyUI, Python and Torch versions to version-db.env. ComfyUI HEAD must have exactly one local vX.Y.Z release tag; Python and CUDA Torch versions must match the setup format. All four outputs are staged before replacement; replacement is atomic per file, not across the whole set. Tokens are untouched.
