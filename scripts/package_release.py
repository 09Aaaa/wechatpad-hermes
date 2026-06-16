from __future__ import annotations

import argparse
import tarfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PREFIX = "hermes-wechatpadpromax"

EXCLUDED_DIR_NAMES = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
}
EXCLUDED_FILE_SUFFIXES = (
    ".db",
    ".db-shm",
    ".db-wal",
    ".pyc",
    ".pyo",
    ".sqlite",
    ".sqlite-shm",
    ".sqlite-wal",
    ".sqlite3",
    ".sqlite3-shm",
    ".sqlite3-wal",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".zip",
)


def should_include(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    parts = rel.parts
    if any(part in EXCLUDED_DIR_NAMES or part.endswith(".egg-info") for part in parts[:-1]):
        return False
    name = path.name
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return False
    if name.endswith(EXCLUDED_FILE_SUFFIXES):
        return False
    if path.is_dir():
        return False
    return True


def iter_release_files() -> list[Path]:
    return sorted(path for path in ROOT.rglob("*") if should_include(path))


def build_archive(output: Path, prefix: str) -> int:
    files = iter_release_files()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz") as archive:
        for path in files:
            archive.add(path, arcname=str(Path(prefix) / path.relative_to(ROOT)))
    return len(files)


def default_output() -> Path:
    stamp = time.strftime("%Y%m%d%H%M%S")
    return ROOT.parent / f"{DEFAULT_PREFIX}-{stamp}.tar.gz"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a clean WeChatPad-Hermes release archive")
    parser.add_argument("--output", type=Path, default=default_output(), help="Target .tar.gz path")
    parser.add_argument("--prefix", default=DEFAULT_PREFIX, help="Top-level archive directory name")
    parser.add_argument("--dry-run", action="store_true", help="List aggregate packaging result without writing an archive")
    args = parser.parse_args()

    files = iter_release_files()
    if args.dry_run:
        print(f"release files: {len(files)}")
        print(f"output: {args.output}")
        return

    count = build_archive(args.output, args.prefix)
    print(f"archive: {args.output}")
    print(f"files: {count}")


if __name__ == "__main__":
    main()
