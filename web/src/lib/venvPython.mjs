// Resolves the repo-root venv's Python interpreter cross-platform: Windows venvs
// put it at .venv\Scripts\python.exe, POSIX venvs at .venv/bin/python.
// UPWORK_PYTHON (passed as `override`) wins when set, e.g. for a system Python.
//
// Plain .mjs (not TS) so both the Next routes and the zero-build
// scripts/notify.mjs can import it.

import path from "node:path";

/**
 * @param {string} root repo root containing .venv
 * @param {string} platform usually process.platform
 * @param {string | undefined} override usually process.env.UPWORK_PYTHON
 * @returns {string}
 */
export function venvPython(root, platform, override) {
  if (override) return override;
  return platform === "win32"
    ? path.join(root, ".venv", "Scripts", "python.exe")
    : path.join(root, ".venv", "bin", "python");
}
