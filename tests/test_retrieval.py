from codiscover_video.concepts import preference_matches
from codiscover_video.models import Video
from codiscover_video.providers.demo import _videos
from codiscover_video.retrieval import recommend, retrieve
from codiscover_video.store import Store, now


def populated_store(tmp_path):
    store = Store(tmp_path / "retrieval.sqlite3")
    goal = {"id": "goal", "text": "放松", "created_at": now(), "intent": {"goal": "放松"}}
    store.save_goal(goal)
    for video in _videos():
        store.save_video(video.model_copy(update={"confidence": 0.65}))
    return store, goal


def test_hash_collisions_do_not_create_false_retrieval_matches(tmp_path):
    store, _ = populated_store(tmp_path)
    # These words previously collided with Chinese fixture tokens in 256 buckets.
    assert retrieve(store, "aerospace") == []
    assert retrieve(store, "QZPVKKTT") == []
    assert retrieve(store, "我想看看视频") == []
    assert any(hit["video_id"] == "demo-science" for hit in retrieve(store, "科普"))


def test_disliked_canonical_concept_filters_actual_content_terms(tmp_path):
    store, goal = populated_store(tmp_path)
    goal["intent"]["disliked_tags"] = ["轻度科普"]
    results = recommend(store, goal, "自然 科普", 12)
    ids = {item["video"]["id"] for item in results}
    assert "demo-science" not in ids
    assert "demo-math" not in ids
    # Gentle curiosity in nature is not automatically educational science.
    assert "demo-birds" in ids


def test_negative_stimulation_does_not_filter_low_stimulation_content(tmp_path):
    store, goal = populated_store(tmp_path)
    store.save_video(
        Video(
            id="demo-calm",
            title="低刺激的自然观察",
            tags=["低刺激"],
            description="没有快节奏，不吵闹，只看自然。",
            confidence=0.3,
        )
    )
    goal["intent"]["disliked_tags"] = ["高刺激"]
    results = recommend(store, goal, "自然 刺激", 12)
    ids = {item["video"]["id"] for item in results}
    assert "demo-calm" in ids
    assert "demo-game" not in ids
    assert "demo-workout" not in ids


def test_concept_matching_handles_direct_custom_tags_and_negation():
    assert preference_matches("轻度科普", "科普、观察、轻度好奇")
    assert not preference_matches("轻度科普", "自然、散步、轻度好奇")
    assert not preference_matches("高刺激", "低刺激，没有快节奏，不吵闹")
    assert preference_matches("猫咪", "看看猫咪睡觉")
