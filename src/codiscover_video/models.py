from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Segment(StrictModel):
    start: float = Field(default=0, ge=0, allow_inf_nan=False)
    end: float = Field(default=0, ge=0, allow_inf_nan=False)
    text: str = Field(max_length=20000)
    source: str = "subtitle"

    @model_validator(mode="after")
    def ordered_timestamps(self) -> Self:
        if self.end < self.start:
            raise ValueError("片段结束时间不能早于开始时间")
        return self


class Video(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=1000)
    url: str = ""
    author: str = ""
    duration: int = Field(default=0, ge=0)
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    segments: list[Segment] = Field(default_factory=list)
    summary: str = ""
    traits: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0, ge=0, le=1)
    source: str = "demo"
    content_status: str = "metadata_only"
    image_urls: list[str] = Field(default_factory=list)


class Analysis(StrictModel):
    summary: str = Field(max_length=3000)
    traits: list[str] = Field(default_factory=list, max_length=12)
    confidence: float = Field(ge=0, le=1)
    evidence_quotes: list[str] = Field(default_factory=list, max_length=8)


class Intent(StrictModel):
    goal: str = Field(max_length=3000)
    context: str = Field(default="", max_length=1000)
    liked_tags: list[str] = Field(default_factory=list, max_length=20)
    disliked_tags: list[str] = Field(default_factory=list, max_length=20)
    hypotheses: list[str] = Field(default_factory=list, max_length=10)
    question: str = Field(default="", max_length=1000)


class ChatAnswer(StrictModel):
    reply: str = Field(min_length=1, max_length=6000)
    intent: Intent
    cited_video_ids: list[str] = Field(default_factory=list, max_length=12)


class GoalCreate(StrictModel):
    text: str = Field(min_length=1, max_length=3000)
    context: str = Field(default="", max_length=1000)
    interval_minutes: int = Field(default=0, ge=0, le=10080)

    @field_validator("text")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("目标不能为空")
        return value.strip()

    @field_validator("interval_minutes")
    @classmethod
    def minimum_interval(cls, value: int) -> int:
        if 0 < value < 15:
            raise ValueError("自动探索间隔至少 15 分钟，0 表示关闭")
        return value


class GoalPatch(StrictModel):
    text: str | None = Field(default=None, min_length=1, max_length=3000)
    context: str | None = Field(default=None, max_length=1000)
    intent: Intent | None = None
    interval_minutes: int | None = Field(default=None, ge=0, le=10080)
    active: bool | None = None


class ChatRequest(StrictModel):
    message: str = Field(min_length=1, max_length=4000)
    goal_id: str
    session_id: str | None = None


class FeedbackCreate(StrictModel):
    goal_id: str
    video_id: str | None = None
    rating: int = Field(ge=1, le=5)
    note: str = Field(default="", max_length=3000)
    context: str = Field(default="", max_length=1000)


class RecommendationRequest(StrictModel):
    goal_id: str
    query: str = Field(default="", max_length=3000)
    limit: int = Field(default=6, ge=1, le=12)


class JobCreate(StrictModel):
    goal_id: str
    max_videos: int = Field(default=4, ge=1, le=12)


class VideoImport(StrictModel):
    video_id: str = Field(min_length=1, max_length=300)


class Presence(StrictModel):
    online: bool


JobStatus = Literal["queued", "running", "paused", "completed", "failed", "cancelled"]
