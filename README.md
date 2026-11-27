# Comfy Managers

A Linux deployment and inventory toolkit for ComfyUI. The database describes the desired installation; the managed directories contain its actual state. Setup provisions an installation, Update reconciles it, Record captures it, and Start launches it.

## Directory layout

All paths below are relative to the parent of this repository, not the shell's working directory.

```text
workspace/
  comfy-managers/
    comfy-setup.sh
    comfy-update.sh
    comfy-record.sh
    comfy-start.sh
    comfy-sync-models.py
    comfy-sync-extensions.py
    requirements.txt
    test.py
  comfy-db-main/
    model-db.xlsx
    extension-db.xlsx
    config-db.json
    version-db.env
    token-db.env
  comfy-ui/
    .venv/
    user/default/comfy.settings.json
    comfy-extension-paths.yaml
  comfy-models/
  comfy-extensions/
  comfy-input/
  comfy-output/
```

Exactly one sibling directory matching `comfy-db*` is required for automatic discovery. Explicit database paths override catalog discovery in the Python managers, but model authentication may still require the sibling token database. Use dedicated managed directories: update and remove can delete unlisted entries. Do not store unrelated files there.

## Quick start

Requirements: Linux (Setup targets Debian/Ubuntu with apt and dpkg), Bash, Git, NVIDIA driver, network access, Python with venv support, sufficient storage, and valid database files. The Python managers require openpyxl >=3.1,<4.

```sh
bash comfy-setup.sh
bash comfy-start.sh 8188
```

For an existing installation:

```sh
bash comfy-update.sh
bash comfy-record.sh
```

Record changes the database, not the installation. Review its output and fill unknown model metadata before deploying elsewhere. No shell script automatically commits or pushes the database.

## Database reference

### Common XLSX conventions

Both catalogs use the first worksheet. The first row contains exactly the required column names; order is flexible and matching ignores case and surrounding whitespace. Blank rows are ignored. Extra populated cells, formulas and Excel error cells are rejected. Normal check/update/add/remove/verify reject empty catalogs as a deletion safeguard. Record can create a header-only catalog, which must be populated before normal use. Save as .xlsx, not CSV or .xls. Keep hashes, commits and identifiers as text. Record rebuilds the workbook; styles, widths, other worksheets and formatting are not preserved.

### model-db.xlsx

One row per physical model file.

| Column | Type | Rules |
| --- | --- | --- |
| Name | Text | Required descriptive model name; ? allowed. |
| Version | Text | Descriptive version; blank or ? allowed. |
| Folder | Text | Relative POSIX directory under comfy-models; blank for root. Nested paths use /. |
| Filename | Text | Required single filename including extension. |
| Size | Integer | Nonnegative decimal byte count, not MB/GB. |
| Hash | Text | Exactly 64 hexadecimal SHA-256 characters; case insensitive. |
| Link | Text | Direct HTTPS download URL without embedded username/password, or ?. |
| Enable | Text | X or x enables the row; blank disables it. Other markers rejected. |

Primary key: **Folder + Filename**, case-sensitive. Name + Version is a partial descriptive identifier, not a unique key. Two files with identical names in different folders are valid even if both descriptive identifiers are ?. Duplicate paths and file/directory conflicts are invalid. Absolute paths, empty path components, dot/dot-dot components, backslashes, colons and NUL are rejected.

Illustrative row (hash and URL are placeholders for demonstration; calculate the actual hash and use a real URL):

| Name | Version | Folder | Filename | Size | Hash | Link | Enable |
| --- | --- | --- | --- | ---: | --- | --- | --- |
| Example | 1 | checkpoints | example.safetensors | 123456 | 0000000000000000000000000000000000000000000000000000000000000000 | ? | X |

Calculate metadata on Linux:

```sh
stat -c %s example.safetensors
sha256sum example.safetensors
```

A disabled model is outside the desired mirror: update/remove may delete its local file; verify reports it as extra. Add does not delete it. Link = ? is valid for catalog validation and for an already present file of the expected size. If an enabled file needs downloading (missing or wrong size), update/add fail before mutation, including dry-run. Verify checks SHA-256 independently of Link.

Downloads accept a direct file with matching remote filename or a ZIP containing exactly one member whose basename matches Filename. Size and SHA-256 must match before atomic replacement. HTTPS redirects are checked and authorization is stripped across origins. Extras are deleted only after all downloads succeed. Existing same-size files are not hashed by update/add; use verify to detect corruption.

