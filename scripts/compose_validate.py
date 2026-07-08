"""Validate docker compose overlays locally or in CI."""

from __future__ import annotations

import argparse
import re
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


def _compose_files(project_root: Path) -> list[Path]:
    return [
        project_root / "docker-compose.yml",
        project_root / "docker-compose.dev.yml",
        project_root / "docker-compose.test.yml",
        project_root / "docker-compose.paper.yml",
        project_root / "docker-compose.live.yml",
    ]


def _runtime_env_example_references(project_root: Path) -> list[str]:
    offenders: list[str] = []
    for compose_file in _compose_files(project_root):
        if not compose_file.exists():
            continue
        text = compose_file.read_text(encoding="utf-8")
        if ".env.example" in text:
            offenders.append(str(compose_file.relative_to(project_root)))
    return offenders


def _service_environment_value(compose_file: Path, service_name: str, key: str) -> str | None:
    active_service: str | None = None
    in_environment = False
    for raw_line in compose_file.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        service_match = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", raw_line)
        if service_match:
            active_service = service_match.group(1)
            in_environment = False
            continue
        if active_service != service_name:
            continue
        if re.match(r"^    environment:\s*$", raw_line):
            in_environment = True
            continue
        if in_environment and re.match(r"^    [A-Za-z0-9_-]+:", raw_line):
            in_environment = False
        if not in_environment:
            continue
        setting_match = re.match(rf"^      {re.escape(key)}:\s*(.+?)\s*$", raw_line)
        if setting_match:
            return setting_match.group(1).strip().strip('"').strip("'")
    return None


def _service_exists(compose_file: Path, service_name: str) -> bool:
    service_pattern = re.compile(rf"^  {re.escape(service_name)}:\s*$")
    return any(service_pattern.match(line) for line in compose_file.read_text(encoding="utf-8").splitlines())


def _scheduler_mode_overlay_errors(project_root: Path) -> list[str]:
    offenders: list[str] = []
    for file_name in ("docker-compose.paper.yml", "docker-compose.live.yml"):
        compose_file = project_root / file_name
        if not compose_file.exists():
            continue
        for service_name in ("api", "celery_worker", "celery_beat"):
            if not _service_exists(compose_file, service_name):
                continue
            mode = _service_environment_value(compose_file, service_name, "RUNTIME_SCHEDULER_MODE")
            if mode != "celery":
                offenders.append(f"{file_name}:{service_name}")
    return offenders


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

    env_example_references = _runtime_env_example_references(project_root)
    if env_example_references:
        return ComposeValidationResult(
            exit_code=1,
            status="invalid_env_file",
            message=(
                "runtime compose env_file must use .env, not .env.example: " + ", ".join(sorted(env_example_references))
            ),
        )

    scheduler_mode_errors = _scheduler_mode_overlay_errors(project_root)
    if scheduler_mode_errors:
        return ComposeValidationResult(
            exit_code=1,
            status="invalid_scheduler_mode",
            message=(
                "paper/live compose services must set RUNTIME_SCHEDULER_MODE=celery to avoid duplicate schedulers: "
                + ", ".join(sorted(scheduler_mode_errors))
            ),
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
