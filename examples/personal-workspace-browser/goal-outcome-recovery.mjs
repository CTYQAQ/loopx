// Packaged outcome recovery against real disposable SQLite/HTTP/CLI authority.
// Only transport failures are injected; refinement and replay use the native owner.
import assert from "node:assert/strict";
import {spawn, execFileSync} from "node:child_process";
import {createServer} from "node:net";
import {createInterface} from "node:readline";
import {mkdtemp, mkdir, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {fileURLToPath} from "node:url";
import {resolve} from "node:path";
import {resolveTestPython} from "../../scripts/test-python.mjs";
import {launchBrowser, loadPlaywright, waitForHttp} from "../dashboard-browser-smoke-support.mjs";

const repoRoot = fileURLToPath(new URL("../../", import.meta.url));
const python = resolveTestPython();
const root = await mkdtemp(resolve(tmpdir(), "loopx-outcome-recovery-"));
const output = resolve(repoRoot, "output/playwright/goal-outcome-recovery");
const reservation = createServer();
await new Promise(accept => reservation.listen(0, "127.0.0.1", accept));
const port = reservation.address().port;
await new Promise(accept => reservation.close(accept));
const child = spawn(python, ["-u", "examples/personal-workspace-browser/goal-outcome-backend.py", root, String(port), "--without-check-plan"],
  {cwd: repoRoot, stdio: ["ignore", "pipe", "pipe"]});
let diagnostic = "", browser, page;
child.stderr.on("data", chunk => diagnostic = (diagnostic + chunk).slice(-4000));
const lines = createInterface({input: child.stdout});
try {
  const authority = await new Promise((accept, reject) => {
    const timer = setTimeout(() => reject(Error(`Outcome fixture did not start: ${diagnostic}`)), 30000);
    child.once("exit", () => {clearTimeout(timer); reject(Error(`Outcome fixture exited: ${diagnostic}`));});
    lines.once("line", line => {clearTimeout(timer); accept(JSON.parse(line));});
  });
  const cli = (...args) => JSON.parse(execFileSync(python, ["-m", "loopx.cli", "--format", "json",
    "--registry", authority.registry, "--runtime-root", authority.runtime, "goal-acceptance", ...args,
    "--goal-id", "product-launch"], {cwd: repoRoot, encoding: "utf8"}));
  const outcome = () => cli("inspect").goal_acceptance_contract.outcome_requirements.outcomes[0];
  const initialCount = outcome().requirements.length;
  const requests = [], errors = [];
  let readsFail = false, loseReply = false;
  const url = `http://127.0.0.1:${port}/chat/`;
  await waitForHttp(url);
  browser = await launchBrowser(loadPlaywright().chromium);
  page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(() => localStorage.setItem("loopx-pw-locale", "en"));
  await page.route("**/api/chat/goal-acceptance**", async route => {
    if (route.request().method() === "GET") {
      if (readsFail) return route.fulfill({status: 503, json: {ok: false, error: "Synthetic read interruption"}});
      return route.continue();
    }
    requests.push(route.request().postDataJSON());
    const response = await route.fetch();
    if (loseReply) {
      assert.equal(response.status(), 200, "The native write commits before its reply is lost");
      loseReply = false; readsFail = true;
      return route.fulfill({status: 503, json: {ok: false, error: "Synthetic lost reply"}});
    }
    return route.fulfill({response});
  });
  await page.goto(url, {waitUntil: "domcontentloaded"});
  await page.locator(".personal-goal-link").first().click();
  await page.getByRole("button", {name: "Overview", exact: true}).click();
  const region = page.locator(".goal-outcomes"), form = region.locator("form");
  await region.locator(".goal-overview-criteria").getByText("Retain the approved introduction", {exact: true}).waitFor();
  assert.equal(await page.locator(".delivery-acceptance-contract").count(), 0, "Start on the default review path without automated checks");
  await region.getByText("Add a requirement", {exact: true}).click();
  const text = form.locator("textarea");
  const save = form.getByRole("button", {name: "Retain requirement", exact: true});
  const refresh = form.getByRole("button", {name: "Refresh result", exact: true});
  const rebase = form.getByRole("button", {name: "Rebase on current version", exact: true});
  const recovered = async () => {
    readsFail = false;
    await refresh.click();
    await save.waitFor({state: "visible"});
    await page.waitForFunction(() => !document.querySelector(".goal-outcomes button[type=submit]")?.disabled);
  };
  const draft = "Keep the conclusion understandable without prior context";
  await text.fill(draft);
  readsFail = true;
  await refresh.click();
  await page.locator(".delivery-acceptance-contract [role=alert]").waitFor();
  assert.equal(await text.inputValue(), draft, "Unsubmitted draft survives a failed refetch");
  assert.equal(await save.isDisabled(), true, "An unavailable basis cannot authorize a write");
  assert.equal(requests.length, 0);
  await recovered();
  loseReply = true;
  await save.focus(); await page.keyboard.press("Enter");
  await region.getByRole("status").filter({hasText: "Result unconfirmed"}).waitFor();
  await page.locator(".delivery-acceptance-contract [role=alert]").waitFor();
  assert.equal(await text.inputValue(), draft);
  assert.equal(await text.isDisabled(), true, "Pending text belongs to the original request");
  assert.equal(await rebase.isDisabled(), true, "Failed reads cannot replace the original operation identity");
  assert.equal(outcome().requirements.length, initialCount + 1, "Exactly one real native refinement committed");
  assert.equal(requests.length, 1);
  await mkdir(output, {recursive: true});
  await page.screenshot({path: resolve(output, "read-failure-desktop.png"), animations: "disabled"});
  await page.setViewportSize({width: 390, height: 844});
  await form.scrollIntoViewIfNeeded();
  await page.screenshot({path: resolve(output, "read-failure-narrow.png"), animations: "disabled"});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await recovered();
  assert.equal(await text.inputValue(), draft);
  assert.equal(await text.isDisabled(), true);
  await save.click();
  await region.getByRole("status").filter({hasText: "Requirement retained alongside earlier requirements"}).waitFor();
  assert.equal(await text.inputValue(), "");
  assert.equal(await text.isDisabled(), false);
  assert.equal(requests.length, 2);
  assert.deepEqual(requests[1], requests[0], "Retry preserves the entire original operation and requirement identity");
  assert.equal(outcome().requirements.length, initialCount + 1, "Replay cannot duplicate the requirement");
  // A genuine concurrent native write invalidates the UI basis. Only explicit
  // rebase changes the operation; the user's pending requirement stays stable.
  await text.fill("Keep the closing summary concise");
  const external = {outcome_id: "product-tour", requirements: [{id: "concurrent-requirement", description: "Retain a readable title"}],
    source: {ref: "fixture:concurrent-author", summary: "An independent author refinement"}};
  const path = resolve(root, "concurrent.json");
  await writeFile(path, JSON.stringify(external));
  cli("refine", "--agent-id", "author", "--document", path, "--operation-id", "concurrent-author",
    "--expected-provider-revision", cli("inspect").provider_revision, "--execute");
  await save.click();
  await region.getByRole("status").filter({hasText: "Result unconfirmed"}).waitFor();
  await page.waitForFunction(() => !document.querySelector(".goal-outcomes button[type=submit]")?.disabled);
  const rejected = requests.at(-1);
  assert.equal(await text.inputValue(), "Keep the closing summary concise");
  await rebase.click(); await save.click();
  await region.getByRole("status").filter({hasText: "Requirement retained alongside earlier requirements"}).waitFor();
  const rebased = requests.at(-1);
  assert.notEqual(rebased.operation_id, rejected.operation_id);
  assert.notEqual(rebased.expected_provider_revision, rejected.expected_provider_revision);
  assert.deepEqual(rebased.refinement, rejected.refinement);
  assert.equal(outcome().requirements.length, initialCount + 3);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({status: "PASS", boundary: "Packaged UI with real disposable SQLite/HTTP/CLI; synthetic transport failures, no model or installed App",
    checks: ["unsubmitted draft/read failure", "committed write/lost reply/failed reconciliation", "stale basis disables rebase", "same-operation replay without duplication", "native concurrent write/explicit rebase", "390px", "keyboard"]}));
} finally {
  await page?.close(); await browser?.close();
  lines.close(); child.kill("SIGTERM");
  await new Promise(accept => {if (child.exitCode !== null) accept(); else child.once("exit", accept);});
  await rm(root, {recursive: true, force: true});
}
