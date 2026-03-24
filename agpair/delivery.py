"""Parse and strip the stable delivery identity header from terminal bodies.

The supervisor companion prepends an ``X-Delivery-Id: <id>`` header line
to the body of terminal replies (EVIDENCE_PACK, BLOCKED, COMMITTED) so
the desktop side can deduplicate replays of the same logical terminal
delivery even when agent-bus assigns a fresh message id.

This module is the single parsing authority on the desktop side.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agpair.transport import messages

_DELIVERY_HEADER_RE = re.compile(
    r"^X-Delivery-Id:\s*(?P<delivery_id>\S+)\s*\n?",
)

_TERMINAL_STATUSES = frozenset({
    messages.EVIDENCE_PACK,
    messages.BLOCKED,
    messages.COMMITTED,
})


@dataclass(frozen=True)
class ParsedBody:
    """Result of parsing a receipt body for delivery identity."""

    delivery_id: str | None
    clean_body: str


def parse_delivery_header(status: str, body: str) -> ParsedBody:
    """Extract ``X-Delivery-Id`` from *body* if *status* is terminal.

    Non-terminal statuses bypass parsing entirely, returning the body
    unchanged with ``delivery_id=None``.  This prevents accidental
    activation for ACK / RUNNING bodies that might coincidentally
    contain the header text.

    Returns
    -------
    ParsedBody
        ``delivery_id`` is the extracted id (or ``None``), and
        ``clean_body`` is the body with the header line stripped.
    """
    if status not in _TERMINAL_STATUSES:
        return ParsedBody(delivery_id=None, clean_body=body)

    match = _DELIVERY_HEADER_RE.match(body)
    if match is None:
        return ParsedBody(delivery_id=None, clean_body=body)

    delivery_id = match.group("delivery_id")
    clean_body = body[match.end():]
    return ParsedBody(delivery_id=delivery_id, clean_body=clean_body)
