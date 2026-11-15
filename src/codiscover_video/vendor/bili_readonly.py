"""Read-only extraction adapted from dsh-bili-scroll 6f3ab83993ccc51df9331048a8f9a51bef19bc28.
Original: c1overzz and AGI_Ananas, MIT. See bundled LICENSE files.
Changes: explicit credential path, anonymous metadata, timestamped subtitles,
bounded resources and requests. No platform-write functions are included.
"""

# ruff: noqa: BLE001, SIM117
# Upstream catches independent network/frame failures to return partial evidence.
import asyncio
import base64
import json
import random
import struct
import sys
from contextvars import ContextVar
from pathlib import Path

from bilibili_api import Credential, search, video
from mcp.types import ImageContent, TextContent

CRED_CONTEXT = ContextVar("bili_credential_path", default=Path("data/bili_credential.json"))
_WEB_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
_HTTP_TIMEOUT = 25
_MAX_IMAGE_BYTES = 1_500_000
_FRAME_MAX = 3
_TILE_W_DEFAULT = 480
_TILE_H_DEFAULT = 270


async def _throttle():
    await asyncio.sleep(random.uniform(0.6, 1.6))


def load_credential() -> Credential:
    """从文件加载凭证"""
    CRED_FILE = CRED_CONTEXT.get()
    if not CRED_FILE.exists():
        return None
    try:
        with open(CRED_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        # ⚠️ 文件写坏一次（上一次写被打断）会让**所有**工具一起 JSONDecodeError：
        #    这里退化成「没登录」，重新扫一次码就能恢复。（2026-10-16 审查修）
        print("[bili-mcp] 凭证文件不可读或格式错误", file=sys.stderr)
        return None
    if not isinstance(data, dict):
        return None
    return Credential(
        sessdata=data.get("sessdata", ""),
        bili_jct=data.get("bili_jct", ""),
        buvid3=data.get("buvid3") or "",
        dedeuserid=data.get("dedeuserid", ""),
    )


def get_cred() -> Credential:
    """获取凭证，未登录时引导用户调用 bili_login"""
    cred = load_credential()
    if not cred:
        raise RuntimeError("未登录B站，请在工作台扫码登录。")
    return cred


async def bili_search(keyword: str, num: int = 10, order: str = "totalrank", page: int = 1) -> str:
    """
    搜索B站视频

    Args:
        keyword: 搜索关键词，如"AI Agent"、"大模型教程"
        num: 返回视频数量，默认10，最大50
        order: 排序方式 totalrank=综合 click=播放量 pubdate=最新 dm=弹幕
        page: 第几页，从 1 开始（一页约 20 条；想多看一屏就翻页）

    Returns:
        JSON格式的视频列表，包含标题、BV号、播放量、评论数、UP主等
    """
    await _throttle()
    order_map = {
        "totalrank": search.OrderVideo.TOTALRANK,
        "click": search.OrderVideo.CLICK,
        "pubdate": search.OrderVideo.PUBDATE,
        "dm": search.OrderVideo.DM,
    }
    order_enum = order_map.get(order, search.OrderVideo.TOTALRANK)

    result = await search.search_by_type(
        keyword=keyword,
        search_type=search.SearchObjectType.VIDEO,
        page=max(1, int(page or 1)),
        order_type=order_enum,
    )

    videos = []
    for item in result.get("result", [])[:num]:
        title = item.get("title", "").replace('<em class="keyword">', "").replace("</em>", "")
        videos.append(
            {
                "bvid": item.get("bvid", ""),
                "aid": item.get("aid", 0),
                "title": title,
                "author": item.get("author", ""),
                "play": item.get("play", 0),
                "review": item.get("review", 0),
                "danmaku": item.get("video_review", 0),
                "duration": item.get("duration", ""),
                "pubdate": item.get("pubdate", 0),
                "description": item.get("description", "")[:200],
            }
        )

    return json.dumps({"keyword": keyword, "count": len(videos), "videos": videos}, ensure_ascii=False)


async def bili_subtitle(bvid: str) -> str:
    """
    获取B站视频的AI字幕（语音转文字）

    Args:
        bvid: 视频BV号，如"BV1uNk1YxEJQ"

    Returns:
        视频的完整字幕文本
    """
    await _throttle()
    cred = get_cred()
    v = video.Video(bvid=bvid, credential=cred)

    info = await v.get_info()
    cid = info.get("cid", 0)
    if not cid and info.get("pages"):
        cid = info["pages"][0].get("cid", 0)

    if not cid:
        return json.dumps({"error": "无法获取cid"}, ensure_ascii=False)

    subtitle_list = await v.get_subtitle(cid=cid)
    subtitles = subtitle_list.get("subtitles", [])

    if not subtitles:
        return json.dumps({"message": "该视频没有字幕"}, ensure_ascii=False)

    # 优先AI中文字幕
    target = None
    for s in subtitles:
        if s.get("lan") in ["ai-zh", "zh-CN", "zh"]:
            target = s
            break
    if not target:
        target = subtitles[0]

    # 下载字幕
    import aiohttp

    sub_url = target.get("subtitle_url", "")
    if sub_url.startswith("//"):
        sub_url = "https:" + sub_url

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=_HTTP_TIMEOUT)) as session:
        async with session.get(_https(sub_url)) as resp:
            resp.raise_for_status()
            sub_data = json.loads(await _bounded_read(resp.content))

    texts = [item.get("content", "") for item in sub_data.get("body", [])]
    full_text = "\n".join(texts)

    return json.dumps(
        {
            "bvid": bvid,
            "title": info.get("title", ""),
            "language": target.get("lan_doc", ""),
            "segments": [
                {
                    "start": float(item.get("from", 0)),
                    "end": float(item.get("to", 0)),
                    "text": str(item.get("content", "")),
                }
                for item in sub_data.get("body", [])
            ],
            "text": full_text,
        },
        ensure_ascii=False,
    )


