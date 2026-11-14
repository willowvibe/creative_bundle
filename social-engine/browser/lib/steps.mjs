// Small, reusable browser steps: screen assertions, hand-off detection, stops.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const HANDOFF = Object.freeze({
  SIGN_IN: "google-sign-in",
  CAPTCHA: "captcha",
  VERIFY: "verify-it-s-you",
  ACCOUNT_CHOOSER: "account-chooser",
});

export function makeResult(
  status,
  { summary = {}, id = null, screenshot = null, message = "" } = {},
) {
  return { status, summary, id, screenshot, message };
}

function describeCheck(check) {
  if (typeof check === "string") return `text "${check}"`;
  if (check.role) return `role "${check.role}" named "${check.name ?? ""}"`;
  if (check.label) return `label "${check.label}"`;
  if (check.text) return `text "${check.text}"`;
  return JSON.stringify(check);
}

function locatorFor(page, check) {
  if (typeof check === "string") {
    return page.getByText(check, { exact: false });
  }
  if (check.role) {
    return page.getByRole(check.role, {
      name: check.name,
      exact: check.exact ?? false,
    });
  }
  if (check.label) {
    return page.getByLabel(check.label, { exact: check.exact ?? false });
  }
  if (check.text) {
    return page.getByText(check.text, { exact: check.exact ?? false });
  }
  throw new Error(`unsupported screen check: ${JSON.stringify(check)}`);
}

/** Wait for every role/label/text check; throw with the screen name on failure. */
export async function expectScreen(page, name, checks, { timeout = 15000 } = {}) {
  if (!Array.isArray(checks) || checks.length === 0) {
    throw new Error(`expected screen "${name}" has no checks`);
  }
  for (const check of checks) {
    try {
      await locatorFor(page, check).first().waitFor({ state: "visible", timeout });
    } catch {
      throw new Error(
        `expected screen "${name}": ${describeCheck(check)} was not visible within ${timeout} ms`,
      );
    }
  }
}

async function isVisible(locator, timeout = 250) {
  try {
    return await locator.first().isVisible({ timeout });
  } catch {
    return false;
  }
}

/** Return a hand-off reason, or null when the page looks like the platform itself. */
export async function detectHandoff(page) {
  if (page.url().includes("accounts.google.com")) {
    return HANDOFF.SIGN_IN;
  }
  if (await isVisible(page.getByText(/captcha|recaptcha|verify you are human/i))) {
    return HANDOFF.CAPTCHA;
  }
  if (await isVisible(page.getByText(/verify it'?s you/i))) {
    return HANDOFF.VERIFY;
  }
  if (
    await isVisible(
      page.getByText(/choose an account|select an account|use another account/i),
    )
  ) {
    return HANDOFF.ACCOUNT_CHOOSER;
  }
  return null;
}

export function logsDir() {
  const home =
    process.env.SOCIAL_ENGINE_HOME ||
    path.join(os.homedir(), ".config", "social-engine");
  return path.join(home, "logs");
}

function safeName(value) {
  return String(value).replace(/[^A-Za-z0-9._-]+/g, "-");
}

export async function takeScreenshot(page, label) {
  const directory = logsDir();
  fs.mkdirSync(directory, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const file = path.join(directory, `${stamp}-${safeName(label)}.png`);
  await page.screenshot({ path: file, fullPage: true });
  return file;
}

/** Save a full-page screenshot and return the BrowserResult for an error stop. */
export async function stopUnexpected(page, step, detail = "") {
  const screenshot = await takeScreenshot(page, `unexpected-${step}`);
  return makeResult("error", {
    summary: { step },
    screenshot,
    message: detail
      ? `unexpected screen at ${step}: ${detail}`
      : `unexpected screen at ${step}`,
  });
}

/** Save a full-page screenshot and return the BrowserResult for an owner hand-off. */
export async function stopForUser(page, reason) {
  const screenshot = await takeScreenshot(page, `needs-user-${reason}`);
  return makeResult("needs_user", {
    summary: { reason },
    screenshot,
    message: `the owner must complete: ${reason}`,
  });
}
