// End-to-end UI test: real UI + real worker, with a fake internet behind the worker.
// Run from the repo root:  node tests/e2e/e2e.mjs
// Env: PYTHON (python with the worker installed), PW_CHROMIUM (optional browser path).
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const require = createRequire(path.join(root, "app/package.json"));
const { chromium } = require("playwright");
const out = path.join(root, "tests/e2e/out");
mkdirSync(out, { recursive: true });

const python = process.env.PYTHON || "python";
const dataDir = mkdtempSync(path.join(tmpdir(), "forge-e2e-"));
writeFileSync(path.join(root, "app/.env.local"), "VITE_WORKER_URL=http://127.0.0.1:8765\nVITE_WORKER_TOKEN=e2e-token\n");

const procs = [
  spawn(python, ["-m", "tests.e2e.fake_worker", dataDir], { cwd: root, stdio: "inherit" }),
  spawn("npx", ["vite", "--port", "1420", "--strictPort"], { cwd: path.join(root, "app"), stdio: "ignore", shell: process.platform === "win32" }),
];
const stop = () => procs.forEach((p) => p.kill());

async function waitFor(url) {
  for (let i = 0; i < 120; i++) {
    try {
      if ((await fetch(url)).ok) return;
    } catch {}
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`not reachable: ${url}`);
}

function check(cond, what) {
  if (!cond) throw new Error(`FAILED: ${what}`);
  console.log(`ok - ${what}`);
}

