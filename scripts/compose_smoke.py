"""Runtime docker compose smoke test (up + health checks + teardown)."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ComposeSmokeResult:
    exit_code: int
    status: str
    message: str


def _compose_command(project_root: Path, *args: str) -> list[str]:
    command = [
        "docker",
        "compose",
        "-f",
        str(project_root / "docker-compose.yml"),
        "-f",
        str(project_root / "docker-compose.test.yml"),
    ]
    command.extend(args)
    return command


def _run(command: list[str], *, project_root: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=project_root, check=check, capture_output=True, text=True)


def _ensure_env(project_root: Path) -> None:
    env_path = project_root / ".env"
    if not env_path.exists():
        shutil.copyfile(project_root / ".env.example", env_path)


def _admin_token(project_root: Path) -> str:
    env_path = project_root / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("ADMIN_API_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "dev-admin-token"


def _wait_for_http(url: str, *, timeout_seconds: float = 120.0) -> None:
    deadline = time.time() + timeout_seconds
    last_error = "unknown"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status == 200:
                    return
        except urllib.error.URLError as exc:
            last_error = str(exc)
        time.sleep(3)
    raise TimeoutError(f"timed out waiting for {url}: {last_error}")


def run_compose_smoke(*, project_root: Path, keep_running: bool = False) -> ComposeSmokeResult:
    if shutil.which("docker") is None:
        return ComposeSmokeResult(
            exit_code=2,
            status="blocked",
            message="docker not found on PATH; compose smoke skipped",
        )

    _ensure_env(project_root)
    services = ["timescaledb", "redis", "api"]
    logs: list[str] = []

    try:
        up = _run(_compose_command(project_root, "up", "-d", "--wait", *services), project_root=project_root)
        logs.append(up.stdout)
        logs.append(up.stderr)

        _wait_for_http("http://127.0.0.1:8000/health")
        health = json.loads(urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=5).read().decode("utf-8"))
        if health.get("status") != "ok":
            return ComposeSmokeResult(
                exit_code=1,
                status="health_failed",
                message=f"/health unexpected payload: {health}",
            )

        deps = json.loads(
            urllib.request.urlopen(
                urllib.request.Request(
                    "http://127.0.0.1:8000/api/v1/system/health/dependencies",
                    headers={"Authorization": f"Bearer {_admin_token(project_root)}"},
                ),
                timeout=10,
            )
            .read()
            .decode("utf-8")
        )
        database = deps.get("dependencies", {}).get("database", {})
        if database.get("status") != "ok":
            return ComposeSmokeResult(
                exit_code=1,
                status="dependencies_failed",
                message=f"database check failed: {database}",
            )

        return ComposeSmokeResult(exit_code=0, status="ok", message="compose smoke passed for timescaledb/redis/api")
    except subprocess.CalledProcessError as exc:
        return ComposeSmokeResult(
            exit_code=1,
            status="compose_failed",
            message=f"compose command failed: {exc.stderr or exc.stdout}",
        )
    except TimeoutError as exc:
        return ComposeSmokeResult(exit_code=1, status="timeout", message=str(exc))
    finally:
        if not keep_running:
            with subprocess.Popen(
                _compose_command(project_root, "down", "-v"),
                cwd=project_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ) as proc:
                stdout, stderr = proc.communicate()
                logs.extend([stdout, stderr])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--keep-running", action="store_true")
    args = parser.parse_args()
    result = run_compose_smoke(project_root=args.project_root, keep_running=args.keep_running)
    print(result.message)
    raise SystemExit(result.exit_code)


if __name__ == "__main__":
    main()