async def bili_video_info(bvid: str) -> str:
    """
    获取B站视频的详细信息

    Args:
        bvid: 视频BV号

    Returns:
        视频标题、描述、UP主、播放量、评论数、收藏数等详细数据
    """
    cred = load_credential()
    v = video.Video(bvid=bvid, credential=cred)
    info = await v.get_info()
    stat = info.get("stat", {})

    return json.dumps(
        {
            "bvid": info.get("bvid"),
            "aid": info.get("aid"),
            "title": info.get("title"),
            "description": info.get("desc"),
            "author": info.get("owner", {}).get("name"),
            "duration": info.get("duration"),
            "pages": len(info.get("pages", [])),
            "tags": [t.get("tag_name") for t in info.get("tag", []) if t.get("tag_name")],
            "stat": {
                "view": stat.get("view", 0),
                "danmaku": stat.get("danmaku", 0),
                "reply": stat.get("reply", 0),
                "favorite": stat.get("favorite", 0),
                "coin": stat.get("coin", 0),
                "like": stat.get("like", 0),
                "share": stat.get("share", 0),
            },
        },
        ensure_ascii=False,
    )


def _https(url: str) -> str:
    """把 B 站的图片/资源地址统一成 https。

    view 接口给的 `pic` 实测 10/10 都是 `http://`，而 http 不会跳转 https；
    图床请求再带上 SESSDATA 就等于把登录凭据明文发出去。
    """
    u = str(url or "")
    if u.startswith("//"):
        u = "https:" + u
    if u.startswith("http://"):
        u = "https://" + u[len("http://") :]
    from urllib.parse import urlsplit

    parsed = urlsplit(u)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not any(
        host == d or host.endswith("." + d) for d in ("hdslb.com", "bilibili.com", "bilivideo.com")
    ):
        raise ValueError("Untrusted Bilibili resource URL")
    return u


def _image_headers() -> dict:
    """图床请求头：只给 UA + Referer，**不带 Cookie**（图床不需要登录态）。"""
    return {"User-Agent": _WEB_UA, "Referer": "https://www.bilibili.com/"}


def _text_only(payload: dict) -> list:
    return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))]


