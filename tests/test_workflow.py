import asyncio

import pytest
from fastapi.testclient import TestClient

from codiscover_video.app import create_app
from codiscover_video.config import Settings
from codiscover_video.models import GoalCreate, Segment, Video
from codiscover_video.providers.base import LoginRequired
from codiscover_video.retrieval import recommend, retrieve
from codiscover_video.store import Store


@pytest.fixture
def app(tmp_path):
    return create_app(
        Settings(data_dir=tmp_path, credential_path=tmp_path / "credential.json"), start_worker=False
    )


def test_end_to_end_chat_feedback_and_export(app):
    with TestClient(app) as client:
        goal = client.post("/api/goals", json={"text": "下班后安静放松，喜欢自然和做饭"}).json()
        job = client.post("/api/jobs", json={"goal_id": goal["id"], "max_videos": 6}).json()
        claimed = app.state.service.store.claim()
        asyncio.run(app.state.runner.execute(claimed))
        completed = client.get("/api/jobs/" + job["id"]).json()
        assert completed["status"] == "completed", completed
        assert len(completed["result"]["video_ids"]) == 6
        assert any(e["kind"] == "indexed" for e in completed["events"])
        body = {"goal_id": goal["id"], "message": "我希望安静放松，不要游戏"}
        chat = client.post("/api/chat", json=body)
        assert chat.status_code == 200, chat.text
        chat = chat.json()
        assert chat["session_id"] and chat["reply"]
        assert len(client.get("/api/messages", params={"session_id": chat["session_id"]}).json()) == 2
        recs = client.post("/api/recommendations", json={"goal_id": goal["id"]}).json()
        assert recs and recs[0]["evidence"]
        rejected = recs[0]["video"]["id"]
        assert (
            client.post(
                "/api/feedback",
                json={"goal_id": goal["id"], "video_id": rejected, "rating": 1, "note": "不适合下班"},
            ).status_code
            == 201
        )
        after = client.post("/api/recommendations", json={"goal_id": goal["id"]}).json()
        assert rejected not in [r["video"]["id"] for r in after]
        assert (
            client.post(
                "/api/feedback", json={"goal_id": goal["id"], "rating": 4, "note": "今天更想有熟悉声音陪伴"}
            ).status_code
            == 201
        )
        export = client.get("/api/export").json()
        assert len(export["feedback"]) == 2 and len(export["messages"]) == 2
        assert "api_key" not in export and "credential" not in export
        assert client.delete("/api/memory").status_code == 200
        assert client.get("/api/status").json()["counts"]["videos"] == 0


def test_validation_and_origin_boundaries(app):
    with TestClient(app) as client:
        assert client.post("/api/goals", json={"text": " "}).status_code == 422
        assert client.post("/api/goals", json={"text": "x", "interval_minutes": 1}).status_code == 422
        assert (
            client.post(
                "/api/goals", json={"text": "x"}, headers={"Origin": "https://evil.example"}
            ).status_code
            == 403
        )
        assert client.get("/api/status", headers={"Host": "evil.example"}).status_code == 400
        assert client.post("/api/goals", data={"text": "x"}).status_code == 415
        assert client.post("/api/jobs", json={"goal_id": "missing"}).status_code == 404
        goal = client.post("/api/goals", json={"text": "放松"}).json()
        assert client.post("/api/feedback", json={"goal_id": goal["id"], "rating": 10}).status_code == 422
        assert client.post("/api/feedback", json={"goal_id": goal["id"], "rating": 3}).status_code == 400
        one = client.post("/api/jobs", json={"goal_id": goal["id"]}).json()
        two = client.post("/api/jobs", json={"goal_id": goal["id"]}).json()
        assert one["id"] == two["id"]
        assert client.delete("/api/memory").status_code == 409
        assert client.post("/api/jobs/" + one["id"] + "/cancel").json()["status"] == "cancelled"
        assert client.post("/api/jobs/" + one["id"] + "/resume").json()["status"] == "queued"


async def test_login_pause_checkpoint_resume(app):
    service = app.state.service
    goal = service.create_goal(GoalCreate(text="放松自然"))
    job = service.store.create_job(goal["id"], 2)
    original = service.provider.inspect

    async def need_login(video_id):
        raise LoginRequired("login required")

    service.provider.inspect = need_login
    await app.state.runner.execute(service.store.claim())
    assert service.store.job(job["id"])["status"] == "paused"
    service.provider.inspect = original
    app.state.runner.resume(job["id"])
    await app.state.runner.execute(service.store.claim())
    assert service.store.job(job["id"])["status"] == "completed"
    assert len(service.store.job(job["id"])["result"]["video_ids"]) == 2


async def test_cancellation_does_not_commit_inflight_video(app):
    service = app.state.service
    goal = service.create_goal(GoalCreate(text="放松"))
    job = service.store.create_job(goal["id"], 2)
    original = service.provider.inspect

    async def cancel_during_inspect(video_id):
        app.state.runner.cancel(job["id"])
        return await original(video_id)

    service.provider.inspect = cancel_during_inspect
    await app.state.runner.execute(service.store.claim())
    assert service.store.job(job["id"])["status"] == "cancelled"
    assert not service.store.videos()


def test_restart_recovery_and_scheduler_presence(app):
    service, runner = app.state.service, app.state.runner
    service.create_goal(GoalCreate(text="自然放松", interval_minutes=15))
    runner.presence(True)
    runner.schedule()
    assert not service.store.jobs()
    runner.presence(False)
    runner.schedule()
    assert len(service.store.jobs()) == 1
    job = service.store.claim()
    assert job["status"] == "running"
    service.store.recover()
    assert service.store.job(job["id"])["status"] == "queued"
    runner.schedule()
    assert len(service.store.jobs()) == 1


def test_rag_time_evidence_and_idempotent_upsert(tmp_path):
    store = Store(tmp_path / "test.db")
    video = Video(
        id="one",
        title="竹林里的雨声",
        source="demo",
        confidence=0.8,
        segments=[Segment(start=20, end=40, text="安静的竹林里只有雨声，让人放松")],
        summary="自然放松",
        traits=["安静"],
        content_status="subtitle",
    )
    store.save_video(video)
    store.save_video(video)
    hits = retrieve(store, "雨声放松")
    assert any(h["start"] == 20 and h["source"] == "subtitle" for h in hits)
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM chunks").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM chunks_fts").fetchone()[0] == 2
    goal = {"id": "goal", "text": "雨声放松", "intent": {"disliked_tags": ["安静"]}}
    assert recommend(store, goal) == []
