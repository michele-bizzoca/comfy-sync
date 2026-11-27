# Comfy Sync

**Your ComfyUI environment, reproducible by design.**

A new machine should not mean starting from scratch. Keep your models, extensions, settings and runtime version pins in a database, then use Comfy Sync to rebuild your working environment, reconcile changes or capture a new snapshot.

Move between machines, recover a working installation, or provision a fresh GPU instance without manually tracking every model and extension. Keep the installation separate from the inventory that describes it.

## How it works

Comfy Sync is a command-line deployment and inventory toolkit. Its primary target is Linux with an NVIDIA GPU.

| Command | Purpose |
| --- | --- |
| `setup` | Provision ComfyUI and its runtime, then synchronize assets and settings. |
| `update` | Reconcile assets and settings and report runtime version mismatches. |
| `record` | Capture the current installation into the database. |
| `start` | Launch ComfyUI with managed directories and forwarded launch options. |

Five independent synchronizers provide finer control:

| Synchronizer | Database | Responsibility |
| --- | --- | --- |
| `sync-models` | model-db.xlsx | Model inventory, download, integrity verification and reconciliation. |
| `sync-extensions` | extension-db.xlsx | Git extension inventory and pinned checkouts. |
| `sync-versions` | version-db.env | Validate, compare and record runtime versions. |
| `sync-config` | config-db.json | Copy native ComfyUI settings in either direction. |
| `sync-tokens` | token-db.env | Validate literal credential assignments locally. |

The database describes the desired installation; the managed directories hold its actual state. Setup and Update work toward the desired state; Record captures the installed state. Start runs the installation without reconciling it.

Models and extensions are stored outside the ComfyUI checkout. The model downloader supports parallel transfers, resumable ranged downloads where supported, SHA-256 verification and ZIP extraction. Extensions are pinned to exact Git commits. This is an inventory-based deployment tool, not a complete backup or a fully locked dependency environment.

> **Important:** Update and remove can delete files or repositories absent from the desired inventory. Use dedicated managed directories, inspect dry-run plans and maintain backups.

## Technical guide

## Directory layout

All paths below are relative to the parent of this repository, not the shell's working directory.

