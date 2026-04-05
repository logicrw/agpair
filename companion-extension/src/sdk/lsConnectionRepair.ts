import * as childProcess from "child_process";
import * as http from "http";
import * as https from "https";
import { promisify } from "util";

export interface LsProcessInfo {
  pid: number;
  csrfToken: string;
  extensionServerPort: number | null;
  extensionServerCsrfToken: string | null;
  commandLine: string;
}

export interface DiscoveredLsConnection {
  pid: number;
  port: number;
  csrfToken: string;
  useTls: boolean;
  source: "live-rpc-port" | "extension-server-port";
}

export function extractCliArg(
  commandLine: string,
  name: string,
): string | null {
  const equalsMatch = commandLine.match(new RegExp(`--${name}=([^\\s"]+)`));
  if (equalsMatch) {
    return equalsMatch[1];
  }
  const spacedMatch = commandLine.match(new RegExp(`--${name}\\s+([^\\s"]+)`));
  return spacedMatch ? spacedMatch[1] : null;
}

export function parseLsProcessLine(line: string): LsProcessInfo | null {
  const trimmed = line.trim();
  if (!trimmed) {
    return null;
  }
  const pidMatch = trimmed.match(/^(\d+)\s+/);
  if (!pidMatch) {
    return null;
  }
  const pid = parseInt(pidMatch[1], 10);
  const csrfToken = extractCliArg(trimmed, "csrf_token");
  if (!Number.isFinite(pid) || !csrfToken) {
    return null;
  }
  const extensionServerPortRaw = extractCliArg(
    trimmed,
    "extension_server_port",
  );
  const extensionServerPort = extensionServerPortRaw
    ? parseInt(extensionServerPortRaw, 10)
    : null;
  const extensionServerCsrfToken = extractCliArg(
    trimmed,
    "extension_server_csrf_token",
  );
  return {
    pid,
    csrfToken,
    extensionServerPort:
      extensionServerPort !== null && Number.isFinite(extensionServerPort)
        ? extensionServerPort
        : null,
    extensionServerCsrfToken,
    commandLine: trimmed,
  };
}

export function selectLsProcessInfo(
  psOutput: string,
  workspaceHint = "",
): LsProcessInfo | null {
  const parsed = psOutput
    .split("\n")
    .map((line) => parseLsProcessLine(line))
    .filter((entry): entry is LsProcessInfo => entry !== null);
  if (parsed.length === 0) {
    return null;
  }
  if (workspaceHint) {
    const hinted = parsed.find((entry) =>
      entry.commandLine.includes(workspaceHint),
    );
    if (hinted) {
      return hinted;
    }
  }
  return parsed[0];
}

export function parseListeningPorts(lsofOutput: string): number[] {
  const ports = new Set<number>();
  for (const match of lsofOutput.matchAll(/127\.0\.0\.1:(\d+)/g)) {
    const port = parseInt(match[1], 10);
    if (Number.isFinite(port)) {
      ports.add(port);
    }
  }
  return Array.from(ports).sort((a, b) => a - b);
}

async function probeRpcPort(
  port: number,
  csrfToken: string,
  useTls: boolean,
): Promise<boolean> {
  const transport = useTls ? https : http;
  const protocol = useTls ? "https" : "http";
  return new Promise((resolve) => {
    const req = transport.request(
      `${protocol}://127.0.0.1:${port}/exa.language_server_pb.LanguageServerService/GetUserStatus`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": 2,
          "x-codeium-csrf-token": csrfToken,
        },
        rejectUnauthorized: false,
        timeout: 1500,
      },
      (res) => {
        resolve(res.statusCode === 200);
      },
    );
    req.on("error", () => resolve(false));
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
    req.write("{}");
    req.end();
  });
}

export async function discoverLiveLsConnection(
  workspaceHint = "",
): Promise<DiscoveredLsConnection | null> {
  if (process.platform === "win32") {
    return null;
  }

  const execFile = promisify(childProcess.execFile);
  let psOutput = "";
  try {
    ({ stdout: psOutput } = await execFile(
      "sh",
      [
        "-lc",
        "ps -eo pid,args 2>/dev/null | grep language_server | grep csrf_token | grep -v grep",
      ],
      { encoding: "utf8", timeout: 5000 },
    ));
  } catch {
    return null;
  }

  const processInfo = selectLsProcessInfo(psOutput, workspaceHint);
  if (!processInfo) {
    return null;
  }

  let lsofOutput = "";
  try {
    ({ stdout: lsofOutput } = await execFile(
      "lsof",
      ["-nP", "-a", "-p", String(processInfo.pid), "-iTCP", "-sTCP:LISTEN"],
      { encoding: "utf8", timeout: 5000 },
    ));
  } catch {
    lsofOutput = "";
  }

  const candidatePorts = parseListeningPorts(lsofOutput).filter(
    (port) => port !== processInfo.extensionServerPort,
  );

  for (const port of candidatePorts) {
    if (await probeRpcPort(port, processInfo.csrfToken, true)) {
      return {
        pid: processInfo.pid,
        port,
        csrfToken: processInfo.csrfToken,
        useTls: true,
        source: "live-rpc-port",
      };
    }
    if (await probeRpcPort(port, processInfo.csrfToken, false)) {
      return {
        pid: processInfo.pid,
        port,
        csrfToken: processInfo.csrfToken,
        useTls: false,
        source: "live-rpc-port",
      };
    }
  }

  if (processInfo.extensionServerPort && processInfo.extensionServerCsrfToken) {
    if (
      await probeRpcPort(
        processInfo.extensionServerPort,
        processInfo.extensionServerCsrfToken,
        true,
      )
    ) {
      return {
        pid: processInfo.pid,
        port: processInfo.extensionServerPort,
        csrfToken: processInfo.extensionServerCsrfToken,
        useTls: true,
        source: "extension-server-port",
      };
    }
    if (
      await probeRpcPort(
        processInfo.extensionServerPort,
        processInfo.extensionServerCsrfToken,
        false,
      )
    ) {
      return {
        pid: processInfo.pid,
        port: processInfo.extensionServerPort,
        csrfToken: processInfo.extensionServerCsrfToken,
        useTls: false,
        source: "extension-server-port",
      };
    }
  }

  return null;
}
