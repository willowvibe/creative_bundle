#!/usr/bin/env node
// The browser lane CLI: one JSON BrowserResult on stdout, one command per run.

import fs from "node:fs";

import { attach, ensureChrome } from "./lib/chrome.mjs";
import { detectHandoff, makeResult, stopForUser } from "./lib/steps.mjs";

function parseArgs(argv) {
  const positional = [];
  let payloadPath = null;
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--payload") {
      payloadPath = argv[index + 1] ?? null;
      index += 1;
      continue;
    }
    if (argument.startsWith("--payload=")) {
      payloadPath = argument.slice("--payload=".length);
      continue;
    }
    if (argument.startsWith("--")) continue;
    positional.push(argument);
  }
  return { platform: positional[0] ?? null, command: positional[1] ?? null, payloadPath };
}

function readPayload(file) {
  if (!file) return {};
  const data = JSON.parse(fs.readFileSync(file, "utf8"));
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    throw new Error("payload must be a JSON object");
  }
  return data;
}

async function loadPlatform(platform) {
  if (!/^[a-z0-9-]+$/.test(platform)) {
    throw new Error(`invalid platform: ${platform}`);
  }
  const url = new URL(`./platforms/${platform}/index.mjs`, import.meta.url);
  try {
    return await import(url.href);
  } catch (error) {
    throw new Error(
      `cannot load ${platform} browser flows: ${error instanceof Error ? error.message : error}`,
    );
  }
}

function commandName(command) {
  return command.replace(/-([a-z0-9])/g, (_match, letter) => letter.toUpperCase());
}

async function openPage(platformModule, payload, { visible = false } = {}) {
  const port = Number(payload.cdp_port);
  if (!Number.isInteger(port) || port <= 0) {
    throw new Error("payload.cdp_port is required");
  }
  if (!payload.chrome_profile_dir) {
    throw new Error("payload.chrome_profile_dir is required");
  }
  const launched = await ensureChrome({
    profileDir: payload.chrome_profile_dir,
    port,
    visible,
  });
  const page = await attach(port, platformModule.startUrl ?? payload.start_url);
  return { page, hidden: launched.hidden === true };
}

const HIDDEN_HANDOFF_HINT =
  "Close the hidden Chrome and rerun with SOCIAL_ENGINE_VISIBLE=1 (or `social login`) to act on the screen.";

async function stopForUserPage(page, reason, hidden) {
  const result = await stopForUser(page, reason);
  if (hidden && result.status === "needs_user") {
    result.message = `${result.message} ${HIDDEN_HANDOFF_HINT}`;
  }
  return result;
}

async function login(platform, platformModule, payload) {
  const { page, hidden } = await openPage(platformModule, payload, { visible: true });
  const handoff = await detectHandoff(page);
  if (handoff) return stopForUserPage(page, handoff, hidden);
  if (typeof platformModule.isSignedIn !== "function") {
    throw new Error(`${platform} has no isSignedIn(page)`);
  }
  if (!(await platformModule.isSignedIn(page))) {
    return stopForUserPage(page, "sign-in", hidden);
  }
  const id =
    typeof platformModule.readAccountId === "function"
      ? await platformModule.readAccountId(page)
      : null;
  return makeResult("done", {
    summary: { platform, signed_in: true },
    id,
    message: "signed in",
  });
}

async function status(platform, platformModule, payload) {
  const { page } = await openPage(platformModule, payload);
  const handoff = await detectHandoff(page);
  const signedIn =
    typeof platformModule.isSignedIn === "function"
      ? await platformModule.isSignedIn(page)
      : false;
  const id =
    signedIn && typeof platformModule.readAccountId === "function"
      ? await platformModule.readAccountId(page)
      : null;
  return makeResult("done", {
    summary: { platform, signed_in: signedIn, handoff },
    id,
    message: signedIn
      ? "signed in"
      : handoff
        ? `needs user: ${handoff}`
        : "not signed in",
  });
}

async function main() {
  const { platform, command, payloadPath } = parseArgs(process.argv.slice(2));
  if (!platform || !command) {
    throw new Error(
      "usage: node browser/driver.mjs <platform> <command> [--payload <file>]",
    );
  }
  const payload = readPayload(payloadPath);
  const platformModule = await loadPlatform(platform);

  if (command === "login") return login(platform, platformModule, payload);
  if (command === "status") return status(platform, platformModule, payload);

  const handler =
    platformModule.commands?.[command] ?? platformModule[commandName(command)];
  if (typeof handler !== "function") {
    throw new Error(`${platform} does not implement the ${command} command`);
  }
  const { page, hidden } = await openPage(platformModule, payload);
  const handoff = await detectHandoff(page);
  if (handoff) return stopForUserPage(page, handoff, hidden);
  const result = await handler({ page, payload, platform });
  if (!result || typeof result !== "object") {
    throw new Error(`${platform} ${command} returned no BrowserResult`);
  }
  return result;
}

main()
  .then((result) => {
    process.stdout.write(`${JSON.stringify(result)}\n`, () => process.exit(0));
  })
  .catch((error) => {
    process.stdout.write(
      `${JSON.stringify(
        makeResult("error", {
          message: error instanceof Error ? error.message : String(error),
        }),
      )}\n`,
      () => process.exit(0),
    );
  });
