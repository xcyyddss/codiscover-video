"""Release checks exercise exclusions and failure boundaries with synthetic files."""

import hashlib
import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_release.py"
spec = importlib.util.spec_from_file_location("release_builder", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "README.md").write_text("# CoDiscover Video\n")
    (root / "LICENSE").write_text("MIT\n")
    (root / "pyproject.toml").write_text('[project]\nname = "codiscover-video"\nversion = "0.1.0"\n')
    (root / "src" / "codiscover_video").mkdir(parents=True)
    (root / "src" / "codiscover_video" / "__init__.py").write_text('"""Synthetic fixture."""\n')
    return root


def write(root, relative, text="private fixture"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def archive_names(path):
    with zipfile.ZipFile(path) as archive:
        return set(archive.namelist())


def test_whitelist_excludes_runtime_data_and_keeps_template(project):
    forbidden = [
        ".env",
        ".env.local",
        "data/bili_credential.json",
        "data/memory.sqlite3",
        "data/transcript.json",
        "private-notes.md",
        "API调用指南.md",
        ".git/config",
        "src/codiscover_video/__pycache__/module.pyc",
        "src/codiscover_video/debug.log",
        "src/codiscover_video/local.sqlite3-wal",
        "docs/cookies.json",
        "docs/credential.json",
        "docs/.env",
        "docs/session.bin",
        "scripts/node_modules/something/index.js",
        "integrations/dsh/runtime/node_modules/private/index.js",
        "tests/.venv/state.txt",
        "dist/old.zip",
        "docs/backups.tar.gz",
    ]
    for name in forbidden:
        write(project, name)
    write(project, ".env.example", "DEEPSEEK_API_KEY=\n")
    write(project, "data/.gitkeep", "")
    write(project, "examples/sample.json", '{"synthetic":true}')
    result = module.build_release(project)
    names = archive_names(result["archive"])
    assert all("codiscover-video/" + name not in names for name in forbidden)
    assert "codiscover-video/.env.example" in names
    assert "codiscover-video/data/.gitkeep" in names
    assert "codiscover-video/examples/sample.json" in names


@pytest.mark.parametrize("location", ["README.md", "docs/note.md", "src/link", "data/.gitkeep", "data"])
def test_candidate_symlinks_are_rejected(project, tmp_path, location):
    target = tmp_path / "outside.txt"
    target.write_text("not release data")
    path = project / location
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    path.symlink_to(target)
    with pytest.raises(module.ReleaseError, match="symlink"):
        module.build_release(project)


@pytest.mark.parametrize(
    "payload",
    [
        "sk-" + "x" * 24,
        "-----BEGIN " + "PRIVATE KEY-----",
        "/" + "Users" + "/example-person/private.txt",
        "/" + "home" + "/example-person/private.txt",
        "Stream" + "ToAgent",
        "S" + "TA_PROVIDER=demo",
        "s" + "ta_feedback",
    ],
)
def test_known_disclosure_patterns_fail_without_echoing_payload(project, payload):
    write(project, "docs/accidental.txt", payload)
    with pytest.raises(module.ReleaseError) as exc:
        module.build_release(project)
    assert "docs/accidental.txt" in str(exc.value)
    assert payload not in str(exc.value)
    assert not (project / "dist").exists()


def test_short_fake_key_is_allowed(project):
    write(project, "tests/fixture.txt", "sk-short-fake")
    assert Path(module.build_release(project)["archive"]).is_file()


def test_repeat_build_is_deterministic_and_manifest_hashes_match(project):
    first = module.build_release(project)
    archive = Path(first["archive"])
    first_bytes = archive.read_bytes()
    # A generated artifact nested in an allowed directory must not reinclude itself.
    nested = project / "docs" / "release.zip"
    second = module.build_release(project, nested)
    third = module.build_release(project, nested)
    assert first_bytes == nested.read_bytes()
    alternate = project / "docs" / "another.zip"
    module.build_release(project, alternate)
    assert alternate.read_bytes() == first_bytes
    assert first["sha256"] == second["sha256"] == third["sha256"]
    assert hashlib.sha256(first_bytes).hexdigest() == first["sha256"]
    assert Path(second["checksum"]).read_text().startswith(first["sha256"])
    with zipfile.ZipFile(archive) as handle:
        manifest = json.loads(handle.read("codiscover-video/RELEASE-MANIFEST.json"))
        assert not any(entry["path"] == "RELEASE-MANIFEST.json" for entry in manifest["files"])
        for item in manifest["files"]:
            payload = handle.read("codiscover-video/" + item["path"])
            assert hashlib.sha256(payload).hexdigest() == item["sha256"]
            assert len(payload) == item["bytes"]
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in handle.infolist())


def test_cli_uses_script_project_root_from_another_cwd(project, tmp_path):
    script = project / "scripts" / "build_release.py"
    script.parent.mkdir()
    script.write_bytes(SCRIPT.read_bytes())
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    write(unrelated, "README.md", "must not enter archive")
    completed = subprocess.run([sys.executable, str(script)], cwd=unrelated, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    with zipfile.ZipFile(result["archive"]) as archive:
        assert archive.read("codiscover-video/README.md") == b"# CoDiscover Video\n"


def test_unexpected_binary_and_non_zip_destination_fail(project):
    path = project / "src" / "payload.bin"
    path.write_bytes(b"\xff\x00\xfe")
    with pytest.raises(module.ReleaseError, match="binary"):
        module.build_release(project)
    path.unlink()
    with pytest.raises(module.ReleaseError, match=".zip"):
        module.build_release(project, project / "README.md")
