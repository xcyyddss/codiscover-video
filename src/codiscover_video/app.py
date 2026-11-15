import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import Settings
from .intelligence import Intelligence
from .jobs import JobRunner
from .llm import LLMError
from .models import (
    ChatRequest,
    FeedbackCreate,
    GoalCreate,
    GoalPatch,
    JobCreate,
    Presence,
    RecommendationRequest,
    VideoImport,
)
from .providers.base import LoginRequired, ProviderError
from .providers.demo import DemoProvider
from .retrieval import recommend
from .service import Service
from .store import Store


def create_app(settings=None, *, provider=None, intelligence=None, start_worker=True):
    settings = settings or Settings.from_env()
    store = Store(settings.db_path)
    if provider is None:
        if settings.provider_mode == "bilibili":
            from .providers.bilibili import BilibiliProvider

            provider = BilibiliProvider(settings)
        else:
            provider = DemoProvider()
    service = Service(settings, store, provider, intelligence or Intelligence(settings))
    runner = JobRunner(service)

    @asynccontextmanager
    async def lifespan(app):
        if start_worker:
            await runner.start()
        yield
        await runner.stop()
        for component in (provider, service.intelligence):
            close = getattr(component, "aclose", None)
            if close:
                await close()

    app = FastAPI(title="CoDiscover Video", version="0.1.0", lifespan=lifespan)
    app.state.service, app.state.runner = service, runner
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "仅允许工作台同源请求"}, status_code=403)
        # Browser simple form requests cannot mutate a local service.
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            content_type = request.headers.get("content-type", "")
            if request.headers.get("content-length", "0") != "0" and "application/json" not in content_type:
                return JSONResponse({"detail": "请使用 application/json"}, status_code=415)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data: https://*.hdslb.com; "
            "style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        )
        return response

    @app.exception_handler(KeyError)
    async def not_found(request, exc):
        return JSONResponse({"detail": str(exc).strip("'")}, status_code=404)

    @app.exception_handler(ValueError)
    async def bad_request(request, exc):
        return JSONResponse({"detail": str(exc)[:300]}, status_code=400)

    @app.exception_handler(ProviderError)
    async def provider_failure(request, exc):
        return JSONResponse(
            {"detail": str(exc)[:300]}, status_code=409 if isinstance(exc, LoginRequired) else 502
        )

    @app.exception_handler(LLMError)
    async def llm_failure(request, exc):
        return JSONResponse({"detail": str(exc), "code": exc.code}, status_code=502)

    @app.exception_handler(Exception)
    async def internal_failure(request, exc):
        # Never include request bodies, tokens, credentials or upstream headers.
        code = getattr(exc, "code", type(exc).__name__)
        return JSONResponse({"detail": f"操作失败（{code}），请检查连接配置或任务执行记录"}, status_code=502)

    @app.get("/api/status")
    async def status():
        return {
            "mode": settings.provider_mode,
            "llm_mode": settings.llm_mode,
            "counts": store.counts(),
            "provider": {"name": settings.provider_mode, "configured": True},
            "online": runner.online,
            "scheduler_enabled": settings.scheduler_enabled,
            "model": settings.model,
            "api_key_configured": bool(settings.api_key.get_secret_value()),
        }

    @app.get("/api/health")
    async def health():
        return await provider.health()

    @app.get("/api/goals")
    async def goals():
        return store.goals()

    @app.post("/api/goals", status_code=201)
    async def create_goal(body: GoalCreate):
        return service.create_goal(body)

    @app.patch("/api/goals/{goal_id}")
    async def update_goal(goal_id: str, body: GoalPatch):
        lock = service.goal_locks.setdefault(goal_id, asyncio.Lock())
        async with lock:
            goal = store.goal(goal_id)
            changes = body.model_dump(exclude_none=True)
            if "interval_minutes" in changes:
                GoalCreate(text=goal["text"], interval_minutes=changes["interval_minutes"])
            if "text" in changes and not changes["text"].strip():
                raise ValueError("目标不能为空")
            goal.update(changes)
            if "text" in changes and "intent" not in changes:
                goal["intent"]["goal"] = changes["text"]
            if "context" in changes and "intent" not in changes:
                goal["intent"]["context"] = changes["context"]
            return store.save_goal(goal)

    @app.get("/api/videos")
    async def videos(query: str = "", goal_id: str | None = None):
        if goal_id:
            return [r["video"] for r in recommend(store, store.goal(goal_id), query, 12)]
        all_videos = store.videos()
        return [
            v
            for v in all_videos
            if not query
            or query.lower()
            in " ".join(
                [
                    v["title"],
                    v["summary"],
                    v["author"],
                    *v["traits"],
                    *v["tags"],
                    *(segment["text"] for segment in v["segments"]),
                ]
            ).lower()
        ]

    @app.post("/api/videos/import")
    async def import_video(body: VideoImport):
        return await service.import_video(body.video_id)

    @app.post("/api/chat")
    async def chat(body: ChatRequest):
        if not body.message.strip():
            raise ValueError("消息不能为空")
        return await service.chat(body)

    @app.get("/api/messages")
    async def messages(session_id: str):
        return store.messages(session_id)

    @app.post("/api/recommendations")
    async def recommendations(body: RecommendationRequest):
        return recommend(store, store.goal(body.goal_id), body.query, body.limit)

    @app.post("/api/feedback", status_code=201)
    async def feedback(body: FeedbackCreate):
        store.goal(body.goal_id)
        if body.video_id:
            store.video(body.video_id)
        if not body.video_id and not body.note.strip():
            raise ValueError("无视频反馈请填写体验描述")
        return store.add_feedback(body.model_dump())

    @app.get("/api/feedback")
    async def feedback_list(goal_id: str | None = None):
        if goal_id:
            store.goal(goal_id)
        return store.feedback(goal_id)

    @app.post("/api/jobs", status_code=202)
    async def job_create(body: JobCreate):
        store.goal(body.goal_id)
        return store.create_job(body.goal_id, body.max_videos)

    @app.get("/api/jobs")
    async def jobs():
        return store.jobs()

    @app.get("/api/jobs/{job_id}")
    async def job(job_id: str):
        return store.job(job_id)

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel(job_id: str):
        return runner.cancel(job_id)

    @app.post("/api/jobs/{job_id}/resume")
    async def resume(job_id: str):
        return runner.resume(job_id)

    @app.post("/api/presence")
    async def presence(body: Presence):
        return runner.presence(body.online)

    @app.post("/api/auth/start")
    async def auth_start():
        if not hasattr(provider, "login_start"):
            raise HTTPException(400, "演示模式无需登录；请将 CDV_PROVIDER 改为 bilibili 后重启")
        return await provider.login_start()

    @app.post("/api/auth/poll")
    async def auth_poll():
        if not hasattr(provider, "login_poll"):
            raise HTTPException(400, "演示模式无需登录")
        return await provider.login_poll()

    @app.get("/api/export")
    async def export():
        return JSONResponse(
            store.export(), headers={"Content-Disposition": "attachment; filename=codiscover-video.json"}
        )

    @app.delete("/api/memory")
    async def clear_memory():
        if runner.active_job_id or service.active_imports:
            raise HTTPException(409, "请等待正在结束的探索或导入请求完成，再清除数据")
        if any(j["status"] in {"running", "queued"} for j in store.jobs()):
            raise HTTPException(409, "请先取消并等待探索任务停止，再清除数据")
        if any(lock.locked() for lock in service.goal_locks.values()):
            raise HTTPException(409, "请等待对话完成后再清除数据")
        store.clear()
        return {"cleared": True}

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(static / "index.html")

    return app
