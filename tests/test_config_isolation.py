from codiscover_video.config import Settings


def clear_config(monkeypatch):
    for key in (
        "CDV_PROVIDER",
        "CDV_LLM",
        "CDV_DATA_DIR",
        "CDV_SCHEDULER",
        "CDV_MAX_DAILY_JOBS",
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_API_BASE",
        "BILI_CREDENTIAL_PATH",
    ):
        monkeypatch.delenv(key, raising=False)


def test_does_not_inherit_parent_dotenv(tmp_path, monkeypatch):
    clear_config(monkeypatch)
    (tmp_path / ".env").write_text("CDV_LLM=deepseek\nDEEPSEEK_API_KEY=parent-test-placeholder\n")
    child = tmp_path / "release"
    child.mkdir()
    monkeypatch.chdir(child)
    settings = Settings.from_env()
    assert settings.llm_mode == "demo"
    assert settings.api_key.get_secret_value() == ""


def test_explicit_working_directory_dotenv_and_env_priority(tmp_path, monkeypatch):
    clear_config(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "CDV_LLM=deepseek\nCDV_PROVIDER=bilibili\nDEEPSEEK_API_KEY=local-test-placeholder\n"
    )
    monkeypatch.setenv("CDV_LLM", "demo")
    settings = Settings.from_env()
    assert settings.llm_mode == "demo"
    assert settings.provider_mode == "bilibili"
    assert settings.api_key.get_secret_value() == "local-test-placeholder"
