from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
SMOKE_FILE = REPO_ROOT / "docker-compose.smoke.yml"
MULTI_WORKER_FILE = REPO_ROOT / "docker-compose.multi-worker.yml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
BIND_HOST_ENV = "CAMPUSVOICE_BIND_HOST"
DEFAULT_DATABASE_URL = "sqlite+aiosqlite:////data/campusvoice.db"
EXAMPLE_DATABASE_URL = "sqlite+aiosqlite:///./campusvoice.db"
EXTERNAL_DATABASE_URL = "postgresql://campusvoice@db/campusvoice"
EXPECTED_PORTS = {
    "api": (8000, "8000"),
    "web": (3000, "3000"),
}


class ComposeBindingError(RuntimeError):
    pass


def _load_compose_config(
    bind_host: str | None,
    *,
    env_file: Path | None = None,
    overrides: dict[str, str] | None = None,
    compose_files: tuple[Path, ...] = (COMPOSE_FILE,),
    profiles: tuple[str, ...] = (),
) -> dict[str, Any]:
    docker = shutil.which("docker")
    if docker is None:
        raise ComposeBindingError("docker was not found on PATH")

    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("CAMPUSVOICE_", "NEXT_PUBLIC_", "COMPOSE_"))
    }
    environment["COMPOSE_DISABLE_ENV_FILE"] = "1"
    if bind_host is not None:
        environment[BIND_HOST_ENV] = bind_host
    if overrides:
        environment.update(overrides)

    command = [docker, "compose"]
    if env_file is not None:
        command.extend(["--env-file", str(env_file)])
    for compose_file in compose_files:
        command.extend(["--file", str(compose_file)])
    for profile in profiles:
        command.extend(["--profile", profile])
    command.extend(["config", "--format", "json"])

    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ComposeBindingError(f"docker compose config failed: {detail}")

    try:
        config = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ComposeBindingError(
            "docker compose config did not return valid JSON"
        ) from error
    if not isinstance(config, dict):
        raise ComposeBindingError(
            "docker compose config returned a non-object JSON value"
        )
    return config


def _assert_bindings(config: dict[str, Any], expected_host: str) -> None:
    services = config.get("services")
    if not isinstance(services, dict):
        raise ComposeBindingError(
            "docker compose config is missing the services object"
        )

    for service_name, (target_port, published_port) in EXPECTED_PORTS.items():
        service = services.get(service_name)
        if not isinstance(service, dict):
            raise ComposeBindingError(
                f"docker compose config is missing service {service_name!r}"
            )
        ports = service.get("ports")
        if not isinstance(ports, list):
            raise ComposeBindingError(f"service {service_name!r} has no ports list")

        matching_ports = [
            port
            for port in ports
            if isinstance(port, dict)
            and port.get("target") == target_port
            and str(port.get("published")) == published_port
        ]
        if len(matching_ports) != 1:
            raise ComposeBindingError(
                f"service {service_name!r} must publish {published_port}:{target_port} exactly once"
            )

        actual_host = matching_ports[0].get("host_ip")
        if actual_host != expected_host:
            raise ComposeBindingError(
                f"service {service_name!r} binds {published_port} to {actual_host!r}; "
                f"expected {expected_host!r}"
            )


def _api_service(config: dict[str, Any]) -> dict[str, Any]:
    services = config.get("services")
    if not isinstance(services, dict):
        raise ComposeBindingError(
            "docker compose config is missing the services object"
        )
    api = services.get("api")
    if not isinstance(api, dict):
        raise ComposeBindingError("docker compose config is missing the api service")
    return api


def _assert_api_storage(
    config: dict[str, Any],
    *,
    expected_database_url: str,
    expected_log_level: str,
    expected_project_name: str = "campusvoice",
    expected_volume_source: str = "campusvoice_data",
    expected_volume_name: str = "campusvoice_campusvoice_data",
) -> None:
    if config.get("name") != expected_project_name:
        raise ComposeBindingError(
            f"Compose project name must be {expected_project_name!r}"
        )

    api = _api_service(config)
    if api.get("working_dir") != "/data":
        raise ComposeBindingError(
            "api working_dir must be /data so relative SQLite URLs use the volume"
        )

    environment = api.get("environment")
    if not isinstance(environment, dict):
        raise ComposeBindingError("api environment is missing")
    if environment.get("CAMPUSVOICE_DATABASE_URL") != expected_database_url:
        raise ComposeBindingError(
            "api database URL was not preserved by Compose interpolation"
        )
    if environment.get("CAMPUSVOICE_LOG_LEVEL") != expected_log_level:
        raise ComposeBindingError("api log level was not passed through Compose")

    mounts = api.get("volumes")
    if not isinstance(mounts, list):
        raise ComposeBindingError("api volumes are missing")
    data_mounts = [
        mount
        for mount in mounts
        if isinstance(mount, dict) and mount.get("target") == "/data"
    ]
    if (
        len(data_mounts) != 1
        or data_mounts[0].get("type") != "volume"
        or data_mounts[0].get("source") != expected_volume_source
    ):
        raise ComposeBindingError(
            f"api /data must be backed by the {expected_volume_source} named volume"
        )

    volumes = config.get("volumes")
    if not isinstance(volumes, dict):
        raise ComposeBindingError("top-level volumes are missing")
    data_volume = volumes.get(expected_volume_source)
    if not isinstance(data_volume, dict):
        raise ComposeBindingError(
            f"top-level volume {expected_volume_source!r} is missing"
        )
    serialized_name = data_volume.get("name")
    if serialized_name is not None and serialized_name != expected_volume_name:
        raise ComposeBindingError(
            f"{expected_volume_source} resolved to unexpected volume name {serialized_name!r}"
        )


def main() -> int:
    try:
        default = _load_compose_config(bind_host=None)
        _assert_bindings(default, "127.0.0.1")
        _assert_api_storage(
            default,
            expected_database_url=DEFAULT_DATABASE_URL,
            expected_log_level="INFO",
        )

        network = _load_compose_config(bind_host="0.0.0.0")
        _assert_bindings(network, "0.0.0.0")

        example = _load_compose_config(bind_host=None, env_file=ENV_EXAMPLE)
        _assert_bindings(example, "127.0.0.1")
        _assert_api_storage(
            example,
            expected_database_url=EXAMPLE_DATABASE_URL,
            expected_log_level="INFO",
        )

        overridden = _load_compose_config(
            bind_host=None,
            overrides={
                "CAMPUSVOICE_DATABASE_URL": EXTERNAL_DATABASE_URL,
                "CAMPUSVOICE_LOG_LEVEL": "ERROR",
            },
        )
        _assert_api_storage(
            overridden,
            expected_database_url=EXTERNAL_DATABASE_URL,
            expected_log_level="ERROR",
        )

        smoke = _load_compose_config(
            bind_host=None,
            compose_files=(COMPOSE_FILE, SMOKE_FILE, MULTI_WORKER_FILE),
            profiles=("multi-worker",),
        )
        _assert_bindings(smoke, "127.0.0.1")
        _assert_api_storage(
            smoke,
            expected_database_url=EXAMPLE_DATABASE_URL,
            expected_log_level="INFO",
            expected_project_name="campusvoice-smoke",
            expected_volume_source="campusvoice_smoke_data",
            expected_volume_name="campusvoice-smoke_campusvoice_smoke_data",
        )
    except ComposeBindingError as error:
        print(f"Compose binding check failed: {error}", file=sys.stderr)
        return 1

    print(
        "Compose checks passed for bindings, SQLite volume persistence, and explicit overrides."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
