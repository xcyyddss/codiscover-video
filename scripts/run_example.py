"""Run the public synthetic scenario without dotenv, accounts, API calls or persistent memory."""

import argparse
import asyncio
import json
import tempfile
from pathlib import Path

from codiscover_video.config import Settings
from codiscover_video.intelligence import Intelligence
from codiscover_video.jobs import JobRunner
from codiscover_video.models import ChatRequest, FeedbackCreate, GoalCreate
from codiscover_video.providers.demo import DemoProvider
from codiscover_video.retrieval import recommend
from codiscover_video.service import Service
from codiscover_video.store import Store

SCENARIO_PATH = Path(__file__).resolve().parents[1] / "examples/after_work/scenario.json"


def public_recommendations(items):
    """Keep only synthetic content and explanatory evidence, never IDs of private DB rows."""
    return [
        {
            "video_id": item["video"]["id"],
            "title": item["video"]["title"],
            "reason": item["reason"],
            "evidence": item["evidence"],
        }
        for item in items
    ]


async def run_scenario(scenario_path=SCENARIO_PATH):
    scenario = json.loads(Path(scenario_path).read_text(encoding="utf-8"))
    if scenario.get("schema_version") != 1 or scenario.get("synthetic") is not True:
        raise ValueError("示例必须声明 schema_version=1 和 synthetic=true。")
    # Construct Settings directly. Do not call from_env(), load dotenv, or reuse an
    # application's data directory; even a live-configured shell remains a demo.
    with tempfile.TemporaryDirectory(prefix="codiscover-example-") as directory:
        settings = Settings(
            data_dir=Path(directory),
            credential_path=Path(directory) / "unused-credential.json",
            provider_mode="demo",
            llm_mode="demo",
            scheduler_enabled=False,
        )
        store = Store(settings.db_path)
        intelligence = Intelligence(settings)
        service = Service(settings, store, DemoProvider(), intelligence)
        runner = JobRunner(service)
        goal = service.create_goal(GoalCreate(**scenario["goal"]))
        job = store.create_job(goal["id"], scenario["exploration"]["max_videos"])
        await runner.execute(store.claim())
        job = store.job(job["id"])
        answer = await service.chat(ChatRequest(goal_id=goal["id"], **scenario["clarification"]))
        goal = store.goal(goal["id"])
        before = recommend(store, goal, scenario["recommendation_query"], limit=6)
        feedback_input = FeedbackCreate(goal_id=goal["id"], **scenario["feedback"])
        store.video(feedback_input.video_id)
        store.add_feedback(feedback_input.model_dump())
        after = recommend(store, goal, scenario["recommendation_query"], limit=6)
        learning = service.create_goal(GoalCreate(**scenario["learning_goal"]))
        for_learning = recommend(store, learning, scenario["learning_query"], limit=6)
        free_input = FeedbackCreate(goal_id=goal["id"], **scenario["free_feedback"])
        free = store.add_feedback(free_input.model_dump())
        target = scenario["feedback"]["video_id"]
        videos = store.videos()
        checks = {
            "exploration_completes": job["status"] == "completed",
            "knowledge_has_timestamped_synthetic_evidence": bool(videos)
            and all(
                video["source"] == "demo"
                and not video["url"]
                and video["segments"]
                and all(segment["source"] == "demo_subtitle" for segment in video["segments"])
                and any(segment["end"] > segment["start"] for segment in video["segments"])
                for video in videos
            ),
            "feedback_target_initially_recommended": target in [item["video"]["id"] for item in before],
            "negative_feedback_excludes_only_current_goal": target
            not in [item["video"]["id"] for item in after],
            "learning_goal_can_still_recommend_feedback_target": target
            in [item["video"]["id"] for item in for_learning],
            "free_feedback_is_saved_without_video": free["video_id"] is None
            and any(item["id"] == free["id"] for item in store.feedback(goal["id"])),
            "no_model_http_calls": not intelligence.records and job["result"]["calls"] == 0,
        }
        if set(checks) != set(scenario["expected_invariants"]):
            raise AssertionError("场景声明与已执行验收不一致。")
        if not all(checks.values()):
            raise AssertionError(f"示例验收失败：{[key for key, value in checks.items() if not value]}")
        # Omit UUIDs, timestamps, temporary paths, tokens and timings so the output
        # is suitable for publication and exact comparison across repeated runs.
        return {
            "schema_version": 1,
            "synthetic": True,
            "scenario": scenario["id"],
            "mode": {"provider": "demo", "llm": "demo", "network_required": False},
            "goal": scenario["goal"],
            "exploration": {
                "status": job["status"],
                "queries": job["result"]["queries"],
                "indexed_video_ids": job["result"]["video_ids"],
                "event_kinds": [event["kind"] for event in job["events"]],
                "model_http_calls": job["result"]["calls"],
            },
            "clarification": {
                "user_message": scenario["clarification"]["message"],
                "assistant_reply": answer["reply"],
                "intent": answer["intent"],
                "cited_video_ids": [video["id"] for video in answer["citations"]],
            },
            "recommendations_before_feedback": public_recommendations(before),
            "feedback": scenario["feedback"],
            "recommendations_after_feedback": public_recommendations(after),
            "learning_goal": scenario["learning_goal"],
            "learning_recommendations": public_recommendations(for_learning),
            "free_feedback": {"video_id": None, **scenario["free_feedback"]},
            "checks": checks,
            "limitations": "确定性合成示例仅验证流程和证据边界，不衡量真实推荐质量或用户满意度。",
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="可选 JSON 输出文件，例如 data/example-result.json")
    args = parser.parse_args()
    report = asyncio.run(run_scenario())
    content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
    print(content, end="")


if __name__ == "__main__":
    main()
