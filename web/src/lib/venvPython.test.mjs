// Run: node --test src/lib/venvPython.test.mjs   (from web/)

import { test } from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { venvPython } from "./venvPython.mjs";

const ROOT = path.join("repo");

test("windows uses Scripts/python.exe", () => {
  assert.equal(venvPython(ROOT, "win32", undefined), path.join(ROOT, ".venv", "Scripts", "python.exe"));
});

test("posix uses bin/python", () => {
  for (const platform of ["linux", "darwin"]) {
    assert.equal(venvPython(ROOT, platform, undefined), path.join(ROOT, ".venv", "bin", "python"));
  }
});

test("override wins, empty override is ignored", () => {
  assert.equal(venvPython(ROOT, "win32", "C:/py/python.exe"), "C:/py/python.exe");
  assert.equal(venvPython(ROOT, "linux", ""), path.join(ROOT, ".venv", "bin", "python"));
});
