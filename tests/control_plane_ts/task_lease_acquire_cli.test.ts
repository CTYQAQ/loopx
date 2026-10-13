import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test, { type TestContext } from "node:test";

const entrypoint = fileURLToPath(
  new URL(
    "../../loopx/control_plane/work_items/task_lease_acquire_cli.ts",
    import.meta.url,
  ),
);

function runCli(input: unknown) {
  const child = spawnSync(
    process.execPath,
    ["--no-warnings", "--experimental-strip-types", entrypoint],
    {
      input: typeof input === "string" ? input : JSON.stringify(input),
      encoding: "utf8",
    },
  );
  assert.equal(child.error, undefined, child.error?.message);
  const lines = child.stdout.trim().split(/\r?\n/u).filter(Boolean);
  assert.equal(lines.length, 1, child.stdout);
  return {
    status: child.status,
    stderr: child.stderr,
    value: JSON.parse(lines[0]) as Record<string, unknown>,
  };
}

async function tempRequest(t: TestContext): Promise<Record<string, unknown>> {
  const root = await mkdtemp(join(tmpdir(), "loopx-task-lease-cli-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const authorityPath = join(root, "authority.json");
  const authorityContent = "authority-v1";
  await writeFile(authorityPath, authorityContent, "utf8");
  return {
    schema_version: "loopx_task_lease_acquire_native_v0",
    runtime_root: join(root, "runtime"),
    goal_id: "goal-cli",
    todo_id: "todo_cli_native",
    owner: "agent-cli",
    idempotency_key: "turn-cli",
    ttl_seconds: 120,
    write_scopes: ["docs/**"],
    expected_version: null,
    authority: {
      handoff_mode: "hard_lease",
      registered_agent_candidates: [["agent-cli"]],
      todos: [{
        todo_id: "todo_cli_native",
        status: "open",
        claimed_by: null,
        excluded_agents: [],
      }],
      todo_projection_error: null,
      source_receipts: [{
        source_id: "authority",
        path: authorityPath,
        state: "file",
        sha256: createHash("sha256").update(authorityContent).digest("hex"),
      }],
    },
  };
}

test("native CLI refuses fresh source execution and replays an original lease", async (t) => {
  const input = await tempRequest(t);
  const leaseDirectory = join(String(input.runtime_root), "goals", "goal-cli", "task-leases");
  const leasePath = join(leaseDirectory, "todo_cli_native.json");
  const first = runCli(input);
  assert.notEqual(first.status, 0);
  assert.equal(first.value.ok, false);
  assert.equal(first.value.error_code, "canonical_authority_required");
  await assert.rejects(readFile(leasePath), { code: "ENOENT" });

  // Historical record; future expiry makes the CLI test independent of wall-clock timing.
  await mkdir(leaseDirectory, { recursive: true });
  const original = JSON.stringify({
    schema_version: "task_lease_v0", goal_id: "goal-cli", todo_id: "todo_cli_native",
    owner: "agent-cli", idempotency_key: "turn-cli", write_scopes: ["docs/**"],
    acquire_ttl_seconds: 120, version: 1, lease_epoch: 1, status: "active",
    acquired_at: "2026-08-27T03:00:00Z", updated_at: "2026-08-27T03:00:00Z",
    expires_at: "2099-01-01T00:00:00Z",
  });
  await writeFile(leasePath, original);
  const replay = runCli(input);
  assert.equal(replay.status, 0);
  assert.equal(replay.value.ok, true);
  assert.equal(replay.value.acquired, false);
  assert.equal(replay.value.idempotent, true);
  assert.equal((replay.value.settlement as Record<string, unknown>).effect_id,
    "goal-cli:agent-cli:todo_cli_native:turn-cli");
  assert.equal(await readFile(leasePath, "utf8"), original);
});

test("native task-lease command returns one typed malformed-input envelope", () => {
  const malformed = runCli("not-json");
  assert.notEqual(malformed.status, 0);
  assert.equal(malformed.value.schema_version, "task_lease_v0");
  assert.equal(malformed.value.action, "acquire");
  assert.equal(malformed.value.error_code, "invalid_json");
});
