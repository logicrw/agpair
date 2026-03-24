/**
 * Bridge Auth Resolver — computes the effective bridge authentication mode.
 *
 * Three modes (in priority order):
 *   1. **configured** — user sets `antigravityCompanion.bridgeToken` explicitly.
 *   2. **insecure**  — user sets `antigravityCompanion.bridgeInsecure = true`.
 *      No auth is enforced on any route. For local debugging only.
 *   3. **generated** — default. A random 256-bit token is generated on first
 *      activation and persisted in VS Code `ExtensionContext.secrets`.
 *      Never written to settings JSON or repo files.
 *
 * The resolved mode and effective token are used by:
 *   - The bridge HTTP server auth gate
 *   - taskExecService.setBridgeContext() for /write_receipt prompt wiring
 *   - The /health payload (mode only — never the token itself)
 */

import * as crypto from "crypto";

/** Key used in VS Code SecretStorage to persist the generated bridge token. */
export const SECRET_STORAGE_KEY = "agpair.bridge.generatedToken";

/** Effective auth mode reported by /health (never includes the token). */
export type BridgeAuthMode = "configured" | "generated" | "insecure";

export interface BridgeAuthResolution {
  /** Auth mode in effect. */
  mode: BridgeAuthMode;
  /** Effective token, or empty string when mode is "insecure". */
  effectiveToken: string;
}

/**
 * Minimal interface for VS Code's SecretStorage.
 * Using an interface allows testing without a real VS Code context.
 */
export interface SecretStorage {
  get(key: string): Thenable<string | undefined>;
  store(key: string, value: string): Thenable<void>;
}

/**
 * Resolve the effective bridge auth configuration.
 *
 * @param configuredToken  Value of `antigravityCompanion.bridgeToken` (may be empty)
 * @param insecureMode     Value of `antigravityCompanion.bridgeInsecure`
 * @param secrets          VS Code SecretStorage (context.secrets)
 * @returns BridgeAuthResolution
 */
export async function resolveAuth(
  configuredToken: string,
  insecureMode: boolean,
  secrets: SecretStorage,
): Promise<BridgeAuthResolution> {
  // Priority 1: user-configured explicit token
  if (configuredToken) {
    return { mode: "configured", effectiveToken: configuredToken };
  }

  // Priority 2: explicit insecure mode (for local debugging)
  if (insecureMode) {
    return { mode: "insecure", effectiveToken: "" };
  }

  // Priority 3: load or generate a persistent random token
  let token = await secrets.get(SECRET_STORAGE_KEY);
  if (!token) {
    token = crypto.randomBytes(32).toString("hex");
    await secrets.store(SECRET_STORAGE_KEY, token);
  }

  return { mode: "generated", effectiveToken: token };
}
