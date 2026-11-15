import json

import httpx
import pytest
from pydantic import SecretStr

from codiscover_video.config import Settings
from codiscover_video.intelligence import Intelligence
from codiscover_video.llm import DeepSeekClient, LLMError
from codiscover_video.models import Intent, Segment, Video


def live_intelligence(payloads):
    values = iter(payloads)
    settings = Settings(llm_mode="deepseek", api_key=SecretStr("test-key"))

    def handler(request):
        value = next(values)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": json.dumps(value, ensure_ascii=False)}}
                ]
            },
        )

    return Intelligence(settings, client=DeepSeekClient(settings, transport=httpx.MockTransport(handler)))


@pytest.mark.asyncio
async def test_demo_negative_then_user_changes_preference():
    intelligence = Intelligence(Settings())
    goal = {"text": "下班后放松", "context": "晚上"}
    first = await intelligence.chat("我喜欢游戏，但不喜欢科普", goal, {}, [], [])
    assert "游戏" in first.intent.liked_tags
    assert "轻度科普" in first.intent.disliked_tags
    assert "轻度科普" not in first.intent.liked_tags
    second = await intelligence.chat(
        "现在想试试科普，不想看游戏", goal, {"intent": first.intent.model_dump()}, [], []
    )
    assert "轻度科普" in second.intent.liked_tags
    assert "轻度科普" not in second.intent.disliked_tags
    assert "游戏" in second.intent.disliked_tags
    assert "游戏" not in second.intent.liked_tags


@pytest.mark.asyncio
async def test_metadata_analysis_never_claims_full_video_evidence():
    intelligence = live_intelligence(
        [
            {
                "summary": "自然主题，可能适合休息。",
                "traits": ["自然"],
                "confidence": 0.99,
                "evidence_quotes": [],
            }
        ]
    )
    result = await intelligence.analyze(Video(id="BV1234567890", title="山林", tags=["自然"]))
    assert result.confidence <= 0.3
    assert "未读取视频正文" in result.summary
    assert result.evidence_quotes == []


@pytest.mark.asyncio
async def test_fabricated_quotes_are_rejected_then_repaired():
    intelligence = live_intelligence(
        [
            {"summary": "山林", "traits": ["自然"], "confidence": 0.8, "evidence_quotes": ["伪造引文"]},
            {"summary": "山林", "traits": ["自然"], "confidence": 0.8, "evidence_quotes": ["今天走进森林"]},
        ]
    )
    result = await intelligence.analyze(
        Video(
            id="demo-forest",
            title="山林",
            content_status="subtitles",
            segments=[Segment(start=12, end=16, text="今天走进森林。")],
        )
    )
    assert result.evidence_quotes == ["今天走进森林"]
    assert len(intelligence.records) == 2


@pytest.mark.asyncio
async def test_unsupported_video_citations_fail_closed():
    payload = {"reply": "推荐不存在的视频", "intent": {"goal": "放松"}, "cited_video_ids": ["invented"]}
    intelligence = live_intelligence([payload, payload])
    with pytest.raises(LLMError) as error:
        await intelligence.chat("想放松", {"text": "放松"}, {}, [], [])
    assert error.value.code == "validation"


@pytest.mark.asyncio
async def test_chat_keeps_preferences_when_model_silently_omits_them():
    intelligence = live_intelligence(
        [
            {
                "reply": "可以继续探索",
                "intent": {"goal": "放松", "liked_tags": [], "disliked_tags": []},
                "cited_video_ids": [],
            }
        ]
    )
    result = await intelligence.chat(
        "还有别的吗",
        {"text": "放松"},
        {"intent": Intent(goal="放松", liked_tags=["自然"], disliked_tags=["高刺激"]).model_dump()},
        [],
        [],
    )
    assert result.intent.liked_tags == ["自然"]
    assert result.intent.disliked_tags == ["高刺激"]


@pytest.mark.asyncio
async def test_demo_includes_only_supplied_citations():
    intelligence = Intelligence(Settings())
    result = await intelligence.chat(
        "我想看自然",
        {"text": "放松"},
        {},
        [],
        [
            {
                "video": {"id": "demo-forest", "title": "森林", "content_status": "subtitles"},
                "evidence": [{"start": 10, "text": "走进森林", "source": "subtitle"}],
            }
        ],
    )
    assert result.cited_video_ids == ["demo-forest"]
    assert "森林" in result.reply


@pytest.mark.asyncio
async def test_demo_plan_avoids_explicit_dislikes():
    intelligence = Intelligence(Settings())
    queries = await intelligence.plan(
        {"text": "想看游戏"},
        {"intent": Intent(goal="放松", liked_tags=["自然"], disliked_tags=["游戏"]).model_dump()},
    )
    assert all("游戏" not in query for query in queries)


@pytest.mark.asyncio
async def test_low_stimulation_is_not_treated_as_high_stimulation():
    answer = await Intelligence(Settings()).chat("我想看低刺激视频", {"text": "放松"}, {}, [], [])
    assert "安静陪伴" in answer.intent.liked_tags
    assert "高刺激" not in answer.intent.liked_tags


@pytest.mark.asyncio
async def test_curiosity_is_preserved_as_experience_not_science_topic():
    answer = await Intelligence(Settings()).chat("我想要一点好奇和新鲜感", {"text": "放松"}, {}, [], [])
    assert "轻度好奇" in answer.intent.liked_tags
    assert "轻度科普" not in answer.intent.liked_tags


@pytest.mark.asyncio
async def test_visual_observations_are_not_treated_as_subtitle_quotes_or_retried():
    import base64

    intelligence = live_intelligence(
        [
            {
                "summary": "采样帧展示了一段软件操作演示。",
                "traits": ["软件演示"],
                "confidence": 0.9,
                "evidence_quotes": ["画面显示桌面与文字界面"],
            }
        ]
    )
    image = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xd9").decode()
    result = await intelligence.analyze(
        Video(
            id="demo-frames",
            title="软件操作",
            content_status="frames",
            image_urls=[image],
            segments=[Segment(start=37, end=37, text="故事板采样帧，仅支持画面观察", source="storyboard")],
        )
    )
    assert result.evidence_quotes == []
    assert "画面观察（模型描述）：画面显示桌面与文字界面" in result.summary
    assert "无法判断音频或完整视频" in result.summary
    assert result.confidence == 0.55
    assert len(intelligence.records) == 1


@pytest.mark.asyncio
async def test_metadata_only_analysis_cannot_retain_a_purported_subtitle_quote():
    intelligence = live_intelligence(
        [{"summary": "山林主题", "traits": ["自然"], "confidence": 0.8, "evidence_quotes": ["我是字幕"]}]
    )
    result = await intelligence.analyze(Video(id="demo-meta", title="山林"))
    assert result.evidence_quotes == []
    assert result.confidence == 0.3
    assert "我是字幕" not in result.summary
    assert len(intelligence.records) == 1
