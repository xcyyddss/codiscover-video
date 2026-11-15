"""Opt-in live subtitle acceptance. Local test feedback is labelled as test data."""

import argparse
import json
import math
import time
from pathlib import Path

import httpx

from codiscover_video.store import now


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def run(video_id, output, base_url="http://127.0.0.1:8765"):
    report = {"tested_at": now(), "source": "authenticated Bilibili subtitles + real DeepSeek", "checks": {}}
    try:
        with httpx.Client(base_url=base_url, timeout=180) as client:
            spec = client.get("/openapi.json")
            spec.raise_for_status()
            check(
                spec.json().get("info", {}).get("title") == "CoDiscover Video",
                "wrong application; refusing live test",
            )
            status = client.get("/api/status")
            status.raise_for_status()
            check(
                status.json()["mode"] == "bilibili" and status.json()["llm_mode"] == "deepseek",
                "live mode required",
            )
            started = time.monotonic()
            response = client.post("/api/videos/import", json={"video_id": video_id})
            response.raise_for_status()
            video = response.json()
            segments = video["segments"]
            check(
                video["content_status"] == "subtitle", "content was not subtitle; fallback is not acceptance"
            )
            check(bool(segments) and not video["image_urls"], "missing subtitles or unexpected frames")
            check(
                all(
                    s["source"] == "subtitle"
                    and s["text"].strip()
                    and math.isfinite(s["start"])
                    and math.isfinite(s["end"])
                    and 0 <= s["start"] <= s["end"]
                    for s in segments
                ),
                "invalid source/text/timestamps",
            )
            check(any(s["start"] > 0 for s in segments), "all timestamps were zero")
            check(
                all(a["start"] <= b["start"] for a, b in zip(segments, segments[1:])), "unordered timestamps"
            )
            check(bool(video["summary"]) and 0 < video["confidence"] <= 0.85, "invalid analysis")
            report["checks"]["subtitle_import"] = {
                "passed": True,
                "status": response.status_code,
                "video_id": video_id,
                "title": video["title"],
                "segment_count": len(segments),
                "characters": sum(len(s["text"]) for s in segments),
                "first_start": segments[0]["start"],
                "last_end": segments[-1]["end"],
                "confidence": video["confidence"],
                "seconds": round(time.monotonic() - started, 2),
            }
            goal_response = client.post(
                "/api/goals",
                json={
                    "text": f"字幕验收：理解 {video['title']}",
                    "context": "开发验收专用目标，测试评分不代表用户真实偏好",
                },
            )
            goal_response.raise_for_status()
            goal = goal_response.json()
            report["goal_id"] = goal["id"]
            anchor = max(segments, key=lambda s: len(s["text"]))
            query = anchor["text"][:100]
            response = client.post(
                "/api/recommendations", json={"goal_id": goal["id"], "query": query, "limit": 6}
            )
            response.raise_for_status()
            target = next((r for r in response.json() if r["video"]["id"] == video_id), None)
            check(target is not None, "target video not retrieved")
            evidence = [e for e in target["evidence"] if e["source"] == "subtitle"]
            check(bool(evidence), "recommendation has no subtitle evidence")
            transcript = " ".join(s["text"] for s in segments)
            check(all(e["text"] in transcript for e in evidence), "evidence not an exact transcript span")
            report["checks"]["retrieval"] = {
                "passed": True,
                "target_rank": next(
                    i + 1 for i, r in enumerate(response.json()) if r["video"]["id"] == video_id
                ),
                "subtitle_evidence_count": len(evidence),
                "exact_text_match": True,
            }
            message = f"请根据 {video_id} 的字幕解释这段内容：{query}。请引用该视频，并区分字幕能确认和不能确认的信息。"
            response = client.post("/api/chat", json={"goal_id": goal["id"], "message": message})
            response.raise_for_status()
            answer = response.json()
            check(bool(answer["reply"].strip()), "empty answer")
            check(video_id in [v["id"] for v in answer["citations"]], "target citation missing")
            messages = client.get("/api/messages", params={"session_id": answer["session_id"]}).json()
            check([m["role"] for m in messages] == ["user", "assistant"], "conversation not persisted")
            report["checks"]["chat"] = {
                "passed": True,
                "session_id": answer["session_id"],
                "cited_video_ids": [v["id"] for v in answer["citations"]],
                "reply": answer["reply"],
                "messages_persisted": len(messages),
            }
            note = "自动化字幕验收：测试负反馈排除机制；该评分不代表用户真实偏好。"
            response = client.post(
                "/api/feedback",
                json={
                    "goal_id": goal["id"],
                    "video_id": video_id,
                    "rating": 1,
                    "note": note,
                    "context": "仅用于开发验收",
                },
            )
            response.raise_for_status()
            feedback_id = response.json()["id"]
            saved = client.get("/api/feedback", params={"goal_id": goal["id"]}).json()
            check(
                any(
                    f["id"] == feedback_id
                    and f["video_id"] == video_id
                    and f["rating"] == 1
                    and f["note"] == note
                    for f in saved
                ),
                "feedback persistence mismatch",
            )
            after = client.post("/api/recommendations", json={"goal_id": goal["id"], "query": query}).json()
            check(
                video_id not in [r["video"]["id"] for r in after], "negative feedback did not exclude video"
            )
            report["checks"]["feedback"] = {
                "passed": True,
                "feedback_id": feedback_id,
                "excluded_after_rating_one": True,
            }
            exported = client.get("/api/export").json()
            check(
                any(v["id"] == video_id and len(v["segments"]) == len(segments) for v in exported["videos"]),
                "subtitle export mismatch",
            )
            check(any(f["id"] == feedback_id for f in exported["feedback"]), "feedback export mismatch")
            report["checks"]["export"] = {"passed": True, "subtitle_and_feedback_retained": True}
    except (httpx.HTTPError, AssertionError, KeyError, StopIteration, ValueError) as exc:
        report["error"] = {
            "type": type(exc).__name__,
            "message": str(exc)[:300]
            if isinstance(exc, AssertionError)
            else "Request or response validation failed; inspect application status.",
        }
    report["passed"] = len(report["checks"]) == 5 and all(c["passed"] for c in report["checks"].values())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "checks": list(report["checks"]),
                "error": report.get("error"),
                "output": str(output),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--video-id", required=True, help="Public video BV id you choose to test")
    parser.add_argument("--output", type=Path, default=Path("data/subtitle-e2e.json"))
    args = parser.parse_args()
    raise SystemExit(run(args.video_id, args.output, args.base_url))
