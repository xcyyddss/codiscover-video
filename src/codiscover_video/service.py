import asyncio

from .models import Intent
from .retrieval import recommend
from .store import now, uid


class Service:
    def __init__(self, settings, store, provider, intelligence):
        self.settings, self.store = settings, store
        self.provider, self.intelligence = provider, intelligence
        self.model_gate = asyncio.Semaphore(settings.max_llm_concurrency)
        self.goal_locks = {}
        self.active_imports = 0

    def create_goal(self, body):
        goal = {
            "id": uid(),
            **body.model_dump(),
            "active": True,
            "created_at": now(),
            "intent": Intent(goal=body.text, context=body.context).model_dump(),
            "last_scheduled_at": None,
        }
        return self.store.save_goal(goal)

    def profile(self, goal):
        return {"intent": goal["intent"], "feedback": self.store.feedback(goal["id"])[:20]}

    async def import_video(self, video_id):
        self.active_imports += 1
        try:
            video = await self.provider.inspect(video_id)
            async with self.model_gate:
                analysis = await self.intelligence.analyze(video)
            video = video.model_copy(
                update={
                    "summary": analysis.summary,
                    "traits": analysis.traits,
                    "confidence": analysis.confidence,
                }
            )
            return self.store.save_video(video)
        finally:
            self.active_imports -= 1

    async def chat(self, body):
        # Serialise updates to one goal, preventing a stale turn overwriting a newer intent.
        lock = self.goal_locks.setdefault(body.goal_id, asyncio.Lock())
        async with lock:
            goal = self.store.goal(body.goal_id)
            session_id = self.store.session(goal["id"], body.session_id)
            history = [{"role": m["role"], "content": m["content"]} for m in self.store.messages(session_id)]
            results = recommend(self.store, goal, body.message, 6)
            evidence = [{**r["video"], "evidence": r["evidence"]} for r in results]
            async with self.model_gate:
                answer = await self.intelligence.chat(
                    body.message, goal, self.profile(goal), history, evidence
                )
            # Persist user/assistant only after a successful model turn, preserving retry semantics.
            self.store.add_message(session_id, "user", body.message)
            self.store.add_message(session_id, "assistant", answer.reply)
            goal = self.store.goal(body.goal_id)
            goal["intent"] = answer.intent.model_dump()
            self.store.save_goal(goal)
            citations = [r["video"] for r in results if r["video"]["id"] in answer.cited_video_ids]
            return {
                "session_id": session_id,
                "reply": answer.reply,
                "intent": goal["intent"],
                "citations": citations,
                "recommendations": recommend(self.store, goal, limit=6),
            }
