"""Bilibili read-only adapter, with upstream storyboard/subtitle extraction."""

from __future__ import annotations

import asyncio
import base64
import importlib
import io
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from codiscover_video.models import Segment, Video
from codiscover_video.providers.base import LoginRequired, ProviderError

_BVID = re.compile(r"BV[0-9A-Za-z]{10}")
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"


def normalize_video_id(value: str) -> str:
    value = value.strip()
    if _BVID.fullmatch(value):
        return value
    parsed = urlsplit(value)
    if parsed.scheme in ("https", "http") and parsed.hostname in (
        "bilibili.com",
        "www.bilibili.com",
        "m.bilibili.com",
    ):
        match = re.fullmatch(r"/video/(BV[0-9A-Za-z]{10})/?", parsed.path)
        if match:
            return match.group(1)
    raise ProviderError("请输入有效的 BV 号或 bilibili.com/video/BV… 链接。")


def _duration(value) -> int:
    if isinstance(value, (int, float)):
        return max(0, int(value))
    try:
        total = 0
        for part in str(value).split(":"):
            total = total * 60 + int(part)
        return total
    except ValueError:
        return 0


def _video(data: dict) -> Video:
    vid = normalize_video_id(str(data.get("bvid", "")))
    return Video(
        id=vid,
        title=str(data.get("title") or vid),
        author=str(data.get("author") or ""),
        url=f"https://www.bilibili.com/video/{vid}/",
        duration=_duration(data.get("duration", 0)),
        description=str(data.get("description") or ""),
        tags=data.get("tags") or [],
        source="bilibili",
        content_status="metadata_only",
    )