Record scans recursively, including ordinary non-model files. Existing paths retain all catalog cell values except Enable, which becomes X; it does not refresh their size or hash. New paths get actual size and SHA-256, Name/Version/Link = ?, Enable = X. Missing paths are omitted. Dry-run lists changes without hashing new files or writing the workbook. Internal symlinks are refused.

### extension-db.xlsx

One row per standalone Git extension repository.

| Column | Type | Rules |
| --- | --- | --- |
| Name | Text | Directory name; ASCII letters/digits/underscore initially, then letters/digits/underscore/dot/hyphen. Case-insensitive uniqueness. |
| Link | Text | HTTPS Git repository URL, no userinfo, query or fragment. |
| Commit | Text | Full 40-character hexadecimal Git commit SHA, not a branch or tag. |

Example (illustrative, not a real commit):

| Name | Link | Commit |
| --- | --- | --- |
| ComfyUI-Example | https://github.com/example/ComfyUI-Example.git | 0123456789abcdef0123456789abcdef01234567 |

Retrieve metadata:

```sh
git -C ../comfy-extensions/ComfyUI-Example remote get-url origin
git -C ../comfy-extensions/ComfyUI-Example rev-parse HEAD
```

There is no Enable column. All rows are desired extensions. The manager compares origin (ignoring trailing slash and .git), HEAD and local changes. It clones/fetches the pinned commit and checks out detached HEAD. Replacement/removal refuses non-standalone repositories and repositories with tracked, untracked or ignored changes. Symlinks are refused. Built-in entries __pycache__, example_node.py.example and websocket_image_save.py are skipped during filesystem reconciliation; do not list them in the catalog.

Requirements are installed only for extensions installed or replaced in that invocation, using the selected Python executable. An installation or pip failure can leave earlier changes applied; the operation is not a global transaction.

Record reads each immediate standalone Git repository's directory name, origin and HEAD. Non-repositories and unreadable origins are skipped with warnings. It does not capture uncommitted changes, local files or dependency versions. Unsupported origins (such as SSH URLs) fail catalog validation before replacement. Missing extensions are omitted.

### config-db.json

A copy of ComfyUI's native settings JSON at `comfy-ui/user/default/comfy.settings.json`. Its schema belongs to ComfyUI and installed extensions; the managers do not define or validate individual keys. Setup and Update copy database to installation; Record copies installation to database. This is a whole-file replacement, not a merge. Workflows, outputs and other user files are not captured.

### version-db.env

Shell-compatible assignments, required by Setup and Update. Example syntax (illustrative versions; choose an actual compatible release combination):

```dotenv
COMFY_VERSION=0.3.50
PYTHON_VERSION=3.12
TORCH_VERSION=2.7.1+cu128
```

| Variable | Format | Meaning |
| --- | --- | --- |
| COMFY_VERSION | X.Y.Z | ComfyUI release tag vX.Y.Z, without v in the value. |
| PYTHON_VERSION | 3.Y | Python major/minor, not patch version. |
| TORCH_VERSION | X.Y.Z+cuNNN | Exact CUDA PyTorch build, including CUDA suffix. |

Setup fetches the release tag from Comfy-Org/ComfyUI, checks out its commit, installs the requested Python/Torch and checks GPU availability. The driver must support at least the requested CUDA version. Update compares HEAD with the upstream release tag and reads Python/Torch versions from the venv; it reports mismatch without automatically changing these versions. Record requires exactly one local release tag vX.Y.Z pointing at HEAD, and reads actual Python/Torch versions. CPU-only or untagged builds cannot be represented by the current schema.

### token-db.env

Optional for model downloads when credentials are not needed or are already supplied through the environment; required by Start because it sources the file. Supported keys:

```dotenv
CA_TOKEN=
HF_TOKEN=
```

CA_TOKEN authenticates civitai.com and www.civitai.com; HF_TOKEN authenticates huggingface.co and www.huggingface.co. Model downloads prefer a nonempty environment variable over the file. The model parser accepts blank lines, comments, KEY=VALUE and optionally matching quotes; it rejects unknown/duplicate keys and whitespace or quote characters inside values. It does not execute shell code. Start sources this file as shell code and exports its variables; keep it trusted and use simple assignments. Version files are also sourced by shell scripts. Never commit actual secrets to a public repository or embed them in catalog URLs. Record leaves tokens untouched.

## Python command reference

```sh
python comfy-sync-models.py [update|add|remove|verify|check|record] [options]
python comfy-sync-extensions.py [update|add|remove|verify|check|record] [options]
```

