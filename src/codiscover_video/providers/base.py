"""Stable source boundary: providers only read platform content."""

from typing import Protocol

from codiscover_video.models import Video


class ProviderError(RuntimeError):
    """A recoverable remote-source failure with a safe user-facing message."""


class LoginRequired(ProviderError):
    """Explicit pause point: the user must log in locally."""


class VideoProvider(Protocol):
    async def search(self, query: str, limit: int = 5) -> list[Video]: ...
    async def inspect(self, video_id: str) -> Video: ...
    async def health(self) -> dict: ...
