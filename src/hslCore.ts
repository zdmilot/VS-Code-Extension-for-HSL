import * as path from "path";

export const HXRUN_EXTENSIONS = [".hsl", ".sub", ".med"] as const;
export const SIMULATION_EXTENSIONS = [".hsl", ".sub"] as const;

export function isAbsoluteWindowsPath(filePath: string): boolean {
  return /^[a-zA-Z]:[\\/]/.test(filePath) || /^\\\\[^\\]+\\[^\\]+/.test(filePath);
}

export function resolveHslIncludeTarget(
  target: string,
  searchRoots: string[],
  includingFile?: string,
  exists: (candidate: string) => boolean = () => false
): string | null {
  const windowsTarget = target.replace(/\//g, "\\");
  const candidates: string[] = [];
  if (isAbsoluteWindowsPath(windowsTarget)) {
    candidates.push(path.win32.normalize(windowsTarget));
  } else {
    if (includingFile) {
      candidates.push(
        path.win32.normalize(
          path.win32.join(path.win32.dirname(includingFile), windowsTarget)
        )
      );
    }
    candidates.push(
      ...searchRoots.map((root) =>
        path.win32.normalize(path.win32.join(root, windowsTarget))
      )
    );
  }
  return candidates.find(exists) ?? null;
}

/** Preserve offsets/newlines while hiding strings and comments from code scans. */
export function maskCommentsAndStrings(text: string): string {
  return text.replace(
    /\/\/[^\n]*|\/\*[\s\S]*?\*\/|"(?:[^"\\]|\\.)*"/g,
    (value) => value.replace(/[^\r\n]/g, " ")
  );
}

export function hasMethodMain(text: string): boolean {
  return /\bmethod\s+main\s*\(/i.test(maskCommentsAndStrings(text));
}

export function isSupportedLaunchTarget(filePath: string, simulation: boolean): boolean {
  const extension = path.extname(filePath).toLowerCase();
  return simulation
    ? (SIMULATION_EXTENSIONS as readonly string[]).includes(extension)
    : (HXRUN_EXTENSIONS as readonly string[]).includes(extension);
}
