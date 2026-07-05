"""Validate docker compose overlays locally or in CI."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ComposeValidationResult:
    exit_code: int
    status: str
    message: str


def build_compose_commands(project_root: Path) -> dict[str, list[str]]:
    compose_files = {
        "dev": ["docker-compose.yml", "docker-compose.dev.yml"],
        "test": ["docker-compose.yml", "docker-compose.test.yml"],
        "paper": ["docker-compose.yml", "docker-compose.paper.yml"],
        "live": ["docker-compose.yml", "docker-compose.live.yml"],
    }
    commands: dict[str, list[str]] = {}
    for env_name, files in compose_files.items():
        command = ["docker", "compose"]
        for file_name in files:
            command.extend(["-f", str(project_root / file_name)])
        command.append("config")
        commands[env_name] = command
    return commands


def validate_compose(
    *,
    project_root: Path,
    require_docker: bool,
    docker_available: Callable[[], bool] | None = None,
    runner: Callable[[list[str]], None] | None = None,
) -> ComposeValidationResult:
    available = docker_available or (lambda: shutil.which("docker") is not None)
    execute = runner or (lambda command: subprocess.run(command, check=True, cwd=project_root))
    commands = build_compose_commands(project_root)

    missing_files = [
        path for command in commands.values() for path in command if path.endswith(".yml") and not Path(path).exists()
    ]
    if missing_files:
        return ComposeValidationResult(
            exit_code=1,
            status="missing_files",
            message=f"missing compose files: {', '.join(sorted(set(missing_files)))}",
        )

    if not available():
        return ComposeValidationResult(
            exit_code=2 if require_docker else 0,
            status="blocked" if require_docker else "skipped",
            message="docker not found on PATH; compose runtime validation skipped",
        )

    for _env_name, command in commands.items():
        execute(command)
    return ComposeValidationResult(
        exit_code=0,
        status="ok",
        message=f"validated compose overlays: {', '.join(commands.keys())}",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate docker-compose overlays.")
    parser.add_argument("--require-docker", action="store_true", help="fail if docker is unavailable")
    args = parser.parse_args()

    result = validate_compose(project_root=Path(__file__).resolve().parents[1], require_docker=args.require_docker)
    print(f"[{result.status}] {result.message}")
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
