"""Local RAG baseline: FTS5 BM25 + stable hashed token vectors, then diversity reranking.

Hash vectors are lexical, NOT a pretrained semantic embedding model. This small, reproducible
baseline intentionally avoids hidden downloads and leaves the encoder replaceable for phase 2.
"""

import hashlib
import json
import math
import re
from collections import Counter

from .concepts import preference_matches


def tokenize(text):
    text = text.lower()
    words = re.findall(r"[a-z0-9_]+", text)
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        words.extend(run)
        words.extend(run[i : i + 2] for i in range(len(run) - 1))
    return words


# Query boilerplate must not turn shared single Chinese characters into evidence.
_QUERY_STOPWORDS = {
    "我想",
    "想看",
    "看看",
    "推荐",
    "视频",
    "内容",
    "适合",
    "观看",
    "一些",
    "一个",
    "什么",
    "希望",
    "帮我",
    "可以",
    "有没有",
    "相关",
    "the",
    "a",
    "an",
    "is",
    "of",
    "to",
    "and",
}
_SINGLE_STOPWORDS = set("我的了是和在有想看些个吗什么能要与或")


def meaningful_query_tokens(text):
    tokens = re.findall(r"[a-z0-9_]+", text.lower())
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        if len(run) == 1:
            if run not in _SINGLE_STOPWORDS:
                tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return set(tokens) - _QUERY_STOPWORDS


def embed(text, dimensions=256):
    vector = [0.0] * dimensions
    for token, count in Counter(tokenize(text)).items():
        digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        vector[index] += (1 + math.log(count)) * (1 if digest[4] & 1 else -1)
    norm = math.sqrt(sum(v * v for v in vector)) or 1
    return [round(v / norm, 6) for v in vector]


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b))


def chunks_for(video):
    metadata = " ".join(
        [video.title, video.author, video.description, *video.tags, video.summary, *video.traits]
    )
    yield {"text": metadata[:8000], "start": 0, "end": 0, "source": "metadata"}
    # Preserve time and source boundaries; overlap one subtitle segment between windows.
    buffer = []
    size = 0
    for segment in video.segments[:3000]:
        buffer.append(segment)
        size += len(segment.text)
        if size >= 500:
            yield {
                "text": " ".join(s.text for s in buffer),
                "start": buffer[0].start,
                "end": buffer[-1].end,
                "source": buffer[0].source,
            }
            buffer, size = [buffer[-1]], len(buffer[-1].text)
    if buffer:
        yield {
            "text": " ".join(s.text for s in buffer),
            "start": buffer[0].start,
            "end": buffer[-1].end,
            "source": buffer[0].source,
        }


def retrieve(store, query, limit=30):
    with store.connection() as db:
        tokens = list(dict.fromkeys(tokenize(query)))[:80]
        lexical = []
        if tokens:
            expression = " OR ".join('"' + t.replace('"', '""') + '"' for t in tokens)
            lexical = db.execute(
                "SELECT id,bm25(chunks_fts) AS rank FROM chunks_fts "
                "WHERE chunks_fts MATCH ? ORDER BY rank LIMIT 150",
                (expression,),
            ).fetchall()
        # Phase 1 bounded local corpus; phase 2 swaps this exact scan for a vector index.
        chunks = db.execute("SELECT * FROM chunks").fetchall()
    terms = meaningful_query_tokens(query)
    # This encoder is lexical hashing, not semantic embedding: collisions alone
    # cannot establish relevance. Require a real word/bigram shared with query.
    chunks = [row for row in chunks if terms.intersection(tokenize(row["text"]))]
    eligible_ids = {row["id"] for row in chunks}
    lexical = [row for row in lexical if row["id"] in eligible_ids]
    qvector = embed(query)
    dense = sorted(
        ((r["id"], cosine(qvector, json.loads(r["vector"]))) for r in chunks),
        key=lambda x: x[1],
        reverse=True,
    )[:150]
    scores = {}
    for rank, row in enumerate(lexical):
        scores[row["id"]] = scores.get(row["id"], 0) + 1 / (40 + rank)
    for rank, (cid, similarity) in enumerate(dense):
        if similarity > 0:
            scores[cid] = scores.get(cid, 0) + 1 / (40 + rank)
    ranked = [{**dict(r), "score": scores.get(r["id"], 0)} for r in chunks if scores.get(r["id"], 0) > 0]
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked[:limit]


def recommend(store, goal, query="", limit=6):
    intent = goal.get("intent", {})
    positive = intent.get("liked_tags", [])
    negative = intent.get("disliked_tags", [])
    query = " ".join([query or intent.get("goal", goal["text"]), goal.get("context", ""), *positive])
    hits = retrieve(store, query, limit=200)
    by_video = {}
    for hit in hits:
        by_video.setdefault(hit["video_id"], []).append(hit)
    feedback = store.feedback(goal["id"])
    latest = {}
    for item in feedback:
        if item["video_id"]:
            latest.setdefault(item["video_id"], item["rating"])
    candidates = []
    for video in store.videos():
        # A rating of 1/2 is an explicit rejection within this goal, not a global blacklist.
        if latest.get(video["id"], 3) <= 2:
            continue
        text = " ".join(
            [video["title"], video["description"], video["summary"], *video["traits"], *video["tags"]]
        )
        if any(preference_matches(tag, text) for tag in negative if tag.strip()):
            continue
        vhits = by_video.get(video["id"], [])
        relevance = min(1, max((h["score"] for h in vhits), default=0) * 20)
        # Do not fill results with unrelated items when there is no matching evidence.
        if relevance <= 0:
            continue
        preference = sum(preference_matches(tag, text) for tag in positive if tag.strip())
        score = 0.67 * relevance + 0.18 * video["confidence"] + min(0.1, 0.03 * preference)
        score += 0.025 * (latest.get(video["id"], 3) - 3)
        evidence = [
            {k: h[k] for k in ("text", "start", "end", "source")} for h in vhits if h["source"] != "metadata"
        ][:2]
        if not evidence:
            evidence = [
                {"text": video["description"] or video["title"], "start": 0, "end": 0, "source": "metadata"}
            ]
        reason = (
            "字幕片段与当前体验目标匹配"
            if any(e["source"] == "subtitle" for e in evidence)
            else "依据本地内容证据匹配；请通过观看反馈验证体验"
        )
        if video["content_status"] == "metadata_only":
            reason = "仅有标题/简介，尚未理解视频正文；适合作为待验证候选"
        if latest.get(video["id"], 3) >= 4:
            reason += "；你曾给出正向反馈"
        candidates.append(
            {"video": video, "score": score, "reason": reason, "evidence": evidence, "_vector": embed(text)}
        )
    selected = []
    while candidates and len(selected) < limit:

        def mmr(candidate):
            similarity = max((cosine(candidate["_vector"], x["_vector"]) for x in selected), default=0)
            author_repeat = any(x["video"]["author"] == candidate["video"]["author"] for x in selected)
            return candidate["score"] - 0.15 * similarity - 0.08 * author_repeat

        best = max(candidates, key=mmr)
        candidates.remove(best)
        selected.append(best)
    return [
        {k: round(v, 4) if k == "score" else v for k, v in item.items() if not k.startswith("_")}
        for item in selected
    ]
