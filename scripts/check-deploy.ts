import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const directory = mkdtempSync(join(tmpdir(), "blaubeere-deploy-"));
const vm = "a".repeat(32), revision = "b".repeat(40);
try {
  writeFileSync(join(directory, "jio"), `#!/usr/bin/env bash
set -eu
printf '%s\\n' "$1" >> "$DEPLOY_TEST/log"
case "$1" in
  usage)
    if [[ "$CASE" == wrong_account ]]; then echo 'Account: demo (shared by its API keys)'; else echo 'Account: blaubeere (shared by its API keys)'; fi
    ;;
  exec)
    if [[ "$CASE" == auth ]]; then echo 'jio: unauthorised' >&2; exit 1; fi
    if [[ "$CASE" == stopped && ! -f "$DEPLOY_TEST/started" ]]; then echo "jio: session $2 is Stopped" >&2; exit 1; fi
    ;;
  start) touch "$DEPLOY_TEST/started" ;;
  ports)
    if [[ "$CASE" == bad_url ]]; then echo '8080 published http://insecure.example'; else echo '8080 published https://app.example'; fi
    echo '3102 published https://landing.example'
    ;;
  connect) cat > "$DEPLOY_TEST/payload" ;;
  *) echo "Unexpected command $1" >&2; exit 1 ;;
esac
`, { mode: 0o700 });
  writeFileSync(join(directory, "bun"), '#!/bin/sh\n[ "$CASE" != verify_failure ]\n', { mode: 0o700 });
  for (const scenario of ["wrong_account", "auth", "bad_url", "stopped", "verify_failure"]) {
    writeFileSync(join(directory, "log"), "");
    const output = join(directory, "outputs");
    writeFileSync(output, "");
    const result = Bun.spawnSync(["bash", "deploy/jio.sh"], { env: { ...process.env, PATH: `${directory}:${process.env.PATH}`, DEPLOY_TEST: directory, CASE: scenario, JIO_VM_ID: vm, GIT_REF: revision, GITHUB_OUTPUT: output } });
    const commands = readFileSync(join(directory, "log"), "utf8");
    if (scenario === "stopped" || scenario === "verify_failure") {
      assert.equal(result.exitCode, scenario === "stopped" ? 0 : 1, result.stderr.toString());
      assert.equal(commands.includes("start\n"), scenario === "stopped");
      assert.equal(readFileSync(output, "utf8"), "app_url=https://app.example\nlanding_url=https://landing.example\n", "Publish the actual Jio URLs even if later verification fails");
      const payload = readFileSync(join(directory, "payload"), "utf8");
      assert.ok(payload.startsWith(`bash -s -- '${revision}' 'https://app.example' 'https://landing.example'`));
      assert.ok(payload.endsWith("BLAUBEERE_DEPLOY_SCRIPT\n"));
    } else {
      assert.notEqual(result.exitCode, 0);
      assert.equal(readFileSync(output, "utf8"), "", "Do not publish URLs before validating the account and origins");
      assert.ok(!commands.includes("start\n") && !commands.includes("connect\n"), "Wrong accounts, invalid credentials and origins must fail before deployment");
    }
  }
  console.log("Deployment checks passed: Blaubeere account, exact revision, HTTPS origins, stopped-VM recovery, authentication failures and Jio URL outputs.");
} finally { rmSync(directory, { recursive: true, force: true }); }
