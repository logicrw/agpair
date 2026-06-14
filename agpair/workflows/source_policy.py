from __future__ import annotations


def build_source_policy(
    *,
    mode: str,
    blocked_domains: list[str] | None = None,
    required_sources: list[str] | None = None,
) -> dict:
    return {
        "schema_version": "1",
        "mode": mode,
        "blocked_domains": list(blocked_domains or []),
        "required_sources": list(required_sources or []),
        "enforcement": "instruction",
        "warning": "This is not a sandbox or network firewall; the controller must verify cited sources and evidence.",
    }
