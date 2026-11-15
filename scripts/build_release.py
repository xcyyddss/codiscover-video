"""Build a deterministic public source ZIP from an explicit allowlist.

This is a release gate for known accidental disclosures, not a guarantee that every
secret or personal detail can be detected. Review the retained examples and images.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import tempfile
import tomllib
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = "codiscover-video"
TOP_FILES = frozenset(
    {
        ".env.example",
        ".gitignore",
        ".gitattributes",
        "AGENTS.md",
        "LICENSE",
        "NOTICE.md",
        "README.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CHANGELOG.md",
        "Makefile",
        "pyproject.toml",
        "uv.lock",
        "package.json",
        "package-lock.json",
        "playwright.config.cjs",
    }
)
TOP_DIRECTORIES = frozenset({"src", "tests", "scripts", "docs", "examples", ".github", "integrations"})
SKIP_DIRECTORIES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".playwright",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".cache",
        "dist",
        "build",
        "htmlcov",
        "coverage",
        "data",
    }
)
SKIP_SUFFIXES = frozenset(
    {
        ".pyc",
        ".pyo",
        ".log",
        ".db",
        ".sqlite",
        ".sqlite3",
        ".zip",
        ".gz",
        ".tar",
        ".tgz",
        ".whl",
        ".sha256",
        ".egg",
        ".DS_Store",
        ".bak",
        ".tmp",
    }
)
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico"})
# Split the former brand spelling so this scanner does not flag its own definition.
OLD_BRAND = "stream" + "toagent"
OLD_PREFIX = "S" + "TA_"
TEXT_RULES = (
    ("API-key-shaped value", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("private-key material", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("personal home path", re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+(?:/|\b)")),
    ("legacy project name", re.compile(re.escape(OLD_BRAND), re.IGNORECASE)),
    ("legacy configuration prefix", re.compile(r"\b" + re.escape(OLD_PREFIX), re.IGNORECASE)),
)


class ReleaseError(ValueError):
    """A safe diagnostic that identifies a file and rule, never a matched secret."""


def excluded(path: Path) -> bool:
    name = path.name.lower()
    if path.name in SKIP_DIRECTORIES or name.endswith(".egg-info"):
        return True
    if name == ".ds_store" or name == ".env" or name.startswith(".env."):
        return True
    if path.suffix.lower() in {suffix.lower() for suffix in SKIP_SUFFIXES}:
        return True
    if re.search(r"\.(?:db|sqlite|sqlite3)-(?:wal|shm|journal)$", name):
        return True
    if name == "api调用指南.md":
        return True
    return bool(
        re.search(r"(?:credential|cookie|session|token).*\.(?:json|txt|bin)$", name)
        and name not in {"package-lock.json"}
    )


def candidate_files(root: Path, omitted: frozenset[Path] = frozenset()) -> list[Path]:
    files: list[Path] = []

    def include(path: Path, *, top_file: bool = False):
        # Resolve no paths here: doing so would conceal a candidate symlink.
        if path.absolute() in omitted or (not top_file and excluded(path)):
            return
        if path.is_symlink():
            raise ReleaseError(f"Candidate symlink rejected: {path.relative_to(root)}")
        if path.is_dir():
            for child in sorted(path.iterdir()):
                include(child)
        elif path.is_file():
            files.append(path)
        elif path.exists():
            raise ReleaseError(f"Non-regular candidate rejected: {path.relative_to(root)}")

    for name in sorted(TOP_FILES | TOP_DIRECTORIES):
        include(root / name, top_file=name in TOP_FILES)
    data = root / "data"
    if data.is_symlink():
        raise ReleaseError("Candidate symlink rejected: data")
    keep = data / ".gitkeep"
    if keep.is_symlink():
        raise ReleaseError("Candidate symlink rejected: data/.gitkeep")
    if keep.is_file():
        files.append(keep)
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def inspect_payload(relative: str, payload: bytes) -> None:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        if Path(relative).suffix.lower() not in IMAGE_SUFFIXES:
            raise ReleaseError(f"Unexpected binary candidate: {relative}") from None
        # Also inspect readable metadata in retained images; this does not replace visual review.
        text = payload.decode("utf-8", errors="ignore")
    for label, pattern in TEXT_RULES:
        if pattern.search(text):
            raise ReleaseError(f"Release gate failed: {label} in {relative}")


def project_version(root: Path) -> str:
    with (root / "pyproject.toml").open("rb") as handle:
        metadata = tomllib.load(handle)["project"]
    if metadata.get("name") != ARCHIVE_ROOT:
        raise ReleaseError("pyproject.toml must name the distribution codiscover-video")
    version = metadata.get("version", "")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[A-Za-z0-9.-]*)", version):
        raise ReleaseError("pyproject.toml has an unsupported version")
    return version


def zip_entry(name: str) -> zipfile.ZipInfo:
    entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    entry.create_system = 3
    entry.external_attr = (stat.S_IFREG | 0o644) << 16
    entry.compress_type = zipfile.ZIP_DEFLATED
    return entry


def build_release(root: Path, output: Path | None = None) -> dict:
    root = root.resolve()
    version = project_version(root)
    output = output or root / "dist" / f"codiscover-video-{version}-public.zip"
    output = Path(os.path.abspath(output))
    checksum = output.with_suffix(output.suffix + ".sha256")
    if output.suffix.lower() != ".zip":
        raise ReleaseError("Output must have a .zip suffix")
    if output.is_symlink() or checksum.is_symlink():
        raise ReleaseError("Output and checksum paths must not be symlinks")
    candidates = candidate_files(root, frozenset({output, checksum}))
    relative_names = {p.relative_to(root).as_posix() for p in candidates}
    if not {"README.md", "LICENSE", "pyproject.toml"} <= relative_names:
        raise ReleaseError("Release requires README.md, LICENSE and pyproject.toml")
    if not any(name.startswith("src/") for name in relative_names):
        raise ReleaseError("Release requires source files")
    payloads = []
    for path in candidates:
        relative = path.relative_to(root).as_posix()
        payload = path.read_bytes()
        inspect_payload(relative, payload)
        payloads.append((relative, payload))
    manifest = {
        "schema_version": 1,
        "project": "CoDiscover Video",
        "version": version,
        "archive_root": ARCHIVE_ROOT,
        "files": [
            {"path": relative, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
            for relative, payload in payloads
        ],
    }
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=".codiscover-release-", suffix=".tmp", dir=output.parent)
        os.close(fd)
        temporary = Path(name)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for relative, payload in payloads:
                archive.writestr(zip_entry(f"{ARCHIVE_ROOT}/{relative}"), payload, compresslevel=9)
            archive.writestr(
                zip_entry(f"{ARCHIVE_ROOT}/RELEASE-MANIFEST.json"), manifest_bytes, compresslevel=9
            )
        digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
        os.replace(temporary, output)
        temporary = None
        checksum.write_text(f"{digest}  {output.name}\n", encoding="utf-8")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {"archive": str(output), "checksum": str(checksum), "sha256": digest, "files": len(payloads)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="ZIP destination; default: project dist directory")
    args = parser.parse_args()
    try:
        result = build_release(PROJECT_ROOT, args.output)
    except (ReleaseError, OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        parser.exit(1, f"Release build failed: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
