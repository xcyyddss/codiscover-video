"""Durable single-worker state machine with idempotent video upserts and per-run budgets."""

import asyncio
import logging
import time
from contextlib import suppress
from datetime import UTC, datetime

from .providers.base import LoginRequired
from .store import now


class JobRunner:
    def __init__(self, service):
        self.service = service
        self.store = service.store
        self.task = None
        self.stopping = False
        self.presence_until = 0.0
        self.active_job_id = None
        self._process_lock = None

    @property
    def online(self):
        return time.monotonic() < self.presence_until

    def presence(self, online):
        self.presence_until = time.monotonic() + 45 if online else 0
        return {"online": self.online}

    async def start(self):
        # One worker owns a data directory. OS releases this lock after a crash.
        import fcntl

        self._process_lock = (self.service.settings.data_dir / "worker.lock").open("a")
        try:
            fcntl.flock(self._process_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._process_lock.close()
            raise RuntimeError("此数据目录已有 worker；serve 与 worker 不能同时运行") from None
        self.store.recover()
        self.task = asyncio.create_task(self.loop(), name="codiscover_video-worker")

    async def stop(self):
        self.stopping = True
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        if self._process_lock:
            self._process_lock.close()

    async def loop(self):
        while not self.stopping:
            try:
                self.schedule()
                job = self.store.claim()
                if job:
                    await self.execute(job)
                else:
                    await asyncio.sleep(self.service.settings.worker_poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Keep the worker alive; job exceptions are recorded by execute.
                logging.getLogger(__name__).error("worker loop failed: %s", type(exc).__name__)
                await asyncio.sleep(self.service.settings.worker_poll_seconds)

    def schedule(self):
        if self.online or not self.service.settings.scheduler_enabled:
            return
        today = now()[:10]
        count = sum(j["created_at"].startswith(today) for j in self.store.jobs())
        if count >= self.service.settings.max_daily_jobs:
            return
        for goal in self.store.goals():
            interval = goal.get("interval_minutes", 0)
            if not goal.get("active", True) or not interval:
                continue
            last = goal.get("last_scheduled_at")
            elapsed = (datetime.now(UTC) - datetime.fromisoformat(last)).total_seconds() if last else 1e9
            if elapsed >= interval * 60:
                self.store.create_job(goal["id"], 4)
                goal["last_scheduled_at"] = now()
                self.store.save_goal(goal)
                count += 1
                if count >= self.service.settings.max_daily_jobs:
                    break

    def cancelled(self, job_id):
        return self.store.job(job_id)["status"] == "cancelled"

    async def execute(self, job):
        job_id = job["id"]
        self.active_job_id = job_id
        progress = job.get("result", {})
        ingested = list(progress.get("video_ids", []))
        errors = list(progress.get("skipped", []))
        queries = list(progress.get("queries", []))
        scope = None
        try:
            goal = self.store.goal(job["goal_id"])
            self.store.event(
                job_id, "started", {"resumed_videos": len(ingested), "max_videos": job["max_videos"]}
            )
            budget = max(0, 2 * job["max_videos"] + 4 - progress.get("calls", 0))
            with self.service.intelligence.usage_scope(job_id, max_calls=budget) as scope:
                if not queries:
                    async with self.service.model_gate:
                        queries = await self.service.intelligence.plan(goal, self.service.profile(goal))
                    self.store.event(job_id, "planned", {"queries": queries})
                seen = set(ingested)
                attempts = 0
                for query in queries[:3]:
                    if self.cancelled(job_id) or len(ingested) >= job["max_videos"]:
                        break
                    self.store.event(job_id, "search", {"query": query})
                    candidates = await self.service.provider.search(query, min(job["max_videos"] * 2, 12))
                    for candidate in candidates:
                        if self.cancelled(job_id) or len(ingested) >= job["max_videos"]:
                            break
                        if candidate.id in seen:
                            continue
                        seen.add(candidate.id)
                        attempts += 1
                        if attempts > job["max_videos"] * 2:
                            break
                        self.store.event(
                            job_id, "inspect", {"video_id": candidate.id, "title": candidate.title}
                        )
                        try:
                            video = await self.service.provider.inspect(candidate.id)
                            if self.cancelled(job_id):
                                break
                            async with self.service.model_gate:
                                analysis = await self.service.intelligence.analyze(video)
                            if self.cancelled(job_id):
                                break
                            video = video.model_copy(
                                update={
                                    "summary": analysis.summary,
                                    "traits": analysis.traits,
                                    "confidence": analysis.confidence,
                                }
                            )
                            self.store.save_video(video)
                            ingested.append(video.id)
                            self.store.event(
                                job_id,
                                "indexed",
                                {
                                    "video_id": video.id,
                                    "content_status": video.content_status,
                                    "segments": len(video.segments),
                                },
                            )
                        except LoginRequired:
                            raise
                        except Exception as exc:
                            # Provider/client exception strings are sanitised at their boundary.
                            code = getattr(exc, "code", "video_failed")
                            if code in {
                                "authentication",
                                "authentication_error",
                                "budget_exhausted",
                                "account",
                                "account_error",
                            }:
                                raise
                            errors.append(
                                {"video_id": candidate.id, "error": type(exc).__name__, "code": code}
                            )
                            self.store.event(job_id, "skipped", errors[-1])
                        self.store.update_job(
                            job_id,
                            result={
                                "video_ids": ingested,
                                "skipped": errors,
                                "queries": queries,
                                "calls": progress.get("calls", 0) + scope.calls,
                                "usage": progress.get("usage", []) + scope.records,
                            },
                        )
                if not self.cancelled(job_id):
                    status = "completed" if ingested else "failed"
                    self.store.update_job(
                        job_id,
                        status=status,
                        error="" if ingested else "未获得可入库视频，请检查搜索结果、账号与执行记录",
                    )
                    self.store.event(job_id, status, {"indexed": len(ingested), "skipped": len(errors)})
        except LoginRequired:
            if not self.cancelled(job_id):
                self.store.update_job(job_id, status="paused", error="需要 B站登录，请在设置中扫码后恢复任务")
                self.store.event(job_id, "login_required", {"message": "等待用户扫码登录"})
        except asyncio.CancelledError:
            if not self.cancelled(job_id):
                self.store.update_job(job_id, status="queued", error="服务停止，启动后从检查点恢复")
            raise
        except Exception as exc:
            if not self.cancelled(job_id):
                code = getattr(exc, "code", type(exc).__name__)
                self.store.update_job(job_id, status="failed", error=f"探索失败：{code}，请检查模型/平台连接")
                self.store.event(job_id, "failed", {"code": code})
        finally:
            self.store.update_job(
                job_id,
                result={
                    "video_ids": ingested,
                    "skipped": errors,
                    "queries": queries,
                    "calls": (progress.get("calls", 0) + scope.calls) if scope else progress.get("calls", 0),
                    "usage": (progress.get("usage", []) + scope.records)
                    if scope
                    else progress.get("usage", []),
                },
            )
            self.active_job_id = None

    def cancel(self, job_id):
        job = self.store.job(job_id)
        if job["status"] in {"queued", "running", "paused"}:
            self.store.update_job(job_id, status="cancelled")
            self.store.event(job_id, "cancelled", {"message": "已取消，当前网络请求完成后停止后续步骤"})
        return self.store.job(job_id)

    def resume(self, job_id):
        if self.active_job_id == job_id:
            raise ValueError("任务正在结束当前请求，请稍后恢复")
        job = self.store.job(job_id)
        if job["status"] not in {"paused", "failed", "cancelled"}:
            raise ValueError("仅暂停、失败或已取消任务可以恢复")
        if any(
            other["id"] != job_id
            and other["goal_id"] == job["goal_id"]
            and other["status"] in {"queued", "running", "paused"}
            for other in self.store.jobs()
        ):
            raise ValueError("该目标已有待执行任务，请先完成或取消其他任务")
        self.store.update_job(job_id, status="queued", error="")
        self.store.event(job_id, "resumed", {"checkpoint": job["result"].get("video_ids", [])})
        return self.store.job(job_id)
