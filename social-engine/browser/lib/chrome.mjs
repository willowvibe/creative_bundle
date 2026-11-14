// Start the owner's system Chrome with a dedicated profile, then attach over CDP.

import fs from "node:fs";
import net from "node:net";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";

import { chromium } from "playwright-core";

const LINUX_NAMES = [
  "google-chrome",
  "google-chrome-stable",
  "chromium",
  "chromium-browser",
];

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

/** True when something already listens on the loopback port. */
export function isListening(port, { host = "127.0.0.1", timeout = 300 } = {}) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      socket.destroy();
      resolve(value);
    };
    const socket = net.connect({ host, port });
    socket.setTimeout(timeout);
    socket.once("connect", () => finish(true));
    socket.once("timeout", () => finish(false));
    socket.once("error", () => finish(false));
  });
}

function which(command) {
  const result = spawnSync("which", [command], { encoding: "utf8" });
  return result.status === 0 ? result.stdout.trim() : null;
}

function xvfbSocket(display) {
  return `/tmp/.X11-unix/X${String(display).replace(/^:/, "")}`;
}

/**
 * Pure launch decision: build Chrome args and env for hidden (Xvfb) or visible
 * mode. No processes are spawned, so this is safe to unit test.
 */
export function buildChromeLaunch({
  profileDir,
  port,
  platform = process.platform,
  visible = false,
  visibleEnv = process.env.SOCIAL_ENGINE_VISIBLE,
  xvfbAvailable = false,
  xvfbDisplay = process.env.SOCIAL_ENGINE_XVFB_DISPLAY || ":99",
  baseEnv = process.env,
} = {}) {
  const hidden =
    platform === "linux" && visible !== true && visibleEnv !== "1" && xvfbAvailable === true;
  const args = [
    `--user-data-dir=${profileDir}`,
    `--remote-debugging-port=${port}`,
    "--no-first-run",
    "--no-default-browser-check",
  ];
  const env = { ...baseEnv };
  if (hidden) {
    args.push("--ozone-platform=x11", "--window-size=1400,1000");
    env.DISPLAY = xvfbDisplay;
    delete env.WAYLAND_DISPLAY;
  }
  return { hidden, display: hidden ? xvfbDisplay : null, args, env };
}

/** Start a detached Xvfb on the display and wait for its socket to appear. */
async function ensureXvfb(display, { timeout = 5000, interval = 100 } = {}) {
  const socket = xvfbSocket(display);
  if (!fs.existsSync(socket)) {
    const child = spawn(
      "Xvfb",
      [display, "-screen", "0", "1400x1000x24", "-nolisten", "tcp"],
      { detached: true, stdio: "ignore" },
    );
    child.unref();
  }
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (fs.existsSync(socket)) return true;
    await delay(interval);
  }
  throw new Error(`Xvfb did not open ${socket} within ${timeout} ms`);
}

/** Find a usable Chrome/Chromium binary, honouring CHROME_PATH first. */
export function findChrome() {
  const candidates = [];
  if (process.env.CHROME_PATH) candidates.push(process.env.CHROME_PATH);

  if (process.platform === "linux") {
    for (const name of LINUX_NAMES) {
      const found = which(name);
      if (found) candidates.push(found);
    }
  } else if (process.platform === "darwin") {
    candidates.push(
      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
      "/Applications/Chromium.app/Contents/MacOS/Chromium",
    );
  } else if (process.platform === "win32") {
    for (const base of [process.env.PROGRAMFILES, process.env["PROGRAMFILES(X86)"], process.env.LOCALAPPDATA]) {
      if (base) {
        candidates.push(
          path.join(base, "Google", "Chrome", "Application", "chrome.exe"),
        );
      }
    }
  }

  for (const candidate of candidates) {
    if (candidate && fs.existsSync(candidate)) return candidate;
  }
  throw new Error(
    "Chrome was not found; install Google Chrome or set CHROME_PATH to its executable",
  );
}

export async function waitForPort(port, { timeout = 15000, interval = 100 } = {}) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await isListening(port)) return true;
    await delay(interval);
  }
  throw new Error(`Chrome did not open the debugging port ${port} within ${timeout} ms`);
}

/**
 * Ensure a Chrome with this profile is listening on the CDP port.
 * Returns { started, port, pid, hidden }.
 */
export async function ensureChrome({ profileDir, port, visible = false }) {
  if (!profileDir) throw new Error("profileDir is required");
  const numericPort = Number(port);
  if (!Number.isInteger(numericPort) || numericPort <= 0) {
    throw new Error(`invalid CDP port: ${port}`);
  }
  if (await isListening(numericPort)) {
    return { started: false, port: numericPort, pid: null, hidden: null };
  }

  fs.mkdirSync(profileDir, { recursive: true, mode: 0o700 });
  const binary = findChrome();
  const launch = buildChromeLaunch({
    profileDir,
    port: numericPort,
    visible,
    xvfbAvailable: which("Xvfb") !== null,
  });
  if (launch.hidden) await ensureXvfb(launch.display);
  const child = spawn(binary, launch.args, {
    detached: true,
    stdio: "ignore",
    env: launch.env,
  });
  child.unref();
  await waitForPort(numericPort);
  return { started: true, port: numericPort, pid: child.pid ?? null, hidden: launch.hidden };
}

/** Connect over CDP and return a page on startUrl, reusing an existing tab. */
export async function attach(port, startUrl) {
  const browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
  const contexts = browser.contexts();
  const context = contexts[0] ?? (await browser.newContext());
  const pages = context.pages();
  const existing = startUrl
    ? pages.find((page) => page.url().startsWith(startUrl))
    : pages[0];
  const page = existing ?? (await context.newPage());
  if (startUrl && !page.url().startsWith(startUrl)) {
    await page.goto(startUrl, { waitUntil: "domcontentloaded" });
  }
  return page;
}