```text
workspace/
  comfy-sync/
    comfy-sync.sh
    comfy-sync.bat
    requirements.txt
    README.md
    LICENSE
    python/
      setup.py
      update.py
      record.py
      start.py
      sync-models.py
      sync-extensions.py
      sync-versions.py
      sync-config.py
      sync-tokens.py
      tests.py
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

## Quick start and platform support

Requirements: Linux (Setup targets Debian/Ubuntu with apt and dpkg), Bash, Git, NVIDIA driver, network access, Python with venv support, sufficient storage, and valid database files. The Python managers require openpyxl >=3.1,<4; model downloads also require httpx, installed by setup. The Bash dispatcher targets Linux and modern macOS with realpath. A Windows BAT dispatcher is supplied, but the Python implementation still assumes Unix paths and shell utilities in several places. Full setup on macOS or Windows is not supported or tested; the current version schema requires CUDA.

```sh
./comfy-sync.sh setup
./comfy-sync.sh start
```

For an existing installation:

```sh
./comfy-sync.sh update
./comfy-sync.sh record
```

Record changes the database, not the installation. Review its output and fill unknown model metadata before deploying elsewhere. No command automatically commits or pushes the database.

## Command-line help

Every Python entry point supports `-h` and `--help`. Both dispatchers provide general help when invoked without a command keyword, and forward command-specific help requests to the corresponding Python script.

```sh
./comfy-sync.sh --help
./comfy-sync.sh setup -h
./comfy-sync.sh sync-models --help
./comfy-sync.sh start --help
```

On Windows, the equivalent dispatcher syntax is:

```bat
comfy-sync.bat --help
comfy-sync.bat sync-extensions --help
```

Python entry points can also be invoked directly:

```sh
python python/sync-versions.py --help
python python/tests.py --help
```

Use the managed Python interpreter where required. The Start help describes the launcher and its forwarding behavior; consult ComfyUI's own help for its complete native option list. The test runner uses the standard unittest help.

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

Downloads default to eight concurrent models and four connections per model, using 16 MiB ranged segments. Partial data and metadata can be retained after network failures; cross-run range reuse requires a strong ETag. Servers without range support fall back to a sequential transfer. Completed payloads are validated before publication. Download progress is reported on stderr with transferred MiB, average speed, and a percentage bar when HTTP Content-Length is available. Interactive terminals refresh in place; redirected logs receive periodic lines. Downloads accept direct files regardless of remote filename, or ZIP archives whose non-directory members are filtered by uncompressed size and checked by SHA-256. The first matching member is selected. Size and SHA-256 must match before atomic replacement at the catalog Folder/Filename destination. HTTPS redirects are checked and authorization is stripped across origins. Extras are deleted before downloads begin, after preflight validation. A subsequent download failure does not restore deleted files. Existing same-size files are not hashed by update/add; use verify to detect corruption.

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

Requirements are installed only for extensions installed or replaced in that invocation, using the selected Python executable. After preflight cleanliness checks, extra repositories are removed before any cloning or requirements installation. A later installation or pip failure does not restore deleted extras. Replacement of an existing desired repository still stages its new checkout before swapping it in. The operation is not a global transaction.

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

Optional for model downloads when credentials are not needed or are already supplied through the environment; required by Start because it sources the file. This is a generic environment-variable file, not a restricted token catalog. Example:

```dotenv
TOKEN_CA=
TOKEN_HF=
OTHER_VARIABLE=example
```

| Variable | Model download authentication |
| --- | --- |
| TOKEN_CA | civitai.com, www.civitai.com, civitai.red and www.civitai.red |
| TOKEN_HF | huggingface.co and www.huggingface.co |

For each supported host, model downloads prefer a nonempty process environment variable, then the corresponding value in token-db.env. If neither supplies a nonempty token, the request is unauthenticated. Tokens are sent as HTTP Authorization: Bearer headers and stripped on cross-origin redirects. Other hosts receive no token.

The model parser accepts arbitrary valid environment variable names, blank lines, full-line comments, KEY=VALUE assignments, optional export prefixes and matching outer quotes. Duplicate assignments use the last value. Values are read literally after trimming surrounding whitespace and removing matching outer quotes; shell expansion and command execution are not performed. Other variables are accepted but not used for model authentication. Start sources this file as shell code and exports its variables; keep it trusted and use simple shell-compatible assignments. Version files are also sourced through Bash. Keep actual secrets out of version control and catalog URLs. Record leaves tokens untouched.

## Command reference

Use `./comfy-sync.sh COMMAND [ARGUMENTS...]` (or `comfy-sync.bat COMMAND [ARGUMENTS...]` on Windows, subject to the platform limitations above). The dispatcher uses the managed interpreter, except that setup falls back to system Python if the venv is absent. Every synchronizer requires an explicit mode; there is no default mode. `--help` displays usage. Direct Python invocation is also available.

### Models and extensions

```sh
python python/sync-models.py {update|add|remove|verify|check|record} [options]
python python/sync-extensions.py {update|add|remove|verify|check|record} [options]
```

| Mode | Models | Extensions |
| --- | --- | --- |
| update | Download missing/wrong-size enabled files; delete extras. | Install missing/mismatched repos; remove extras. |
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
| --parallel-downloads N | Models only; default 8 concurrent model downloads. |
| --connections-per-download N | Models only; default 4 connections per model. |
| --help | CLI usage |

Explicit paths are interpreted relative to the invoking working directory if not absolute. Automatic paths are relative to the script location. Catalog must be outside the managed directory for filesystem operations. Root, home and current working directory are refused as managed roots. Use the venv interpreter for normal operation.

Examples:

```sh
python python/sync-models.py check
python python/sync-models.py update --dry-run
python python/sync-models.py verify
python python/sync-models.py record --database /tmp/model-db.xlsx
python python/sync-extensions.py add --python ../comfy-ui/.venv/bin/python
python python/sync-extensions.py verify --directory ../comfy-extensions
```

Exit status: 0 success (including dry-run plans), 1 operational/validation failure or real verify discrepancies, 2 argparse usage errors.

### Versions, configuration and tokens

```sh
./comfy-sync.sh sync-versions check
./comfy-sync.sh sync-versions verify
./comfy-sync.sh sync-versions record
./comfy-sync.sh sync-config update
./comfy-sync.sh sync-config record
./comfy-sync.sh sync-tokens check
```

| Synchronizer / mode | Behavior |
| --- | --- |
| versions check | Validate the version file and print the three pins, one per line. |
| versions verify | Compare the installed HEAD and runtime versions with the pins. |
| versions record | Write the installed release and runtime versions to the database. |
| config update | Copy the database settings into the default user profile. |
| config record | Copy the installed settings into the database. |
| tokens check | Validate literal assignments and report their count without printing secrets. |

All three accept `--database PATH` (the file itself, not a directory) and `--dry-run`. Versions and tokens dry-runs are no-ops; configuration dry-run reports the copy direction without writing. Token check requires a readable UTF-8 .env file and does not authenticate against any remote service. It uses the same literal parser as model downloads; Start uses Bash sourcing instead. Configuration is copied as-is, not validated as JSON. Versions has no update mode: Setup owns runtime installation.

## Lifecycle commands

### setup

Provisions the installation from the database. Checks driver compatibility, fetches/checks out ComfyUI release, moves initial models/custom_nodes/input/output directories out when applicable, creates managed directories and venv, checks existing Torch, torchvision and torchaudio before installing pinned CUDA Torch, then installs requirements plus additional runtime dependencies, writes Torch constraints and extension-path YAML inside comfy-ui, updates extensions, runs pip check and CUDA smoke test, updates models and copies settings. Stops on error. If all three Torch packages import successfully, Torch and CUDA versions match the database, and declared Torch dependencies of torchvision and torchaudio are satisfied, Setup skips both wheel-resolution dry-run and Torch installation. Otherwise it runs the original resolution and installation steps. The final CUDA smoke test and pip check still run. Setup can generally be rerun after completed stages, but recovery from an interrupted command is not guaranteed. Not a transactional installer; back up existing installations before running.

### update

Uses the existing venv and Torch constraints. Updates extensions and models, copies settings from database, then checks ComfyUI/Python/Torch versions. Attempts subsequent stages even if an earlier stage fails; returns nonzero if any stage fails. Does not automatically upgrade core versions. A version mismatch is reported after reconciliation, not as a preflight guard.

### record

Reverse inventory operation: checks local release tag and actual venv versions, stages existing model/extension catalogs, invokes both record modes, copies installed settings and writes version-db.env. Publishes all four staged files only after successful preparation. Each file replacement is atomic, but the four replacements are not one transaction. Token file is unchanged. No dry-run option on the lifecycle command; use individual Python record dry-runs to preview. Stop ComfyUI or avoid concurrent modifications during capture.

### start

Launches ComfyUI with the venv interpreter, external model/input/output directories, generated extension-path YAML, manager enabled, automatic previews and DETAIL verbosity. Sources and exports token-db.env. All arguments after start are forwarded to ComfyUI. The dispatcher supplies --preview-method auto, --listen 0.0.0.0 and --port 8188 unless the corresponding option is explicitly supplied (including --option=value syntax). By default it binds to 0.0.0.0; use firewall/access controls rather than exposing the service directly to the internet.

```sh
./comfy-sync.sh start --port 8190
./comfy-sync.sh start --listen 127.0.0.1 --preview-method none
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

## Tests and verification status

```sh
python -m pip install -r requirements.txt
python -m pip install httpx
python python/tests.py
bash -n comfy-sync.sh
```

Validate real catalogs without modifying them:

```sh
python python/sync-models.py check --database ../comfy-db-main/model-db.xlsx
python python/sync-extensions.py check --database ../comfy-db-main/extension-db.xlsx
```

The unit suite covers catalog validation, discovery, dry-run, record, download handling, extension reconciliation and requirements interpreter selection. A real GPU deployment and shell integration tests are separate from the Python unit suite. The latest dispatcher and extracted synchronizers have not yet been integration-tested; the Windows dispatcher is untested on Windows.

## License

Comfy Sync is released under the [MIT License](LICENSE).
