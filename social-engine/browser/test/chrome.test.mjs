// Pure launch-decision tests: no Chrome or Xvfb process is started.

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { buildChromeLaunch } from "../lib/chrome.mjs";

const base = {
  profileDir: "/tmp/profile",
  port: 9222,
  visibleEnv: undefined,
  baseEnv: { PATH: "/usr/bin", WAYLAND_DISPLAY: "wayland-0" },
};

describe("buildChromeLaunch", () => {
  it("hides Chrome on Linux by default when Xvfb is available", () => {
    const launch = buildChromeLaunch({
      ...base,
      platform: "linux",
      xvfbAvailable: true,
    });

    assert.equal(launch.hidden, true);
    assert.equal(launch.display, ":99");
    assert.ok(launch.args.includes("--user-data-dir=/tmp/profile"));
    assert.ok(launch.args.includes("--remote-debugging-port=9222"));
    assert.ok(launch.args.includes("--ozone-platform=x11"));
    assert.ok(launch.args.includes("--window-size=1400,1000"));
    assert.equal(launch.env.DISPLAY, ":99");
    assert.equal("WAYLAND_DISPLAY" in launch.env, false);
  });

  it("lets SOCIAL_ENGINE_XVFB_DISPLAY override the display", () => {
    const launch = buildChromeLaunch({
      ...base,
      platform: "linux",
      xvfbAvailable: true,
      xvfbDisplay: ":7",
    });

    assert.equal(launch.hidden, true);
    assert.equal(launch.display, ":7");
    assert.equal(launch.env.DISPLAY, ":7");
  });

  it("uses the owner's desktop when visible is true", () => {
    const launch = buildChromeLaunch({
      ...base,
      platform: "linux",
      xvfbAvailable: true,
      visible: true,
    });

    assert.equal(launch.hidden, false);
    assert.equal(launch.display, null);
    assert.equal(launch.args.includes("--ozone-platform=x11"), false);
    assert.equal(launch.args.includes("--window-size=1400,1000"), false);
    assert.equal(launch.env.DISPLAY, undefined);
    assert.equal(launch.env.WAYLAND_DISPLAY, "wayland-0");
  });

  it("uses the owner's desktop when SOCIAL_ENGINE_VISIBLE=1", () => {
    const launch = buildChromeLaunch({
      ...base,
      platform: "linux",
      xvfbAvailable: true,
      visibleEnv: "1",
    });

    assert.equal(launch.hidden, false);
    assert.equal(launch.env.DISPLAY, undefined);
  });

  it("falls back to visible when Xvfb is missing", () => {
    const launch = buildChromeLaunch({
      ...base,
      platform: "linux",
      xvfbAvailable: false,
    });

    assert.equal(launch.hidden, false);
    assert.equal(launch.args.includes("--ozone-platform=x11"), false);
    assert.equal(launch.env.DISPLAY, undefined);
  });

  it("never hides Chrome on non-Linux platforms", () => {
    const launch = buildChromeLaunch({
      ...base,
      platform: "darwin",
      xvfbAvailable: true,
    });

    assert.equal(launch.hidden, false);
    assert.equal(launch.env.DISPLAY, undefined);
    assert.equal(launch.env.WAYLAND_DISPLAY, "wayland-0");
  });
});
