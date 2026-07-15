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
BIND_HOST_ENV = "CAMPUSVOICE_BIND_HOST"
EXPECTED_PORTS = {
    "api": (8000, "8000"),
    "web": (3000, "3000"),
}


class ComposeBindingError(RuntimeError):
    pass


def _load_compose_config(bind_host: str | None) -> dict[str, Any]:
    docker = shutil.which("docker")
    if docker is None:
        raise ComposeBindingError("docker was not found on PATH")

    environment = os.environ.copy()
    for name in (
        BIND_HOST_ENV,
        "COMPOSE_ENV_FILES",
        "COMPOSE_FILE",
        "COMPOSE_PROFILES",
        "COMPOSE_PROJECT_NAME",
    ):
        environment.pop(name, None)
    environment["COMPOSE_DISABLE_ENV_FILE"] = "1"
    if bind_host is not None:
        environment[BIND_HOST_ENV] = bind_host

    completed = subprocess.run(
        [docker, "compose", "--file", str(COMPOSE_FILE), "config", "--format", "json"],
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


def main() -> int:
    try:
        _assert_bindings(_load_compose_config(bind_host=None), "127.0.0.1")
        _assert_bindings(_load_compose_config(bind_host="0.0.0.0"), "0.0.0.0")
    except ComposeBindingError as error:
        print(f"Compose binding check failed: {error}", file=sys.stderr)
        return 1

    print(
        "Compose binding checks passed for the loopback default and explicit network override."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
