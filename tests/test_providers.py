import asyncio
import json
import stat
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from codiscover_video.config import Settings
from codiscover_video.models import Segment
from codiscover_video.providers.base import LoginRequired, ProviderError
from codiscover_video.providers.bilibili import BilibiliProvider, normalize_video_id
from codiscover_video.providers.demo import DemoProvider

BV = "BV1d8pU6xEZX"


def test_video_id_validation():
    assert normalize_video_id(BV) == BV
    assert normalize_video_id(f"https://www.bilibili.com/video/{BV}/?t=20") == BV
    for value in (
        "file:///etc/passwd",
        f"https://bilibili.com.evil.test/video/{BV}",
        f"https://evil.test/{BV}",
        "BVbad",
        f"{BV}; echo bad",
    ):
        with pytest.raises(ProviderError):
            normalize_video_id(value)


async def test_demo_fixtures_are_fictional_and_have_evidence():
    provider = DemoProvider()
    candidates = await provider.search("安静 自然 放松", 12)
    assert len(candidates) == 12
    assert len({v.id for v in candidates}) == 12
    assert all(not v.url and v.source == "demo" and v.segments for v in candidates)
    assert "安静" in candidates[0].tags
    assert (await provider.inspect("demo-cook")).author == "厨房灯还亮着"


async def test_subtitle_timestamps_survive_adapter(tmp_path):
    settings = Settings(credential_path=tmp_path / "credential.json")
    settings.credential_path.write_text("{}")
    provider = BilibiliProvider(settings)

    async def call(name, **kwargs):
        if name == "bili_video_info":
            return {"bvid": BV, "title": "测试视频", "duration": 300}
        assert name == "bili_subtitle"
        return {"text": "原始证据", "segments": [{"start": 12.5, "end": 17.0, "text": "原始证据"}]}

    provider._call = call
    result = await provider.inspect(BV)
    assert result.content_status == "subtitle"
    assert result.segments[0].start == 12.5
    assert result.segments[0].end == 17.0
    assert result.segments[0].text == "原始证据"
    assert result.segments[0].source == "subtitle"
    assert not result.image_urls


@pytest.mark.parametrize(
    ("start", "end"),
    [(float("nan"), 1), (0, float("nan")), (float("inf"), float("inf")), (0, float("inf")), (2, 1)],
)
def test_segments_reject_nonfinite_or_reversed_timestamps(start, end):
    with pytest.raises(ValidationError):
        Segment(start=start, end=end, text="不能作为时间证据")


@pytest.mark.parametrize(
    "raw_segments",
    [
        None,
        [],
        [{"text": "只有文字，没有时间"}],
        [{"start": 3, "end": 2, "text": "时间颠倒"}],
        [{"start": 0, "end": float("inf"), "text": "时间无效"}],
        [{"start": 0, "end": 1, "text": "   "}],
    ],
)
async def test_incomplete_subtitle_evidence_downgrades_to_metadata(tmp_path, raw_segments):
    settings = Settings(credential_path=tmp_path / "credential.json")
    settings.credential_path.write_text("{}")
    provider = BilibiliProvider(settings)
    calls = []

    async def call(name, **kwargs):
        calls.append(name)
        if name == "bili_video_info":
            return {"bvid": BV, "title": "测试视频"}
        if name == "bili_subtitle":
            return {"text": "不能伪装为有时间戳的字幕证据", "segments": raw_segments}
        assert name == "bili_frames"
        return []

    provider._call = call
    result = await provider.inspect(BV)
    assert calls == ["bili_video_info", "bili_subtitle", "bili_frames"]
    assert result.content_status == "metadata_only"
    assert result.segments == []


async def test_missing_subtitle_timestamps_can_fall_back_to_actual_frames(tmp_path):
    settings = Settings(credential_path=tmp_path / "credential.json")
    settings.credential_path.write_text("{}")
    provider = BilibiliProvider(settings)

    async def call(name, **kwargs):
        if name == "bili_video_info":
            return {"bvid": BV, "title": "测试视频"}
        if name == "bili_subtitle":
            return {"text": "没有时间戳的旧格式文本"}
        return [
            SimpleNamespace(type="text", text=json.dumps({"frames": [{"got_sec": 21.0}]})),
            SimpleNamespace(type="image", mimeType="image/jpeg", data="YWJj"),
        ]

    provider._call = call
    result = await provider.inspect(BV)
    assert result.content_status == "frames"
    assert len(result.segments) == 1
    assert result.segments[0].source == "storyboard"
    assert result.segments[0].start == result.segments[0].end == 21.0


