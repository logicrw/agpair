from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


def _controller_id(value: str | None) -> str:
    normalized = (value or "generic").strip().lower().replace("_", "-")
    return normalized or "generic"


def _normalize_executor(value: str) -> str:
    from agpair.executors.policy import normalize_executor_id

    normalized = normalize_executor_id(value)
    assert normalized is not None
    return normalized


def _tuple_unique(values: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if not values:
        return ()
    seen: set[str] = set()
    normalized: list[str] = []
    for item in values:
        executor_id = _normalize_executor(str(item))
        if executor_id in seen:
            continue
        seen.add(executor_id)
        normalized.append(executor_id)
    return tuple(normalized)


def _immutable_nested_tuple_map(data: Mapping[str, tuple[str, ...]]) -> Mapping[str, tuple[str, ...]]:
    return MappingProxyType({key: tuple(value) for key, value in data.items()})


@dataclass(frozen=True)
class ExecutorPolicyOverlay:
    disabled_global: tuple[str, ...] = ()
    controller_disabled: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    controller_priority: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "ExecutorPolicyOverlay":
        return cls()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "ExecutorPolicyOverlay":
        if not data:
            return cls.empty()
        if not isinstance(data, Mapping):
            raise ValueError("executor policy file must be a JSON object")
        global_section = data.get("global") if isinstance(data.get("global"), Mapping) else {}
        disabled_global = _tuple_unique(list(global_section.get("disabled", []) or []))

        disabled_by_controller: dict[str, tuple[str, ...]] = {}
        priority_by_controller: dict[str, tuple[str, ...]] = {}
        controllers = data.get("controllers")
        if isinstance(controllers, Mapping):
            for raw_controller, raw_payload in controllers.items():
                controller = _controller_id(str(raw_controller))
                payload = raw_payload if isinstance(raw_payload, Mapping) else {}
                disabled = _tuple_unique(list(payload.get("disabled", []) or []))
                priority = _tuple_unique(list(payload.get("priority", []) or []))
                if disabled:
                    disabled_by_controller[controller] = disabled
                if priority:
                    priority_by_controller[controller] = priority

        return cls(
            disabled_global=disabled_global,
            controller_disabled=_immutable_nested_tuple_map(disabled_by_controller),
            controller_priority=_immutable_nested_tuple_map(priority_by_controller),
        )

    def to_dict(self) -> dict[str, Any]:
        controllers: dict[str, dict[str, Any]] = {}
        for controller, disabled in self.controller_disabled.items():
            controllers.setdefault(controller, {})["disabled"] = list(disabled)
        for controller, priority in self.controller_priority.items():
            controllers.setdefault(controller, {})["priority"] = list(priority)
        payload: dict[str, Any] = {"version": 1}
        if self.disabled_global:
            payload["global"] = {"disabled": list(self.disabled_global)}
        if controllers:
            payload["controllers"] = controllers
        return payload

    def disabled_for(self, controller: str | None) -> tuple[str, ...]:
        controller_id = _controller_id(controller)
        return tuple((*self.disabled_global, *self.controller_disabled.get(controller_id, ())))

    def priority_for(self, controller: str | None) -> tuple[str, ...]:
        return self.controller_priority.get(_controller_id(controller), ())

class ExecutorPolicyConfigError(Exception):
    pass


class ExecutorPolicyManager:
    def __init__(self, path: Path):
        self.path = path

    def read(self) -> ExecutorPolicyOverlay:
        if not self.path.exists():
            return ExecutorPolicyOverlay.empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ExecutorPolicyConfigError(f"Failed to read executor policy file: {exc}") from exc
        try:
            return ExecutorPolicyOverlay.from_dict(data)
        except ValueError as exc:
            raise ExecutorPolicyConfigError(str(exc)) from exc

    def write(self, overlay: ExecutorPolicyOverlay) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(overlay.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _mutate(self, fn) -> ExecutorPolicyOverlay:
        overlay = self.read()
        data = overlay.to_dict()
        fn(data)
        updated = ExecutorPolicyOverlay.from_dict(data)
        self.write(updated)
        return updated

    @staticmethod
    def _scope(data: dict[str, Any], *, controller: str | None, global_scope: bool) -> dict[str, Any]:
        if global_scope:
            return data.setdefault("global", {})
        controller_id = _controller_id(controller)
        return data.setdefault("controllers", {}).setdefault(controller_id, {})

    def disable(self, executor_id: str, *, controller: str | None = None, global_scope: bool = False) -> ExecutorPolicyOverlay:
        normalized = _normalize_executor(executor_id)

        def update(data: dict[str, Any]) -> None:
            scope = self._scope(data, controller=controller, global_scope=global_scope)
            disabled = _tuple_unique([*scope.get("disabled", []), normalized])
            scope["disabled"] = list(disabled)

        return self._mutate(update)

    def enable(self, executor_id: str, *, controller: str | None = None, global_scope: bool = False) -> ExecutorPolicyOverlay:
        normalized = _normalize_executor(executor_id)

        def update(data: dict[str, Any]) -> None:
            scope = self._scope(data, controller=controller, global_scope=global_scope)
            scope["disabled"] = [item for item in _tuple_unique(scope.get("disabled", [])) if item != normalized]

        return self._mutate(update)

    def set_priority(self, executor_ids: list[str], *, controller: str | None = None) -> ExecutorPolicyOverlay:
        normalized = list(_tuple_unique(executor_ids))
        controller_id = _controller_id(controller)

        def update(data: dict[str, Any]) -> None:
            data.setdefault("controllers", {}).setdefault(controller_id, {})["priority"] = normalized

        return self._mutate(update)

    def reset(self, *, controller: str | None = None, global_scope: bool = False) -> ExecutorPolicyOverlay:
        def update(data: dict[str, Any]) -> None:
            if global_scope:
                data.pop("global", None)
                return
            controllers = data.get("controllers")
            if isinstance(controllers, dict):
                controllers.pop(_controller_id(controller), None)

        return self._mutate(update)