class BilibiliProvider:
    def __init__(self, settings):
        self.settings = settings
        self.credential_path = Path(settings.credential_path)
        self.timeout = float(settings.request_timeout)
        self._qr_key: str | None = None
        self._upstream = None
        self._lock = asyncio.Lock()

    def _module(self):
        if self._upstream is None:
            try:
                self._upstream = importlib.import_module("codiscover_video.vendor.bili_readonly")
            except ImportError as exc:
                raise ProviderError("缺少 B 站依赖，请运行 uv sync --extra bilibili。") from exc
        return self._upstream

    async def _call(self, name: str, **kwargs):
        # The pinned upstream reads a module-level credential path. Serialize calls so
        # per-provider credential selection and underlying network state remain isolated.
        module = self._module()
        async with self._lock:
            token = module.CRED_CONTEXT.set(self.credential_path)
            try:
                result = await asyncio.wait_for(getattr(module, name)(**kwargs), timeout=self.timeout)
                return json.loads(result) if isinstance(result, str) else result
            except TimeoutError as exc:
                raise ProviderError("B 站请求超时，请稍后重试。") from exc
            except ProviderError:
                raise
            except Exception as exc:
                text = str(exc)
                if "未登录" in text or "SESSDATA" in text or "-101" in text:
                    raise LoginRequired("读取字幕需要 B 站登录，请在工作台扫码登录。") from exc
                if any(code in text for code in ("-412", "-352", "403", "429")):
                    raise ProviderError("B 站暂时限制了访问，请暂停探索并稍后重试。") from exc
                raise ProviderError(
                    f"B 站读取失败（{type(exc).__name__}），请检查网络或视频是否公开。"
                ) from exc
            finally:
                module.CRED_CONTEXT.reset(token)

    async def search(self, query: str, limit: int = 5) -> list[Video]:
        query = query.strip()
        if not query or len(query) > 200:
            raise ProviderError("搜索词长度需要在 1 到 200 个字符之间。")
        data = await self._call("bili_search", keyword=query, num=max(1, min(limit, 20)), order="totalrank")
        videos = []
        for item in data.get("videos", []):
            try:
                videos.append(_video(item))
            except ProviderError:
                continue
        return videos

    async def inspect(self, video_id: str) -> Video:
        vid = normalize_video_id(video_id)
        result = _video(await self._call("bili_video_info", bvid=vid))
        login_missing = False
        if self.credential_path.exists():
            try:
                subtitle = await self._call("bili_subtitle", bvid=vid)
                text, raw_segments = subtitle.get("text"), subtitle.get("segments")
                # Only accept actual timestamped segments from the pinned extractor.
                # Untimed text must not become a fabricated 0:00 citation. If a
                # compatibility/partial response omits timestamps, try frames below.
                if (
                    isinstance(text, str)
                    and text.strip()
                    and isinstance(raw_segments, list)
                    and raw_segments
                    and all(
                        isinstance(item, dict) and {"start", "end", "text"} <= item.keys()
                        for item in raw_segments
                    )
                ):
                    try:
                        segments = [
                            Segment.model_validate({**item, "source": "subtitle"}) for item in raw_segments
                        ]
                    except ValidationError:
                        segments = []
                    if segments and all(segment.text.strip() for segment in segments):
                        result.segments = segments
                        result.content_status = "subtitle"
                        return result
            except LoginRequired:
                login_missing = True
        else:
            login_missing = True
        # Storyboard is public and provides evidence when subtitles are unavailable.
        try:
            blocks = await self._call("bili_frames", bvid=vid, num=3)
            frame_info = {}
            for block in blocks:
                if getattr(block, "type", "") == "text":
                    frame_info = json.loads(block.text)
                elif (
                    getattr(block, "type", "") == "image"
                    and block.mimeType == "image/jpeg"
                    and len(block.data) <= 2_000_000
                ):
                    result.image_urls.append(f"data:image/jpeg;base64,{block.data}")
            if result.image_urls:
                result.content_status = "frames"
                result.segments = [
                    Segment(
                        start=f.get("got_sec", 0),
                        end=f.get("got_sec", 0),
                        source="storyboard",
                        text=f"第 {i + 1} 个故事板采样帧；仅支持画面观察，不代表整段音频或完整视频。",
                    )
                    for i, f in enumerate(frame_info.get("frames", [])[: len(result.image_urls)])
                ]
                return result
        except ProviderError:
            pass
        if login_missing:
            raise LoginRequired("当前视频没有可用故事板；读取字幕需要你先扫码登录 B 站。")
        # Metadata must not be presented as having watched the content.
        return result

    async def health(self) -> dict:
        try:
            self._module()
        except ProviderError as exc:
            return {"available": False, "logged_in": False, "source": "bilibili", "message": str(exc)}
        present = False
        try:
            data = json.loads(self.credential_path.read_text())
            present = bool(data.get("sessdata"))
        except (OSError, ValueError, AttributeError):
            pass
        return {
            "available": True,
            "logged_in": present,
            "credential_verified": False,
            "source": "bilibili",
            "message": "已保存本地凭证；有效性会在读取字幕时确认。"
            if present
            else "可匿名搜索和读取故事板；字幕通常需要扫码登录。",
        }

    async def login_start(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=self.timeout, headers={"User-Agent": _UA}) as client:
                response = await client.get(
                    "https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
                    params={"source": "main-fe-header"},
                )
                response.raise_for_status()
                data = response.json().get("data", {})
            key, url = data.get("qrcode_key"), data.get("url")
            if not isinstance(key, str) or not isinstance(url, str) or not url.startswith("https://"):
                raise ProviderError("B 站未返回有效登录二维码。")
            import qrcode

            image = qrcode.make(url)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            self._qr_key = key
            return {
                "state": "waiting",
                "url": url,
                "qr_image": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(),
                "message": "请用 B 站手机 App 扫码，并在手机上确认。登录凭证仅保存在本机。",
            }
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError("无法获取登录二维码，请检查网络或 B 站服务状态。") from exc

    async def login_poll(self) -> dict:
        if not self._qr_key:
            return {"state": "no_session", "message": "请先生成登录二维码。"}
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, headers={"User-Agent": _UA}, follow_redirects=False
            ) as client:
                response = await client.get(
                    "https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
                    params={"qrcode_key": self._qr_key, "source": "main-fe-header"},
                )
                response.raise_for_status()
                code = response.json().get("data", {}).get("code")
                if code == 0:
                    jar = {
                        out: response.cookies.get(key, "")
                        for key, out in [
                            ("SESSDATA", "sessdata"),
                            ("bili_jct", "bili_jct"),
                            ("DedeUserID", "dedeuserid"),
                            ("buvid3", "buvid3"),
                        ]
                    }
                    if not jar["sessdata"] or not jar["bili_jct"]:
                        raise ProviderError("登录响应缺少凭证，请重新生成二维码。")
                    if not jar["buvid3"]:
                        try:
                            fingerprint = await client.get("https://api.bilibili.com/x/frontend/finger/spi")
                            jar["buvid3"] = fingerprint.json().get("data", {}).get("b_3", "")
                        except (httpx.HTTPError, ValueError):
                            pass
                    self._save_credential(jar)
                    self._qr_key = None
                    return {"state": "ok", "message": "登录成功，凭证已安全保存在本机。"}
            state = {86101: "waiting", 86090: "scanned", 86038: "expired"}.get(code, "unknown")
            if state == "expired":
                self._qr_key = None
            return {"state": state}
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError("查询登录状态失败，请稍后重试。") from exc

    def _save_credential(self, credential: dict) -> None:
        self.credential_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".bili-", dir=self.credential_path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(credential, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.credential_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
