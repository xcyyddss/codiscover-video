"""SQLite unit-of-work boundaries; no connection is shared across threads."""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .models import Video


def now() -> str:
    return datetime.now(UTC).isoformat()


def uid() -> str:
    return uuid.uuid4().hex


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL);
            INSERT INTO schema_version SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_version);
            CREATE TABLE IF NOT EXISTS goals(id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS videos(id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY, video_id TEXT NOT NULL REFERENCES videos(id)
              ON DELETE CASCADE, text TEXT NOT NULL, start REAL, end REAL, source TEXT, vector TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS chunks_video ON chunks(video_id);
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(id UNINDEXED, tokens);
            CREATE TABLE IF NOT EXISTS feedback(id TEXT PRIMARY KEY, goal_id TEXT NOT NULL REFERENCES goals(id)
              ON DELETE CASCADE, video_id TEXT REFERENCES videos(id) ON DELETE SET NULL,
              rating INTEGER CHECK(rating BETWEEN 1 AND 5), note TEXT, context TEXT, created_at TEXT);
            CREATE INDEX IF NOT EXISTS feedback_goal ON feedback(goal_id,created_at);
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, goal_id TEXT NOT NULL REFERENCES goals(id)
              ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL
              REFERENCES sessions(id) ON DELETE CASCADE, role TEXT, content TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, goal_id TEXT NOT NULL REFERENCES goals(id)
              ON DELETE CASCADE, status TEXT NOT NULL, max_videos INTEGER, created_at TEXT, updated_at TEXT,
              result TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '');
            CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status,created_at);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL
              REFERENCES jobs(id) ON DELETE CASCADE, kind TEXT, detail TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
        try:
            path.chmod(0o600)
        except OSError:
            pass

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=15000")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def goals(self):
        with self.connection() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM goals ORDER BY created_at")]

    def goal(self, goal_id):
        with self.connection() as db:
            row = db.execute("SELECT payload FROM goals WHERE id=?", (goal_id,)).fetchone()
        if not row:
            raise KeyError("目标不存在")
        return json.loads(row[0])

    def save_goal(self, goal):
        with self.connection() as db:
            db.execute(
                "INSERT INTO goals VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (goal["id"], encode(goal), goal["created_at"]),
            )
        return goal

    def videos(self):
        with self.connection() as db:
            return [
                json.loads(r[0]) for r in db.execute("SELECT payload FROM videos ORDER BY updated_at DESC")
            ]

    def video(self, video_id):
        with self.connection() as db:
            row = db.execute("SELECT payload FROM videos WHERE id=?", (video_id,)).fetchone()
        if not row:
            raise KeyError("视频尚未入库，请先导入")
        return json.loads(row[0])

    def save_video(self, video: Video):
        from .retrieval import chunks_for, embed, tokenize

        payload = video.model_dump()
        with self.connection() as db:
            db.execute(
                "INSERT INTO videos VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "payload=excluded.payload,updated_at=excluded.updated_at",
                (video.id, encode(payload), now()),
            )
            db.execute(
                "DELETE FROM chunks_fts WHERE id IN (SELECT id FROM chunks WHERE video_id=?)", (video.id,)
            )
            db.execute("DELETE FROM chunks WHERE video_id=?", (video.id,))
            for i, chunk in enumerate(chunks_for(video)):
                cid = f"{video.id}:{i}"
                db.execute(
                    "INSERT INTO chunks VALUES (?,?,?,?,?,?,?)",
                    (
                        cid,
                        video.id,
                        chunk["text"],
                        chunk["start"],
                        chunk["end"],
                        chunk["source"],
                        encode(embed(chunk["text"])),
                    ),
                )
                db.execute("INSERT INTO chunks_fts VALUES (?,?)", (cid, " ".join(tokenize(chunk["text"]))))
        return payload

    def feedback(self, goal_id=None):
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM feedback"
                + (" WHERE goal_id=?" if goal_id else "")
                + " ORDER BY created_at DESC LIMIT 500",
                (goal_id,) if goal_id else (),
            ).fetchall()
        return [dict(r) for r in rows]

    def add_feedback(self, payload):
        item = {"id": uid(), **payload, "created_at": now()}
        with self.connection() as db:
            db.execute(
                "INSERT INTO feedback VALUES (:id,:goal_id,:video_id,:rating,:note,:context,:created_at)",
                item,
            )
        return item

    def session(self, goal_id, session_id=None):
        with self.connection() as db:
            if session_id:
                row = db.execute("SELECT goal_id FROM sessions WHERE id=?", (session_id,)).fetchone()
                if not row or row[0] != goal_id:
                    raise ValueError("对话不属于此目标，请开始新对话")
                return session_id
            session_id = uid()
            db.execute("INSERT INTO sessions VALUES (?,?)", (session_id, goal_id))
            return session_id

    def messages(self, session_id):
        with self.connection() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM (SELECT * FROM messages WHERE session_id=? "
                    "ORDER BY id DESC LIMIT 40) ORDER BY id",
                    (session_id,),
                )
            ]

    def add_message(self, session_id, role, content):
        with self.connection() as db:
            db.execute(
                "INSERT INTO messages(session_id,role,content,created_at) VALUES (?,?,?,?)",
                (session_id, role, content, now()),
            )

    def create_job(self, goal_id, max_videos):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT id FROM jobs WHERE goal_id=? AND status IN ('queued','running','paused')", (goal_id,)
            ).fetchone()
            if existing:
                job_id = existing[0]
            else:
                job_id = uid()
                db.execute(
                    "INSERT INTO jobs(id,goal_id,status,max_videos,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                    (job_id, goal_id, "queued", max_videos, now(), now()),
                )
        return self.job(job_id)

    def jobs(self):
        with self.connection() as db:
            rows = db.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 100").fetchall()
        return [self._job_row(r) for r in rows]

    @staticmethod
    def _job_row(row):
        result = dict(row)
        result["result"] = json.loads(result["result"])
        return result

    def job(self, job_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise KeyError("任务不存在")
            job = self._job_row(row)
            job["events"] = [
                {**dict(r), "detail": json.loads(r["detail"])}
                for r in db.execute("SELECT * FROM events WHERE job_id=? ORDER BY id", (job_id,))
            ]
        return job

    def update_job(self, job_id, status=None, result=None, error=None):
        with self.connection() as db:
            if status:
                db.execute("UPDATE jobs SET status=?,updated_at=? WHERE id=?", (status, now(), job_id))
            if result is not None:
                db.execute(
                    "UPDATE jobs SET result=?,updated_at=? WHERE id=?", (encode(result), now(), job_id)
                )
            if error is not None:
                db.execute("UPDATE jobs SET error=?,updated_at=? WHERE id=?", (error, now(), job_id))

    def claim(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if not row:
                return None
            db.execute("UPDATE jobs SET status='running',updated_at=? WHERE id=?", (now(), row[0]))
        return self.job(row[0])

    def event(self, job_id, kind, detail):
        with self.connection() as db:
            db.execute(
                "INSERT INTO events(job_id,kind,detail,created_at) VALUES (?,?,?,?)",
                (job_id, kind, encode(detail), now()),
            )

    def recover(self):
        with self.connection() as db:
            db.execute("UPDATE jobs SET status='queued',updated_at=? WHERE status='running'", (now(),))

    def set_state(self, key, value):
        with self.connection() as db:
            db.execute(
                "INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, encode(value)),
            )

    def get_state(self, key, default=None):
        with self.connection() as db:
            r = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def counts(self):
        with self.connection() as db:
            return {
                t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                for t in ("videos", "feedback", "jobs")
            }

    def clear(self):
        with self.connection() as db:
            db.execute("DELETE FROM chunks_fts")
            db.execute("DELETE FROM goals")
            db.execute("DELETE FROM videos")
            db.execute("DELETE FROM state")

    def export(self):
        with self.connection() as db:
            chats = [dict(r) for r in db.execute("SELECT * FROM messages")]
            sessions = [dict(r) for r in db.execute("SELECT * FROM sessions")]
            feedback = [dict(r) for r in db.execute("SELECT * FROM feedback ORDER BY created_at")]
            jobs = [self._job_row(r) for r in db.execute("SELECT * FROM jobs ORDER BY created_at")]
            for job in jobs:
                job["events"] = [
                    {**dict(r), "detail": json.loads(r["detail"])}
                    for r in db.execute("SELECT * FROM events WHERE job_id=? ORDER BY id", (job["id"],))
                ]
        return {
            "schema_version": 1,
            "exported_at": now(),
            "goals": self.goals(),
            "videos": self.videos(),
            "feedback": feedback,
            "sessions": sessions,
            "messages": chats,
            "jobs": jobs,
        }