| Mode | Models | Extensions |
| --- | --- | --- |
| update (default) | Download missing/wrong-size enabled files; delete extras. | Install missing/mismatched repos; remove extras. |
| add | Download only. | Install/replace only. |
| remove | Delete extras only. | Remove extra clean repositories only. |
| verify | Check presence, size, SHA-256 and extras. | Check origin, HEAD, local changes and extras. |
| check | Validate XLSX schema and rows only. | Validate XLSX schema and rows only. |
| record | Rebuild inventory from disk. | Rebuild inventory from Git repositories. |

| Option | Default / scope |
| --- | --- |
| --database PATH | model-db.xlsx or extension-db.xlsx in unique sibling comfy-db* |
| --directory PATH | sibling comfy-models or comfy-extensions |
| --dry-run | No writes, downloads, installs or deletions. Model verify skips hashes; extension verify still inspects Git. Check with dry-run is a no-op. |
| --python PATH | Extensions only; sibling comfy-ui/.venv/bin/python for requirements installation. |
| --help | CLI usage |

Explicit paths are interpreted relative to the invoking working directory if not absolute. Automatic paths are relative to the script location. Catalog must be outside the managed directory for filesystem operations. Root, home and current working directory are refused as managed roots. Use the venv interpreter for normal operation.

Examples:

```sh
python comfy-sync-models.py check
python comfy-sync-models.py update --dry-run
python comfy-sync-models.py verify
python comfy-sync-models.py record --database /tmp/model-db.xlsx
python comfy-sync-extensions.py add --python ../comfy-ui/.venv/bin/python
python comfy-sync-extensions.py verify --directory ../comfy-extensions
```

Exit status: 0 success (including dry-run plans), 1 operational/validation failure or real verify discrepancies, 2 argparse usage errors.

## Shell managers

### comfy-setup.sh

Provisions the installation from the database. Checks driver compatibility, fetches/checks out ComfyUI release, moves initial models/custom_nodes/input/output directories out when applicable, creates managed directories and venv, installs pinned CUDA Torch and requirements plus additional runtime dependencies, writes Torch constraints and extension-path YAML inside comfy-ui, updates extensions, runs pip check and CUDA smoke test, updates models and copies settings. Stops on error. Not a transactional installer; back up existing installations before running.

### comfy-update.sh

Uses the existing venv and Torch constraints. Updates extensions and models, copies settings from database, then checks ComfyUI/Python/Torch versions. Attempts subsequent stages even if an earlier stage fails; returns nonzero if any stage fails. Does not automatically upgrade core versions. A version mismatch is reported after reconciliation, not as a preflight guard.

### comfy-record.sh

Reverse inventory operation: checks local release tag and actual venv versions, stages existing model/extension catalogs, invokes both record modes, copies installed settings and writes version-db.env. Publishes all four staged files only after successful preparation. Each file replacement is atomic, but the four replacements are not one transaction. Token file is unchanged. No dry-run option on the shell wrapper; use individual Python record dry-runs to preview. Stop ComfyUI or avoid concurrent modifications during capture.

### comfy-start.sh

Launches ComfyUI with the venv interpreter, external model/input/output directories, generated extension-path YAML, manager enabled, automatic previews and DETAIL verbosity. Sources and exports token-db.env. Optional first positional argument sets port (default 8188). Binds to 0.0.0.0; use firewall/access controls rather than exposing the service directly to the internet.

```sh
bash comfy-start.sh 8190
```

## Maintenance and safety

- Use check to validate edited catalogs, then update --dry-run to inspect planned changes.
- Use verify after deployment; model update alone cannot detect same-size corruption.
- Before record, preserve a database backup or commit. Record drops missing entries and resets workbook formatting.
- A recorded model with unknown Link cannot be restored on a fresh machine until the URL is supplied.
- Keep extension repositories clean before update/remove. Record is not a backup of local code modifications.
- Protect credentials and restrict access to the ComfyUI listener.
- Avoid concurrent manager runs or edits to managed files; there is no global locking or rollback.
- The database is an inventory, not a full backup or complete dependency lockfile.

## Tests

```sh
python -m pip install -r requirements.txt
python -m unittest -v test
bash -n comfy-setup.sh comfy-update.sh comfy-record.sh comfy-start.sh
```

Validate real catalogs without modifying them:

```sh
python comfy-sync-models.py check --database ../comfy-db-main/model-db.xlsx
python comfy-sync-extensions.py check --database ../comfy-db-main/extension-db.xlsx
```

The unit suite covers catalog validation, discovery, dry-run, record, download handling, extension reconciliation and requirements interpreter selection. A real GPU deployment and shell integration tests are separate from the Python unit suite.
