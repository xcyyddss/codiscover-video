"""Reproducible fixture evaluation; scores do not estimate real user satisfaction."""

import argparse
import asyncio
import json
import tempfile
import time
from pathlib import Path

from codiscover_video.config import Settings
from codiscover_video.intelligence import Intelligence
from codiscover_video.models import GoalCreate
from codiscover_video.providers.demo import DemoProvider
from codiscover_video.retrieval import recommend
from codiscover_video.service import Service
from codiscover_video.store import Store, now


async def evaluate(output):
    with tempfile.TemporaryDirectory() as directory:
        settings = Settings(data_dir=Path(directory))
        store = Store(settings.db_path)
        provider = DemoProvider()
        model = Intelligence(settings)
        service = Service(settings, store, provider, model)
        # Diverse fixture corpus; labels are hand-authored, never scraped popularity claims.
        all_videos = await provider.search("", 20)
        if not all_videos:
            all_videos = await provider.search("放松", 20)
        for candidate in all_videos:
            video = await provider.inspect(candidate.id)
            analysis = await model.analyze(video)
            store.save_video(video.model_copy(update=analysis.model_dump(exclude={"evidence_quotes"})))
        cases = [
            {"query": "自然 雨声 森林", "expected": ["自然", "雨声", "森林", "鸟"]},
            {"query": "做饭 料理 厨房", "expected": ["料理", "做饭", "厨房"]},
            {"query": "手工 木工 修复", "expected": ["手工", "木工", "修复"]},
            {"query": "科普 天文 宇宙", "expected": ["科普", "天文", "宇宙"]},
        ]
        results = []
        for case in cases:
            goal = service.create_goal(GoalCreate(text=case["query"]))
            started = time.perf_counter()
            recs = recommend(store, goal, limit=3)
            hit = any(
                any(term in json.dumps(r["video"], ensure_ascii=False) for term in case["expected"])
                for r in recs
            )
            results.append(
                {
                    "query": case["query"],
                    "hit_at_3": int(hit),
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                    "video_ids": [r["video"]["id"] for r in recs],
                    "evidence_coverage": sum(bool(r["evidence"]) for r in recs) / max(1, len(recs)),
                }
            )
        report = {
            "evaluated_at": now(),
            "corpus": "12 authored demo fixtures, NOT real user study",
            "videos": len(store.videos()),
            "cases": results,
            "hit_at_3": sum(r["hit_at_3"] for r in results) / len(results),
            "notes": "Sanity benchmark only. Phase 2 requires held-out real queries and human labels.",
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("data/evaluation.json"))
    asyncio.run(evaluate(parser.parse_args().output))
