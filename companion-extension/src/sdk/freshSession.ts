export interface SessionLike {
  id?: string | null;
}

export interface RecentTrajectoryLike {
  googleAgentId?: string | null;
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

export function pickFreshTrajectoryId(
  beforeIds: Set<string>,
  trajectories: RecentTrajectoryLike[],
): string | null {
  for (const trajectory of trajectories) {
    const id =
      typeof trajectory.googleAgentId === "string"
        ? trajectory.googleAgentId
        : "";
    if (id.length > 0 && !beforeIds.has(id)) {
      return id;
    }
  }
  return null;
}
