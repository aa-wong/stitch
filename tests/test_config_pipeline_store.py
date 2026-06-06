from __future__ import annotations

from stitch.config import init_project, load_config, load_dotenv, parse_env_file
from stitch.json_yaml import write_document
from stitch.paths import StitchPaths
from stitch.pipeline_store import latest_report_path, list_pipelines, load_pipeline


def test_init_project_creates_stitch_files(tmp_path):
    paths = StitchPaths(tmp_path)

    init_project(paths, redis_url="redis://localhost:6379/0", weave_project="demo")
    config = load_config(paths)

    assert paths.config_file.exists()
    assert paths.sources_file.exists()
    assert paths.questions_file.exists()
    assert config.redis_url == "redis://localhost:6379/0"
    assert config.weave_project == "demo"


def test_pipeline_store_lists_and_loads_latest_report(tmp_path):
    paths = StitchPaths(tmp_path)
    pipeline_dir = paths.pipeline_dir("demo")
    pipeline_dir.mkdir(parents=True)
    write_document(pipeline_dir / "pipeline.yaml", {"name": "demo"})
    (pipeline_dir / "pipeline.md").write_text("# Demo\n", encoding="utf-8")
    (pipeline_dir / "latest-report.md").write_text("# Report\n", encoding="utf-8")

    assert list_pipelines(paths) == ["demo"]
    assert load_pipeline(paths, "demo").yaml["name"] == "demo"
    assert latest_report_path(paths) == pipeline_dir / "latest-report.md"


def test_load_dotenv_parses_values_without_overriding_existing_env(tmp_path, monkeypatch):
    paths = StitchPaths(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "# comment",
                "STITCH_REDIS_URL=redis://localhost:6379/1",
                "STITCH_WEAVE_PROJECT='demo-project'",
                "export STITCH_WEAVE_DISABLED=true",
                "OPENAI_API_KEY=sk-test",
                "STITCH_OPENAI_MODEL=gpt-test",
                "STITCH_AGENT_TIMEOUT_SECONDS=42",
                "IGNORED_LINE",
            ]
        ),
        encoding="utf-8",
    )
    environ = {"STITCH_REDIS_URL": "redis://already-set/0"}

    values = load_dotenv(paths, environ=environ)

    assert values == {
        "STITCH_REDIS_URL": "redis://localhost:6379/1",
        "STITCH_WEAVE_PROJECT": "demo-project",
        "STITCH_WEAVE_DISABLED": "true",
        "OPENAI_API_KEY": "sk-test",
        "STITCH_OPENAI_MODEL": "gpt-test",
        "STITCH_AGENT_TIMEOUT_SECONDS": "42",
    }
    assert parse_env_file(tmp_path / "missing.env") == {}
    assert environ["STITCH_REDIS_URL"] == "redis://already-set/0"
    assert environ["STITCH_WEAVE_PROJECT"] == "demo-project"
    monkeypatch.setenv("STITCH_REDIS_URL", environ["STITCH_REDIS_URL"])
    monkeypatch.setenv("STITCH_WEAVE_PROJECT", environ["STITCH_WEAVE_PROJECT"])
    monkeypatch.setenv("STITCH_WEAVE_DISABLED", environ["STITCH_WEAVE_DISABLED"])
    monkeypatch.setenv("OPENAI_API_KEY", environ["OPENAI_API_KEY"])
    monkeypatch.setenv("STITCH_OPENAI_MODEL", environ["STITCH_OPENAI_MODEL"])
    monkeypatch.setenv("STITCH_AGENT_TIMEOUT_SECONDS", environ["STITCH_AGENT_TIMEOUT_SECONDS"])
    assert load_config(paths).redis_url == "redis://already-set/0"
    assert load_config(paths).weave_project == "demo-project"
    assert load_config(paths).openai_api_key == "sk-test"
    assert load_config(paths).openai_model == "gpt-test"
    assert load_config(paths).agent_timeout_seconds == 42