def _snapshot_entries(data: dict, duration: float = 0.0) -> list:
    """把 videoshot 的 index 归一成 [(秒, 块序号), ...]。

    时间单位实测两种都出现过（秒 / 毫秒），只能靠和时长比较来判断：
    超过时长 1.5 倍才当毫秒。⚠️ 别再加"绝对值大于某个数就当毫秒"这类判据 ——
    6 小时视频的秒数（22163）会被误判成毫秒，三帧全塌到同一块、时间缩小 1000 倍。
    """
    raws = []
    for i, item in enumerate(data.get("index") or []):
        ts = item.get("timestamp") if isinstance(item, dict) else item
        try:
            raws.append((float(ts), i))
        except (TypeError, ValueError):
            continue
    if not raws:
        return []

    biggest = max(v for v, _ in raws)
    looks_ms = duration > 0 and biggest > duration * 1.5
    scale = 1000.0 if looks_ms else 1.0
    return [(v / scale, i) for v, i in raws]


async def _entries_from_pvdata(data: dict) -> list:
    """index 为空时的兜底：pvdata 指向一个大端 uint16 秒数数组，与 index 逐项相同。"""
    url = _https(data.get("pvdata") or "")
    if not url:
        return []
    try:
        raw = await _fetch_bytes(url, _image_headers())
    except Exception:
        return []
    if not raw or len(raw) % 2:
        return []
    count = len(raw) // 2
    if count > 20000:  # 明显不对，别乱解
        return []
    return [(float(sec), i) for i, sec in enumerate(struct.unpack(f">{count}H", raw))]


async def _bounded_read(stream, limit: int = 8_000_000) -> bytes:
    """Read to EOF; one StreamReader.read(n) can legally return a partial chunk."""
    body = bytearray()
    async for chunk in stream.iter_chunked(64 * 1024):
        body.extend(chunk)
        if len(body) > limit:
            raise RuntimeError("Resource exceeds configured byte limit")
    return bytes(body)


async def _fetch_bytes(url: str, headers: dict) -> bytes:
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=_HTTP_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as s, s.get(_https(url), headers=headers) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        return await _bounded_read(resp.content)


