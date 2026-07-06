from __future__ import annotations

from pathlib import Path

from scripts.compose_validate import build_compose_commands, validate_compose


def _project_tree(root: Path) -> None:
    for name in (
        "docker-compose.yml",
        "docker-compose.dev.yml",
        "docker-compose.test.yml",
        "docker-compose.paper.yml",
        "docker-compose.live.yml",
    ):
        (root / name).write_text("services: {}\n", encoding="utf-8")


def test_build_compose_commands_covers_all_environments(tmp_path) -> None:
    _project_tree(tmp_path)

    commands = build_compose_commands(tmp_path)

    assert set(commands) == {"dev", "test", "paper", "live"}
    assert commands["dev"][:3] == ["docker", "compose", "-f"]
    assert any(part.endswith("docker-compose.paper.yml") for part in commands["paper"])


def test_validate_compose_skips_when_docker_missing(tmp_path) -> None:
    _project_tree(tmp_path)

    result = validate_compose(
        project_root=tmp_path,
        require_docker=False,
        docker_available=lambda: False,
        runner=lambda _: None,
    )

    assert result.exit_code == 0
    assert result.status == "skipped"
    assert "docker" in result.message.lower()


def test_validate_compose_requires_docker_when_requested(tmp_path) -> None:
    _project_tree(tmp_path)

    result = validate_compose(
        project_root=tmp_path,
        require_docker=True,
        docker_available=lambda: False,
        runner=lambda _: None,
    )

    assert result.exit_code == 2
    assert result.status == "blocked"


def test_validate_compose_rejects_env_example_as_runtime_env_file(tmp_path) -> None:
    _project_tree(tmp_path)
    (tmp_path / "docker-compose.yml").write_text(
        """
services:
  api:
    image: python:3.11-slim
    env_file:
      - .env.example
""",
        encoding="utf-8",
    )

    result = validate_compose(
        project_root=tmp_path,
        require_docker=False,
        docker_available=lambda: False,
        runner=lambda _: None,
    )

    assert result.exit_code == 1
    assert result.status == "invalid_env_file"
    assert ".env.example" in result.message
