from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock
from time import perf_counter
from typing import Literal

ComponentName = Literal["asr", "intent", "retrieval", "llm", "action", "verification"]
ComponentOutcome = Literal["ok", "error"]

_COMPONENT_OPERATIONS: dict[ComponentName, frozenset[str]] = {
    "asr": frozenset({"session"}),
    "intent": frozenset({"parse"}),
    "retrieval": frozenset({"search"}),
    "llm": frozenset({"complete"}),
    "action": frozenset({"execute"}),
    "verification": frozenset({"verify"}),
}


@dataclass(slots=True)
class _Aggregate:
    count: int = 0
    error_count: int = 0
    total_duration_ms: float = 0
    max_duration_ms: float = 0

    def add(self, duration_seconds: float, *, error: bool) -> None:
        duration_ms = max(0.0, duration_seconds * 1_000)
        self.count += 1
        self.error_count += int(error)
        self.total_duration_ms += duration_ms
        self.max_duration_ms = max(self.max_duration_ms, duration_ms)

    def snapshot(self) -> dict[str, int | float]:
        return {
            "count": self.count,
            "error_count": self.error_count,
            "total_duration_ms": round(self.total_duration_ms, 3),
            "max_duration_ms": round(self.max_duration_ms, 3),
        }


@dataclass(slots=True)
class ComponentObservation:
    """Allows a successful call to mark a business-level error outcome."""

    error: bool = False


class InMemoryMetrics:
    """Process-local, bounded-cardinality runtime metrics.

    The registry intentionally accepts only route templates and a fixed set of
    component/operation pairs. It must never receive user IDs, entity IDs,
    query strings, or free-form provider names.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._http: defaultdict[tuple[str, str, str], _Aggregate] = defaultdict(_Aggregate)
        self._components: defaultdict[tuple[str, str, str], _Aggregate] = defaultdict(_Aggregate)

    def record_http(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        status_family = f"{status_code // 100}xx" if 100 <= status_code <= 599 else "other"
        key = (method.upper(), route, status_family)
        with self._lock:
            self._http[key].add(duration_seconds, error=status_code >= 400)

    def record_component(
        self,
        *,
        component: ComponentName,
        operation: str,
        outcome: ComponentOutcome,
        duration_seconds: float,
    ) -> None:
        if operation not in _COMPONENT_OPERATIONS[component]:
            raise ValueError(f"unsupported metric operation for {component}: {operation}")
        key = (component, operation, outcome)
        with self._lock:
            self._components[key].add(duration_seconds, error=outcome == "error")

    def snapshot(self) -> dict[str, list[dict[str, object]]]:
        with self._lock:
            http_items: list[dict[str, object]] = [
                {
                    "method": method,
                    "route": route,
                    "status_family": status_family,
                    **aggregate.snapshot(),
                }
                for (method, route, status_family), aggregate in sorted(self._http.items())
            ]
            component_items: list[dict[str, object]] = [
                {
                    "component": component,
                    "operation": operation,
                    "outcome": outcome,
                    **aggregate.snapshot(),
                }
                for (component, operation, outcome), aggregate in sorted(self._components.items())
            ]
        return {"http": http_items, "components": component_items}


@contextmanager
def observe_component(
    metrics: InMemoryMetrics | None,
    component: ComponentName,
    operation: str,
) -> Iterator[ComponentObservation]:
    """Time one fixed component operation without recording request content."""

    observation = ComponentObservation()
    if metrics is None:
        yield observation
        return
    started = perf_counter()
    try:
        yield observation
    except Exception:
        metrics.record_component(
            component=component,
            operation=operation,
            outcome="error",
            duration_seconds=perf_counter() - started,
        )
        raise
    metrics.record_component(
        component=component,
        operation=operation,
        outcome="error" if observation.error else "ok",
        duration_seconds=perf_counter() - started,
    )