def _crop_tile(sheet: bytes, tile_index: int, tw: int, th: int) -> bytes:
    """从拼图里裁出第 tile_index 块，返回 jpeg 字节。"""
    import io

    from PIL import Image

    img = Image.open(io.BytesIO(sheet))
    cols = max(1, img.width // tw)
    rows = max(1, img.height // th)
    if tile_index >= cols * rows:
        raise RuntimeError(f"第 {tile_index} 块超出拼图（只有 {cols}×{rows}）")
    col, row = tile_index % cols, tile_index // cols
    tile = img.crop((col * tw, row * th, (col + 1) * tw, (row + 1) * th)).convert("RGB")
    buf = io.BytesIO()
    tile.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


async def bili_frames(bvid: str, num: int = 3, at: list | None = None) -> list:
    """
    看这个视频里的画面（从 B 站自带的故事板里裁几帧）

    想看"它到底拍了什么"就用它。默认按时长均匀取 3 帧；
    想盯某个时刻（比如字幕里提到的那一段）就传 at（单位：秒），会取**最接近的那一块**。

    Args:
        bvid: 视频BV号，如"BV1uNk1YxEJQ"
        num: 取几帧，1-6，默认 3
        at: 指定时间点（秒）的列表；传了就以它为准，忽略 num

    Returns:
        若干张 480×270 的画面 + 一句说明（时长、实际取到的那一刻、哪几帧失败）。
        失败会写进 failed。**多P 视频的故事板通常只覆盖第 1P**，返回里会用
        covered_sec / pages 标明实际覆盖范围。图能否被看见取决于模型是否支持图片输入。
    """
    cred = load_credential()  # 故事板不登录也能取
    try:
        v = video.Video(bvid=bvid, credential=cred)
        info = await v.get_info()
    except Exception as e:
        return _text_only({"bvid": bvid, "ok": False, "error": f"取视频信息失败：{e}"})
    duration = float(info.get("duration") or 0)
    pages = info.get("pages") or []
    cid = info.get("cid") or ((pages or [{}])[0].get("cid") or 0)
    if not cid:
        return _text_only({"bvid": bvid, "ok": False, "error": "拿不到 cid"})

    # 故事板只覆盖第 1P：目标时间点按"实际覆盖的长度"算，别落到没采样的 P2 上
    covered = float(pages[0].get("duration") or duration) if pages else duration
    covered = covered or duration

    # index 会偶发返回空（实测约 25%）：先用 pvdata 兜底，两个都空才重试
    snap = {}
    entries = []
    last_err = None
    for attempt in range(2):
        try:
            snap = await v.get_video_snapshot(cid=cid, json_index=True, pvideo=False) or {}
            last_err = None
        except Exception as e:
            snap, last_err = {}, e
        entries = _snapshot_entries(snap, covered)
        if not entries:
            entries = await _entries_from_pvdata(snap)
        if entries and (snap.get("image") or []):
            break
        if attempt < 1:
            await asyncio.sleep(1.0)

    sheets = snap.get("image") or []
    if not entries or not sheets:
        err = f"（最后一次错误：{last_err}）" if last_err is not None else ""
        return _text_only(
            {
                "bvid": bvid,
                "ok": False,
                "error": f"取不到画面快照（试了 2 次）{err}",
            }
        )

    # 覆盖长度以**故事板实测到的那一刻**为准（比 P1 的标称时长保守，通常短 1–10%）
    story_max = max((e[0] for e in entries), default=0.0)
    if story_max > 0:
        covered = min(covered, story_max)

    # 目标时间点：传了 at 就用它；否则按"覆盖长度"均分
    if at:
        targets = [min(max(0.0, float(t)), max(0.0, covered - 0.1)) for t in at][:_FRAME_MAX]
    else:
        n = max(1, min(int(num or 3), _FRAME_MAX))
        targets = [covered * (i + 1) / (n + 1) for i in range(n)]

    picked = []
    for t in targets:
        ts, tile = min(entries, key=lambda e: abs(e[0] - t))
        picked.append((round(t, 1), round(ts, 1), tile))

    tw = int(snap.get("img_x_size") or _TILE_W_DEFAULT)
    th = int(snap.get("img_y_size") or _TILE_H_DEFAULT)
    headers = _image_headers()

    cache = {}

    async def get_sheet(no: int) -> bytes:
        if no not in cache:
            if no >= len(sheets):
                raise RuntimeError(f"拼图 {no} 不存在（共 {len(sheets)} 张）")
            cache[no] = await _fetch_bytes(str(sheets[no]), headers)
        return cache[no]

    # 每张拼图装几块要等第一张下来才知道（列数 = 宽 // 块宽）
    blocks, frames, failed = [], [], []
    cap = None
    for i, (want, got, tile) in enumerate(picked):
        try:
            if cap is None:
                first = await get_sheet(0)
                import io

                from PIL import Image

                im = Image.open(io.BytesIO(first))
                cap = max(1, (im.width // tw) * (im.height // th))
            sheet_no, pos = tile // cap, tile % cap
            raw = _crop_tile(await get_sheet(sheet_no), pos, tw, th)
            if len(raw) > _MAX_IMAGE_BYTES:
                raise RuntimeError(f"这一帧太大（{len(raw) // 1024}KB）")
            blocks.append(
                ImageContent(
                    type="image",
                    data=base64.b64encode(raw).decode("ascii"),
                    mimeType="image/jpeg",
                )
            )
            frames.append({"index": i, "want_sec": want, "got_sec": got, "kb": round(len(raw) / 1024, 1)})
        except Exception as e:
            failed.append({"index": i, "want_sec": want, "got_sec": got, "reason": str(e)})

    if not blocks:
        return _text_only(
            {
                "bvid": bvid,
                "ok": False,
                "error": "一帧都没取到",
                "failed": failed,
            }
        )

    return [
        TextContent(
            type="text",
            text=json.dumps(
                {
                    "bvid": bvid,
                    "ok": True,
                    "title": info.get("title"),
                    "duration": duration,
                    "covered_sec": round(covered, 1),
                    "pages": max(1, len(pages)),
                    "tile": f"{tw}x{th}",
                    "frames": frames,
                    "failed": failed,  # 空列表 = 全成
                    "note": "want_sec 是想要的时刻，got_sec 是实际取到的那一块（故事板按固定间隔切）；"
                    "多P 视频只覆盖第 1P，看 covered_sec",
                },
                ensure_ascii=False,
            ),
        ),
        *blocks,
    ]
