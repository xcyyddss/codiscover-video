import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr


class Settings(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    data_dir: Path = Path("data")
    provider_mode: Literal["demo", "bilibili"] = "demo"
    llm_mode: Literal["demo", "deepseek"] = "demo"
    api_key: SecretStr = SecretStr("")
    api_base: str = "https://api.deepseek.com"
    model: Literal["deepseek-flash"] = "deepseek-flash"
    request_timeout: float = Field(default=60, ge=1, le=300)
    llm_max_tokens: int = Field(default=4096, ge=512, le=16384)
    credential_path: Path = Path("data/bili_credential.json")
    scheduler_enabled: bool = True
    worker_poll_seconds: float = 1
    max_daily_jobs: int = Field(default=12, ge=1, le=100)
    max_llm_concurrency: int = Field(default=2, ge=1, le=8)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "codiscover_video.sqlite3"

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)
        data = Path(os.getenv("CDV_DATA_DIR", "data")).expanduser().resolve()
        return cls(
            data_dir=data,
            credential_path=Path(os.getenv("BILI_CREDENTIAL_PATH", str(data / "bili_credential.json"))),
            provider_mode=os.getenv("CDV_PROVIDER", "demo"),
            llm_mode=os.getenv("CDV_LLM", "demo"),
            api_key=SecretStr(os.getenv("DEEPSEEK_API_KEY", "")),
            api_base=os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com"),
            scheduler_enabled=os.getenv("CDV_SCHEDULER", "true").lower() == "true",
            max_daily_jobs=int(os.getenv("CDV_MAX_DAILY_JOBS", "12")),
        )
