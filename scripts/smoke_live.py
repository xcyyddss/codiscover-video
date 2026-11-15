"""Opt-in billable DeepSeek smoke; key only comes from local env/.env, never printed."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codiscover_video.config import Settings
from codiscover_video.intelligence import Intelligence
from codiscover_video.providers.demo import DemoProvider
from codiscover_video.store import now


async def run(output):
    settings = Settings.from_env().model_copy(update={"llm_mode": "deepseek"})
    model = Intelligence(settings)
    provider = DemoProvider()
    goal = {
        "id": "smoke",
        "text": "下班后想安静放松，不希望高刺激和学习压力",
        "context": "晚上20分钟",
        "intent": {"goal": "下班后安静放松", "liked_tags": ["自然"], "disliked_tags": ["高刺激"]},
    }
    report = {
        "tested_at": now(),
        "model": settings.model,
        "provider": "demo fixtures + real DeepSeek",
        "checks": {},
    }
    try:
        with model.usage_scope("live-smoke", max_calls=6) as scope:
            queries = await model.plan(goal, {"intent": goal["intent"], "feedback": []})
            report["checks"]["plan"] = {"passed": bool(queries), "queries": queries}
            video = (await provider.search("自然", 1))[0]
            video = await provider.inspect(video.id)
            analysis = await model.analyze(video)
            report["checks"]["analyze"] = {
                "passed": bool(analysis.summary),
                "video_id": video.id,
                "analysis": analysis.model_dump(),
            }
            evidence = [video.model_dump()]
            answer = await model.chat(
                "更喜欢自然声音，不想看游戏，请推荐一个并解释依据",
                goal,
                {"intent": goal["intent"], "feedback": []},
                [],
                evidence,
            )
            report["checks"]["chat"] = {"passed": bool(answer.reply), "answer": answer.model_dump()}
            report["calls"] = scope.calls
            report["usage"] = scope.records
    except Exception as exc:
        report["error"] = {"type": type(exc).__name__, "code": getattr(exc, "code", "unknown")}
    report["passed"] = len(report["checks"]) == 3 and all(c["passed"] for c in report["checks"].values())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "checks": list(report["checks"]),
                "error": report.get("error"),
                "report": str(output),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("data/live-smoke.json"))
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.output)))
