import * as fs from "fs";
import * as path from "path";

interface WriteMarkerParams {
  dir: string;
  markerName: string;
  value: string;
  writtenPaths: string[];
  mode?: number;
}

export function writeMarkerToDir({
  dir,
  markerName,
  value,
  writtenPaths,
  mode,
}: WriteMarkerParams): boolean {
  try {
    const markerDir = path.join(dir, ".agpair");
    fs.mkdirSync(markerDir, { recursive: true });
    const markerFile = path.join(markerDir, markerName);
    fs.writeFileSync(markerFile, value, {
      encoding: "utf-8",
      mode,
    });
    if (mode !== undefined) {
      fs.chmodSync(markerFile, mode);
    }
    if (!writtenPaths.includes(markerFile)) {
      writtenPaths.push(markerFile);
    }
    return true;
  } catch {
    return false;
  }
}

export function removeWrittenMarkers(writtenPaths: string[]): void {
  for (const markerFile of writtenPaths) {
    try {
      if (fs.existsSync(markerFile)) {
        fs.unlinkSync(markerFile);
      }
    } catch {
      // best-effort
    }
  }
  writtenPaths.length = 0;
}
