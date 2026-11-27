#!/usr/bin/env python3
"""Validate the token database without executing it or exposing credentials."""

import argparse
import sys
from pathlib import Path


def discover_database():
    """Locate the token file in the unique sibling database directory."""
    workspace = Path(__file__).resolve().parents[2]
    directories = [p for p in workspace.glob("comfy-db*") if p.is_dir()]
    if len(directories) != 1:
        raise ValueError(f"expected exactly one comfy-db* directory; found {len(directories)}")
    return directories[0] / "token-db.env"


def read_tokens(path):
    """Parse literal environment assignments without executing shell code."""
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
            # Later assignments override earlier ones; values are never shell-expanded.
            tokens[key] = value
    return tokens


def main(argv=None):
    """Check literal assignments without contacting remote services."""
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("command", nargs="?", choices=("check",), default="check")
    cli.add_argument("--database", type=Path)
    cli.add_argument("--dry-run", action="store_true")
    args = cli.parse_args(argv)
    if args.dry_run:
        return 0
    try:
        path = args.database if args.database is not None else discover_database()
        if path.suffix.lower() != ".env":
            raise ValueError("token database must have the .env extension")
        tokens = read_tokens(path)
        print(f"Token database valid: {len(tokens)} distinct assignments")
        return 0
    except UnicodeError:
        print("Error: token database must be UTF-8", file=sys.stderr)
    except OSError:
        print("Error: cannot read token database", file=sys.stderr)
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
