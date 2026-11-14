// Browser step tests against local HTML fixtures in headless Chromium.

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { after, before, describe, it } from "node:test";

import { chromium } from "playwright-core";

import {
  HANDOFF,
  detectHandoff,
  expectScreen,
  stopForUser,
  stopUnexpected,
} from "../lib/steps.mjs";

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

function fixture(name) {
  return new URL(`./fixtures/${name}`, import.meta.url);
}

async function loadFixture(page, name) {
  await page.setContent(fs.readFileSync(fixture(name), "utf8"));
}

describe("browser steps", () => {
  let browser;

  before(async () => {
    browser = await chromium.launch(launchOptions());
  });

  after(async () => {
    await browser?.close();
  });

  async function newPage(test) {
    const page = await browser.newPage();
    test.after(() => page.close());
    return page;
  }

  it("expectScreen accepts role, label and text checks", async (test) => {
    const page = await newPage(test);
    await loadFixture(page, "dashboard.html");

    await expectScreen(
      page,
      "dashboard",
      [
        { role: "button", name: "Create" },
        { label: "Search" },
        { text: "Your channel" },
      ],
      { timeout: 2000 },
    );
  });

  it("expectScreen names the screen when a check is missing", async (test) => {
    const page = await newPage(test);
    await loadFixture(page, "unknown.html");

    await assert.rejects(
      () => expectScreen(page, "dashboard", [{ text: "Your channel" }], { timeout: 250 }),
      /expected screen "dashboard".*Your channel/,
    );
  });

  it("detectHandoff returns null on a normal screen", async (test) => {
    const page = await newPage(test);
    await loadFixture(page, "dashboard.html");

    assert.equal(await detectHandoff(page), null);
  });

  it("detectHandoff recognizes a Google sign-in URL", async (test) => {
    const page = await newPage(test);
    await page.route("https://accounts.google.com/**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "text/html",
        body: "<html><body><h1>Sign in</h1></body></html>",
      }),
    );
    await page.goto("https://accounts.google.com/signin");

    assert.equal(await detectHandoff(page), HANDOFF.SIGN_IN);
  });

  it("detectHandoff recognizes a CAPTCHA", async (test) => {
    const page = await newPage(test);
    await page.setContent("<main><p>Please complete the reCAPTCHA</p></main>");

    assert.equal(await detectHandoff(page), HANDOFF.CAPTCHA);
  });

  it("detectHandoff recognizes 'Verify it's you'", async (test) => {
    const page = await newPage(test);
    await page.setContent("<main><h1>Verify it's you</h1></main>");

    assert.equal(await detectHandoff(page), HANDOFF.VERIFY);
  });

  it("detectHandoff recognizes an account chooser", async (test) => {
    const page = await newPage(test);
    await loadFixture(page, "signin.html");

    assert.equal(await detectHandoff(page), HANDOFF.ACCOUNT_CHOOSER);
  });

  it("stopUnexpected writes a full-page screenshot", async (test) => {
    const page = await newPage(test);
    await loadFixture(page, "unknown.html");
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "social-engine-browser-"));
    const previous = process.env.SOCIAL_ENGINE_HOME;
    process.env.SOCIAL_ENGINE_HOME = home;
    try {
      const result = await stopUnexpected(page, "details");

      assert.equal(result.status, "error");
      assert.deepEqual(result.summary, { step: "details" });
      assert.match(result.message, /unexpected screen at details/);
      assert.ok(result.screenshot.startsWith(path.join(home, "logs")));
      assert.ok(fs.statSync(result.screenshot).size > 0);
    } finally {
      if (previous === undefined) delete process.env.SOCIAL_ENGINE_HOME;
      else process.env.SOCIAL_ENGINE_HOME = previous;
      fs.rmSync(home, { recursive: true, force: true });
    }
  });

  it("stopForUser writes a screenshot and a needs_user result", async (test) => {
    const page = await newPage(test);
    await loadFixture(page, "signin.html");
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "social-engine-browser-"));
    const previous = process.env.SOCIAL_ENGINE_HOME;
    process.env.SOCIAL_ENGINE_HOME = home;
    try {
      const result = await stopForUser(page, "sign-in");

      assert.equal(result.status, "needs_user");
      assert.deepEqual(result.summary, { reason: "sign-in" });
      assert.match(result.message, /sign-in/);
      assert.ok(result.screenshot.startsWith(path.join(home, "logs")));
      assert.ok(fs.statSync(result.screenshot).size > 0);
    } finally {
      if (previous === undefined) delete process.env.SOCIAL_ENGINE_HOME;
      else process.env.SOCIAL_ENGINE_HOME = previous;
      fs.rmSync(home, { recursive: true, force: true });
    }
  });
});
