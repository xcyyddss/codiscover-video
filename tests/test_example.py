"""Release examples must stay synthetic, deterministic, isolated and offline."""

import importlib.util
import json
import socket
from importlib.resources import files
from pathlib import Path

import pytest

from codiscover_video.config import Settings
from codiscover_video.llm import DeepSeekClient
from codiscover_video.providers.bilibili import BilibiliProvider
from codiscover_video.providers.demo import DemoProvider, _videos

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("public_example", ROOT / "scripts/run_example.py")
example = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(example)


def test_fixture_provenance_and_media_boundary():
    data = json.loads(files("codiscover_video").joinpath("fixtures/demo_videos.json").read_text())
    assert data["synthetic"] is True
    assert data["schema_version"] == 1 and data["license"] == "MIT"
    assert data["provenance"]["contains_platform_media"] is False
    assert data["provenance"]["contains_real_people_or_accounts"] is False
    assert data["provenance"]["contains_user_feedback"] is False
    videos = _videos()
    assert len(videos) == len({video.id for video in videos}) == 12
    assert all(video.id.startswith("demo-") and video.source == "demo" for video in videos)
    assert all(not video.url and not video.image_urls for video in videos)
    assert all(segment.source == "demo_subtitle" for video in videos for segment in video.segments)


async def test_fixture_load_returns_independent_models():
    provider = DemoProvider()
    first = await provider.inspect("demo-rain")
    first.title = "mutation in caller"
    first.segments[0].text = "not original"
    second = await provider.inspect("demo-rain")
    assert second.title != first.title
    assert second.segments[0].text != first.segments[0].text


async def test_scenario_offline_and_stable_even_from_live_configured_directory(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError(
            "Public examples must not load credentials, use live providers or access network"
        )

    async def no_request(*args, **kwargs):
        forbidden()

    monkeypatch.setattr(Settings, "from_env", forbidden)
    monkeypatch.setattr("codiscover_video.config.load_dotenv", forbidden)
    monkeypatch.setattr(BilibiliProvider, "__init__", forbidden)
    monkeypatch.setattr(DeepSeekClient, "_request", no_request)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setenv("CDV_PROVIDER", "bilibili")
    monkeypatch.setenv("CDV_LLM", "deepseek")
    monkeypatch.setenv("CDV_DATA_DIR", str(tmp_path / "private-memory"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-be-used")
    dotenv = tmp_path / ".env"
    dotenv.write_text("CDV_PROVIDER=bilibili\nCDV_LLM=deepseek\n")
    monkeypatch.chdir(tmp_path)
    report = await example.run_scenario()
    second = await example.run_scenario()
    expected = json.loads((ROOT / "examples/after_work/expected.json").read_text())
    assert report == second == expected
    assert report["synthetic"] and all(report["checks"].values())
    assert report["exploration"]["model_http_calls"] == 0
    assert {item["video_id"] for item in report["learning_recommendations"]} == {"demo-science"}
    assert "demo-science" not in {item["video_id"] for item in report["recommendations_after_feedback"]}
    assert report["free_feedback"]["video_id"] is None
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env"]
    content = json.dumps(report, ensure_ascii=False)
    assert "must-not-be-used" not in content and str(tmp_path) not in content
    assert "created_at" not in content and "session_id" not in content and "goal_id" not in content


async def test_scenario_requires_synthetic_marker(tmp_path):
    path = tmp_path / "scenario.json"
    path.write_text('{"schema_version":1,"synthetic":false}')
    with pytest.raises(ValueError, match="synthetic"):
        await example.run_scenario(path)
