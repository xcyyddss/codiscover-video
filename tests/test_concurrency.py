import asyncio

import pytest
from fastapi.testclient import TestClient

from codiscover_video.app import create_app
from codiscover_video.config import Settings
from codiscover_video.models import GoalCreate


async def test_cancel_must_drain_before_resume(tmp_path):
    app = create_app(Settings(data_dir=tmp_path), start_worker=False)
    service, runner = app.state.service, app.state.runner
    goal = service.create_goal(GoalCreate(text="自然放松"))
    job = service.store.create_job(goal["id"], 2)
    original = service.provider.inspect
    entered, release = asyncio.Event(), asyncio.Event()

    async def pending(video_id):
        entered.set()
        await release.wait()
        return await original(video_id)

    service.provider.inspect = pending
    task = asyncio.create_task(runner.execute(service.store.claim()))
    await entered.wait()
    runner.cancel(job["id"])
    with pytest.raises(ValueError, match="正在结束"):
        runner.resume(job["id"])
    release.set()
    await task
    assert runner.active_job_id is None
    assert service.store.job(job["id"])["status"] == "cancelled"
    runner.resume(job["id"])
    assert service.store.job(job["id"])["status"] == "queued"


def test_resume_cannot_bypass_goal_dedup(tmp_path):
    app = create_app(Settings(data_dir=tmp_path), start_worker=False)
    service, runner = app.state.service, app.state.runner
    goal = service.create_goal(GoalCreate(text="放松"))
    job = service.store.create_job(goal["id"], 1)
    runner.cancel(job["id"])
    service.store.create_job(goal["id"], 1)
    with pytest.raises(ValueError, match="已有"):
        runner.resume(job["id"])


def test_export_is_complete_beyond_list_page_limit(tmp_path):
    app = create_app(Settings(data_dir=tmp_path), start_worker=False)
    store = app.state.service.store
    goal = app.state.service.create_goal(GoalCreate(text="自然"))
    for index in range(510):
        store.add_feedback(
            {"goal_id": goal["id"], "video_id": None, "rating": 3, "note": str(index), "context": ""}
        )
    session = store.session(goal["id"])
    store.add_message(session, "user", "hello")
    assert len(store.feedback()) == 500
    exported = store.export()
    assert len(exported["feedback"]) == 510
    assert exported["sessions"] == [{"id": session, "goal_id": goal["id"]}]


async def test_worker_ownership_lock(tmp_path):
    a = create_app(Settings(data_dir=tmp_path), start_worker=False).state.runner
    b = create_app(Settings(data_dir=tmp_path), start_worker=False).state.runner
    await a.start()
    try:
        with pytest.raises(RuntimeError, match="已有 worker"):
            await b.start()
    finally:
        await a.stop()


def test_clear_blocks_inflight_import(tmp_path):
    app = create_app(Settings(data_dir=tmp_path), start_worker=False)
    with TestClient(app) as client:
        app.state.service.active_imports = 1
        assert client.delete("/api/memory").status_code == 409
        app.state.service.active_imports = 0
        assert client.delete("/api/memory").status_code == 200
