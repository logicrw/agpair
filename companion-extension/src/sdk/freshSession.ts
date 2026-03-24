export interface SessionLike {
  id?: string | null;
}

export function pickFreshSessionId(
  beforeIds: Set<string>,
  returnedId: string | null | undefined,
  sessions: SessionLike[],
): string | null {
  if (returnedId && !beforeIds.has(returnedId)) {
    return returnedId;
  }

  const fresh = sessions
    .map((session) => (typeof session.id === "string" ? session.id : ""))
    .filter((id) => id.length > 0 && !beforeIds.has(id));

  if (fresh.length === 0) {
    return null;
  }

  return fresh[fresh.length - 1];
}
