import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { after, before, describe, it } from "node:test";

import { chromium } from "playwright-core";

import { PLAYWRIGHT_TRANSFER_LIMIT, setLocalFiles } from "../lib/files.mjs";

function launchOptions() {
  const candidates = [
    process.env.CHROME_PATH,
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
  ];
  for (const candidate of candidates) {
    if (candidate && fs.existsSync(candidate)) {
      return { headless: true, executablePath: candidate };
    }
  }
  return { headless: true };
}

const SHADOW_INPUT = `<div id="host"></div><script>
  const root = document.getElementById("host").attachShadow({ mode: "open" });
  root.innerHTML = '<input type="file" aria-label="Pick">';
</script>`;

async function chosenFile(page) {
  return page.evaluate(() => {
    const input = document.getElementById("host").shadowRoot.querySelector("input");
    const file = input.files[0];
    return { name: file?.name, size: file?.size, marked: input.hasAttribute("data-social-engine-upload") };
  });
}

describe("setLocalFiles", () => {
  let browser;
  let dir;

  before(async () => {
    browser = await chromium.launch(launchOptions());
    dir = fs.mkdtempSync(path.join(os.tmpdir(), "se-files-"));
  });

  after(async () => {
    await browser?.close();
    fs.rmSync(dir, { recursive: true, force: true });
  });

  it("uses setInputFiles for small files", async () => {
    const page = await browser.newPage();
    await page.setContent(SHADOW_INPUT);
    const small = path.join(dir, "small.m4a");
    fs.writeFileSync(small, "x".repeat(1024));
    await setLocalFiles(page, page.getByLabel("Pick"), small);
    assert.deepEqual(await chosenFile(page), { name: "small.m4a", size: 1024, marked: false });
    await page.close();
  });

  it("hands files over the transfer limit to a shadow-root input by path", async () => {
    const page = await browser.newPage();
    await page.setContent(SHADOW_INPUT);
    const big = path.join(dir, "big.mp4");
    const size = PLAYWRIGHT_TRANSFER_LIMIT + 1024 * 1024;
    fs.closeSync(fs.openSync(big, "w"));
    fs.truncateSync(big, size);
    await setLocalFiles(page, page.getByLabel("Pick"), big);
    assert.deepEqual(await chosenFile(page), { name: "big.mp4", size, marked: false });
    await page.close();
  });
});
