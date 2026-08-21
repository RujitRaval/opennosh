import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import test from "node:test";

import { main, PACKAGE_VERSION } from "../bin/opennosh.mjs";

function output() {
  let value = "";
  return {
    stream: {
      write(chunk) {
        value += chunk;
      },
    },
    read() {
      return value;
    },
  };
}

test("prints help without touching the filesystem", () => {
  const stdout = output();
  const status = main([], {
    stdout: stdout.stream,
    pathExists() {
      throw new Error("help must not inspect paths");
    },
  });

  assert.equal(status, 0);
  assert.match(stdout.read(), /opennosh init \[directory\]/);
});

test("prints the package version", () => {
  const stdout = output();

  assert.equal(main(["--version"], { stdout: stdout.stream }), 0);
  assert.equal(stdout.read(), `${PACKAGE_VERSION}\n`);
});

test("clones with argument arrays and prints safe next steps", () => {
  const stdout = output();
  const calls = [];
  const status = main(["init", "my-nosh"], {
    cwd: "/workspace",
    stdout: stdout.stream,
    pathExists: () => false,
    run(command, args, options) {
      calls.push({ command, args, options });
      return { status: 0 };
    },
  });

  assert.equal(status, 0);
  assert.deepEqual(calls[0].args, ["--version"]);
  assert.deepEqual(calls[1].args, [
    "clone",
    "--depth",
    "1",
    "--single-branch",
    "https://github.com/RujitRaval/opennosh.git",
    "/workspace/my-nosh",
  ]);
  assert.equal(calls[1].options.stdio, "inherit");
  assert.match(stdout.read(), /Next: cd my-nosh/);
});

test("uses opennosh as the default directory", () => {
  const calls = [];
  const status = main(["init"], {
    cwd: "/workspace",
    pathExists: () => false,
    run(command, args) {
      calls.push({ command, args });
      return { status: 0 };
    },
  });

  assert.equal(status, 0);
  assert.equal(calls[1].args.at(-1), "/workspace/opennosh");
});

test("refuses to overwrite an existing path", () => {
  const stderr = output();
  const status = main(["init", "existing"], {
    cwd: "/workspace",
    stderr: stderr.stream,
    pathExists: () => true,
    run() {
      throw new Error("existing targets must not run Git");
    },
  });

  assert.equal(status, 2);
  assert.match(stderr.read(), /Refusing to overwrite existing path/);
});

test("reports a missing Git installation", () => {
  const stderr = output();
  const status = main(["init"], {
    stderr: stderr.stream,
    pathExists: () => false,
    run: () => ({ status: null, error: new Error("ENOENT") }),
  });

  assert.equal(status, 2);
  assert.match(stderr.read(), /Git is required/);
});

test("reports Git checks that exit unsuccessfully", () => {
  const stderr = output();
  const status = main(["init"], {
    stderr: stderr.stream,
    pathExists: () => false,
    run: () => ({ status: 127 }),
  });

  assert.equal(status, 2);
  assert.match(stderr.read(), /Git is required/);
});

test("reports clone failures", () => {
  const stdout = output();
  const stderr = output();
  let invocation = 0;
  const status = main(["init"], {
    stdout: stdout.stream,
    stderr: stderr.stream,
    pathExists: () => false,
    run() {
      invocation += 1;
      return { status: invocation === 1 ? 0 : 1 };
    },
  });

  assert.equal(status, 2);
  assert.match(stderr.read(), /could not clone/);
});

test("reports clone spawn errors", () => {
  const stderr = output();
  let invocation = 0;
  const status = main(["init"], {
    stderr: stderr.stream,
    pathExists: () => false,
    run() {
      invocation += 1;
      return invocation === 1
        ? { status: 0 }
        : { status: null, error: new Error("spawn failed") };
    },
  });

  assert.equal(status, 2);
  assert.match(stderr.read(), /could not clone/);
});

test("rejects unknown commands and extra init arguments", () => {
  const unknownError = output();
  const argumentsError = output();

  assert.equal(main(["start"], { stderr: unknownError.stream }), 2);
  assert.match(unknownError.read(), /Unknown command/);
  assert.equal(
    main(["init", "one", "two"], { stderr: argumentsError.stream }),
    2,
  );
  assert.match(argumentsError.read(), /at most one directory/);
});

test("rejects an option-looking init directory", () => {
  const stderr = output();

  assert.equal(main(["init", "--force"], { stderr: stderr.stream }), 2);
  assert.match(stderr.read(), /no options/);
});

test("the packed executable runs and reports its version", () => {
  const packageRoot = dirname(dirname(fileURLToPath(import.meta.url)));
  const directory = mkdtempSync(join(tmpdir(), "opennosh-npm-test-"));
  try {
    const packed = spawnSync(
      "npm",
      ["pack", "--json", "--pack-destination", directory],
      {
        cwd: packageRoot,
        encoding: "utf8",
        env: { ...process.env, npm_config_cache: join(directory, "npm-cache") },
      },
    );
    assert.equal(packed.status, 0, packed.stderr);
    const [{ filename }] = JSON.parse(packed.stdout);
    const extracted = spawnSync(
      "tar",
      ["-xzf", join(directory, filename), "-C", directory],
      { encoding: "utf8" },
    );
    assert.equal(extracted.status, 0, extracted.stderr);

    const executable = join(directory, "package", "bin", "opennosh.mjs");
    assert.notEqual(statSync(executable).mode & 0o111, 0);
    const result = spawnSync(process.execPath, [executable, "--version"], {
      encoding: "utf8",
    });

    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout, `${PACKAGE_VERSION}\n`);
    assert.equal(
      JSON.parse(
        readFileSync(join(directory, "package", "package.json"), "utf8"),
      ).name,
      "opennosh",
    );
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
