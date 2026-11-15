"""Fictional, deterministic fixtures for credential-free end-to-end demos."""

import json
from importlib.resources import files

from codiscover_video.models import Video
from codiscover_video.providers.base import ProviderError


def _videos() -> list[Video]:
    """Load the same published fixture resource from a checkout or installed wheel.

    Return fresh models so callers cannot alter the shared corpus between runs.
    """
    resource = files("codiscover_video").joinpath("fixtures/demo_videos.json")
    corpus = json.loads(resource.read_text(encoding="utf-8"))
    if corpus.get("schema_version") != 1 or corpus.get("synthetic") is not True:
        raise ProviderError("演示语料缺少预期的合成数据标识。")
    videos = [Video.model_validate(item) for item in corpus["videos"]]
    if not videos or any(
        video.source != "demo"
        or not video.id.startswith("demo-")
        or video.url
        or video.image_urls
        or any(segment.source != "demo_subtitle" for segment in video.segments)
        for video in videos
    ):
        raise ProviderError("演示语料不能包含平台视频、媒体地址或未标注的字幕。")
    return videos


class DemoProvider:
    async def search(self, query: str, limit: int = 5) -> list[Video]:
        query = query.strip().lower()

        def score(v: Video) -> int:
            hay = v.title + v.description + " ".join(v.tags)
            return sum(3 for tag in v.tags if tag in query) + sum(1 for c in set(query) if c in hay)

        return sorted(_videos(), key=score, reverse=True)[: max(1, min(limit, 20))]

    async def inspect(self, video_id: str) -> Video:
        for video in _videos():
            if video.id == video_id:
                return video
        raise ProviderError("演示库中不存在这个视频。演示模式仅接受 demo- 开头的样本 ID。")

    async def health(self) -> dict:
        return {
            "available": True,
            "logged_in": False,
            "source": "demo",
            "message": "演示模式：12 条虚构素材，不访问 B 站。",
        }
