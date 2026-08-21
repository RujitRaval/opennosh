#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, realpathSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const REPOSITORY_URL = "https://github.com/RujitRaval/opennosh.git";
const packageMetadata = JSON.parse(
  readFileSync(new URL("../package.json", import.meta.url), "utf8"),
);

export const PACKAGE_VERSION = packageMetadata.version;

const HELP = `opennosh ${PACKAGE_VERSION}

Bootstrap a local opennosh checkout.

Usage:
  opennosh init [directory]
  opennosh --help
  opennosh --version

The init command clones the public opennosh repository. It never overwrites an
existing path. Docker, Docker Compose, and local configuration remain under your
control.`;

function write(stream, message) {
  stream.write(`${message}\n`);
}

export function main(
  argv,
  {
    cwd = process.cwd(),
    pathExists = existsSync,
    run = spawnSync,
    stdout = process.stdout,
    stderr = process.stderr,
  } = {},
) {
  const [command, directory, ...extras] = argv;

  if (command === undefined || command === "--help" || command === "-h") {
    write(stdout, HELP);
    return 0;
  }

  if (command === "--version" || command === "-v") {
    write(stdout, PACKAGE_VERSION);
    return 0;
  }

  if (command !== "init") {
    write(stderr, `Unknown command: ${command}`);
    write(stderr, "Run opennosh --help for usage.");
    return 2;
  }

  if (extras.length > 0 || directory?.startsWith("-")) {
    write(stderr, "init accepts at most one directory and no options.");
    return 2;
  }

  const requestedDirectory = directory ?? "opennosh";
  const target = resolve(cwd, requestedDirectory);
  if (pathExists(target)) {
    write(stderr, `Refusing to overwrite existing path: ${target}`);
    return 2;
  }

  const gitCheck = run("git", ["--version"], { encoding: "utf8" });
  if (gitCheck.error || gitCheck.status !== 0) {
    write(stderr, "Git is required. Install Git, then run this command again.");
    return 2;
  }

  write(stdout, `Cloning opennosh into ${target}...`);
  const clone = run(
    "git",
    ["clone", "--depth", "1", "--single-branch", REPOSITORY_URL, target],
    { stdio: "inherit" },
  );
  if (clone.error || clone.status !== 0) {
    write(stderr, "Git could not clone opennosh. No existing path was overwritten.");
    return 2;
  }

  write(stdout, "opennosh is ready for local setup.");
  write(stdout, `Next: cd ${requestedDirectory}`);
  write(stdout, "Then follow README.md to configure .env and run Docker Compose.");
  return 0;
}

if (
  process.argv[1] &&
  realpathSync(fileURLToPath(import.meta.url)) === realpathSync(resolve(process.argv[1]))
) {
  process.exitCode = main(process.argv.slice(2));
}