async function main() {
  await waitFor("http://127.0.0.1:8765/health");
  await waitFor("http://localhost:1420");
  const browser = await chromium.launch(process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {});
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
  const shot = (name) => page.screenshot({ path: path.join(out, `${name}.png`) });
  await page.goto("http://localhost:1420");
  await page.getByText("Engine ready").waitFor({ timeout: 30000 });
  await page.waitForFunction(() => window.__map && window.__map.getSource("draft"), null, { timeout: 30000 });
  const map = page.getByTestId("map");
  const box = await map.boundingBox();
  const at = (fx, fy) => ({ position: { x: box.width * fx, y: box.height * fy } });
  const center = () => page.evaluate(() => [window.__map.getCenter().lng, window.__map.getCenter().lat, window.__map.getZoom()]);

  // 1. a country-sized box is refused with a clear message
  await map.click(at(0.3, 0.3));
  check((await page.getByTestId("hint").innerText()).includes("opposite corner"), "hint guides to the second corner");
  await map.click(at(0.6, 0.7));
  await page.getByRole("alert").first().waitFor();
  check((await page.getByRole("alert").first().innerText()).includes("limit"), "too-large area shows the size limit");
  check(await page.getByRole("button", { name: "Build it" }).isDisabled(), "Build is disabled for an invalid selection");
  await shot("1-too-big");

  // 2. coordinates search flies to Prague
  await page.getByLabel("Search the map").fill("50.0755, 14.4378");
  await page.getByRole("button", { name: "Go" }).click();
  await page.waitForFunction(() => window.__map.getZoom() > 13.9 && !window.__map.isMoving(), null, { timeout: 15000 });
  const c = await center();
  check(Math.abs(c[0] - 14.4378) < 0.01 && Math.abs(c[1] - 50.0755) < 0.01, "lat, lon search moves the map to Prague");

  // 3. draw a ~1.5 km box: readout + national data sources
  await page.keyboard.press("Escape");
  await map.click(at(0.4, 0.35));
  await map.click(at(0.6, 0.65));
  await page.getByTestId("readout").waitFor();
  const readout = await page.getByTestId("readout").innerText();
  check(/km²/.test(readout) && /MB/.test(readout) && /min/.test(readout), "readout shows area, download size and time");
  const quality = await page.getByTestId("quality").innerText();
  check(quality.includes("ČÚZK DMR 5G") && quality.includes("2 m") && quality.includes("50 cm"), "Czech area uses LiDAR 2 m and 50 cm photo");
  check(quality.includes("OpenStreetMap") && quality.includes("ODbL"), "licence badges are shown");
  check(await page.getByRole("button", { name: "Build it" }).isDisabled(), "Build needs a name first");

  // 4. name it, pick both games, build, watch it finish
  await page.getByLabel("Map name").fill("Praha Hradčany");
  await page.getByText("praha_hradcany").waitFor();
  check(true, "name is turned into a game-safe folder name");
  await page.getByLabel("BeamNG.drive").check();
  await page.getByRole("button", { name: "Build it" }).click();
  await page.locator(".job").first().waitFor();
  await shot("2-area-building");
  await page.locator(".job .badge-done").first().waitFor({ timeout: 60000 });
  await page.getByRole("button", { name: "Show log" }).first().click();
  await page.getByText(/reused .* MB from the cache|Downloaded/).first().waitFor();
  const log = await page.locator(".log").innerText();
  check(log.includes("ČÚZK DMR 5G") && log.includes("OpenStreetMap"), "build log names the data sources used");
  await shot("3-area-done");
  check(log.includes("OK: Terrain never pokes through the road") && !log.includes("PROBLEM"), "build log shows the drivability checks passing");

  // 4b. the finished build opens in 3D with its drivability report
  await page.getByRole("button", { name: "View in 3D" }).first().click();
  await page.locator('.preview canvas[data-ready="true"]').waitFor({ timeout: 60000 });
  const triangles = Number(await page.locator(".preview canvas").getAttribute("data-triangles"));
  check(triangles > 20000, `3D preview loaded the model (${triangles} triangles)`);
  await page.waitForFunction(() => window.__preview.drawn() > 20000, null, { timeout: 30000 });
  check(true, "3D preview is really drawing roads and terrain");
  check((await page.locator(".verdict").innerText()).includes("all checks passed"), "report says the map is drivable");
  check((await page.locator(".checks li").count()) >= 8, "report lists every check");
  check((await page.locator(".repairs").innerText()).length > 20, "report lists what was repaired");
  await page.waitForTimeout(800);
  await shot("3b-preview-3d");
  await page.keyboard.press("Escape");
  await page.locator(".preview").waitFor({ state: "detached" });
  check((await page.getByTestId("readout").count()) === 1, "closing the preview keeps the drawing");

  // 5. undo and clear
  await page.getByRole("button", { name: "Clear" }).click();
  check((await page.getByTestId("hint").innerText()).includes("one corner"), "Clear resets the drawing");

  // 6. free shape: three clicks + Enter
  await page.getByRole("button", { name: "Free shape" }).click();
  await map.click(at(0.4, 0.4));
  await map.click(at(0.6, 0.4));
  await map.click(at(0.5, 0.6));
  await page.keyboard.press("Enter");
  await page.getByTestId("readout").waitFor();
  check(/km²/.test(await page.getByTestId("readout").innerText()), "free shape closes with Enter and is measured");

  // 7. road mode: two clicks, route follows roads, width slider changes the area
  await page.getByRole("button", { name: "Road A to B" }).click();
  await map.click(at(0.3, 0.5));
  await map.click(at(0.7, 0.5));
  await page.getByText("Road length").waitFor();
  const before = await page.getByTestId("readout").innerText();
  await page.getByRole("slider").fill("600");
  await page.waitForFunction((b) => document.querySelector('[data-testid="readout"]')?.innerText !== b, before);
  check((await page.locator(".slider").innerText()).includes("600 m"), "width slider updates the corridor");
  await shot("4-road");

  // 8. place search by name
  await page.getByLabel("Search the map").fill("brno");
  await page.getByRole("button", { name: "Go" }).click();
  await page.waitForFunction(() => Math.abs(window.__map.getCenter().lng - 16.6) < 0.2 && !window.__map.isMoving(), null, { timeout: 15000 });
  check(true, "place search moves the map to Brno");

  await browser.close();
  console.log("\nALL E2E CHECKS PASSED");
}

main()
  .then(() => { stop(); process.exit(0); })
  .catch((err) => { console.error(err); stop(); process.exit(1); });