async def test_storyboard_fallback_retains_actual_timestamp(tmp_path):
    provider = BilibiliProvider(Settings(credential_path=tmp_path / "missing.json"))

    async def call(name, **kwargs):
        if name == "bili_video_info":
            return {"bvid": BV, "title": "测试视频"}
        assert name == "bili_frames"
        return [
            SimpleNamespace(type="text", text=json.dumps({"frames": [{"got_sec": 14.5}]})),
            SimpleNamespace(type="image", mimeType="image/jpeg", data="YWJj"),
        ]

    provider._call = call
    result = await provider.inspect(BV)
    assert result.content_status == "frames"
    assert result.segments[0].start == 14.5
    assert result.segments[0].source == "storyboard"
    assert result.image_urls == ["data:image/jpeg;base64,YWJj"]


async def test_missing_evidence_and_login_requests_pause(tmp_path):
    provider = BilibiliProvider(Settings(credential_path=tmp_path / "missing.json"))

    async def call(name, **kwargs):
        if name == "bili_video_info":
            return {"bvid": BV, "title": "测试视频"}
        return []

    provider._call = call
    with pytest.raises(LoginRequired):
        await provider.inspect(BV)


def test_credentials_are_atomic_and_owner_only(tmp_path):
    provider = BilibiliProvider(Settings(credential_path=tmp_path / "credential.json"))
    provider._save_credential({"sessdata": "test-session", "bili_jct": "test-csrf"})
    assert json.loads(provider.credential_path.read_text())["sessdata"] == "test-session"
    assert stat.S_IMODE(provider.credential_path.stat().st_mode) == 0o600
    assert len(list(tmp_path.iterdir())) == 1


async def test_provider_timeout_is_bounded_and_safe(tmp_path):
    from contextvars import ContextVar

    provider = BilibiliProvider(Settings(credential_path=tmp_path / "credential.json"))
    provider.timeout = 0.01

    async def slow(**kwargs):
        await asyncio.sleep(1)

    provider._upstream = SimpleNamespace(CRED_CONTEXT=ContextVar("test_cred"), bili_search=slow)
    with pytest.raises(ProviderError, match="超时"):
        await provider.search("自然")


def test_pinned_storyboard_helpers_enforce_cdn_and_units():
    pytest.importorskip("mcp")
    from codiscover_video.vendor.bili_readonly import _https, _snapshot_entries

    assert _https("//i0.hdslb.com/a.jpg") == "https://i0.hdslb.com/a.jpg"
    with pytest.raises(ValueError):
        _https("https://127.0.0.1/private")
    assert _snapshot_entries({"index": [1000, 2000]}, 10) == [(1.0, 0), (2.0, 1)]
    assert _snapshot_entries({"index": [1000, 2000]}, 3600) == [(1000.0, 0), (2000.0, 1)]


async def test_mcp_facade_exposes_only_readonly_tools():
    pytest.importorskip("mcp")
    import os
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "codiscover_video.providers.mcp_server"],
        env={**os.environ, "CDV_PROVIDER": "demo"},
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
        await client.initialize()
        listing = await client.list_tools()
        assert {tool.name for tool in listing.tools} == {"video_search", "video_inspect", "source_health"}
        result = await client.call_tool("video_inspect", {"video_id": "demo-rain"})
        assert not result.isError
        assert "demo-rain" in result.content[0].text


async def test_upstream_resource_reader_consumes_chunks_and_limits_size():
    pytest.importorskip("mcp")
    from codiscover_video.vendor.bili_readonly import _bounded_read

    class Stream:
        async def iter_chunked(self, size):
            for chunk in (b"first", b"second", b"third"):
                yield chunk

    assert await _bounded_read(Stream()) == b"firstsecondthird"
    with pytest.raises(RuntimeError, match="byte limit"):
        await _bounded_read(Stream(), limit=6)
