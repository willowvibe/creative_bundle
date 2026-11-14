// YouTube Studio flow tests against simplified local HTML fixtures.

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { after, before, describe, it } from "node:test";

import { chromium } from "playwright-core";

import { addDubs, confirm, isSignedIn, readAccountId, upload } from "../platforms/youtube/index.mjs";

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

function withStateHome(test) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "social-engine-youtube-"));
  const previous = process.env.SOCIAL_ENGINE_HOME;
  process.env.SOCIAL_ENGINE_HOME = home;
  test.after(() => {
    if (previous === undefined) delete process.env.SOCIAL_ENGINE_HOME;
    else process.env.SOCIAL_ENGINE_HOME = previous;
    fs.rmSync(home, { recursive: true, force: true });
  });
  return home;
}

describe("youtube studio flows", () => {
  let browser;
  let mediaDir;
  let videoPath;
  let thumbnailPath;
  let audioPath;
  let studioHtml;
  let translationsHtml;
  let dashboardHtml;
  let signinHtml;

  before(async () => {
    browser = await chromium.launch(launchOptions());
    mediaDir = fs.mkdtempSync(path.join(os.tmpdir(), "social-engine-media-"));
    videoPath = path.join(mediaDir, "final.mp4");
    thumbnailPath = path.join(mediaDir, "thumbnail.jpg");
    audioPath = path.join(mediaDir, "hi.m4a");
    fs.writeFileSync(videoPath, "video");
    fs.writeFileSync(thumbnailPath, "image");
    fs.writeFileSync(audioPath, "audio");
    studioHtml = fs.readFileSync(fixture("youtube/studio.html"), "utf8");
    translationsHtml = fs.readFileSync(fixture("youtube/translations.html"), "utf8");
    dashboardHtml = fs.readFileSync(fixture("dashboard.html"), "utf8");
    signinHtml = fs.readFileSync(fixture("signin.html"), "utf8");
  });

  after(async () => {
    await browser?.close();
    fs.rmSync(mediaDir, { recursive: true, force: true });
  });

  async function newPage(test) {
    const page = await browser.newPage();
    test.after(() => page.close());
    return page;
  }

  function payload(overrides = {}) {
    return {
      account: "mantra-shakti",
      video: videoPath,
      title: "A title",
      description: "A description",
      thumbnail: thumbnailPath,
      playlists: ["Mantra Rahasyam"],
      made_for_kids: false,
      synthetic_media: true,
      language: "te",
      privacy: "private",
      publish_at: null,
      ...overrides,
    };
  }

  it("reads sign-in state and the channel id", async (test) => {
    const page = await newPage(test);
    await page.setContent(studioHtml);
    assert.equal(await isSignedIn(page), true);
    await page.setContent("<main><h1>Unknown</h1></main>");
    assert.equal(await isSignedIn(page), false);

    await page.route("https://studio.youtube.com/channel/UC123", (route) =>
      route.fulfill({ status: 200, contentType: "text/html", body: dashboardHtml }),
    );
    await page.goto("https://studio.youtube.com/channel/UC123");
    assert.equal(await readAccountId(page), "UC123");
  });

  it("uploads through details and visibility without clicking the final button", async (test) => {
    const page = await newPage(test);
    await page.setContent(studioHtml);

    const result = await upload({ page, payload: payload() });

    assert.equal(result.status, "ready_to_confirm");
    assert.equal(result.id, "studio-video-1");
    assert.equal(result.summary.privacy, "private");
    assert.equal(result.summary.final_button, "Save");
    assert.equal(result.summary.video_url, "https://youtu.be/studio-video-1");
    assert.deepEqual(result.summary.playlists, ["Mantra Rahasyam"]);

    const actions = await page.evaluate(() => window.__actions);
    assert.deepEqual(actions, [
      "create",
      "upload-videos",
      "file-selected",
      "show-more",
      "next",
      "visibility-private",
    ]);
    assert.ok(!actions.includes("final-clicked"));

    const state = await page.evaluate(() => window.__state);
    assert.equal(state.title, "A title");
    assert.equal(state.description, "A description");
    assert.equal(state.videoName, "final.mp4");
    assert.equal(state.thumbnailName, "thumbnail.jpg");
    assert.deepEqual(state.playlists, ["Mantra Rahasyam"]);
    assert.equal(state.madeForKids, "no");
    assert.equal(state.synthetic, "yes");
    assert.equal(state.language, "te");
    assert.equal(state.privacy, "private");
  });

  it("schedules an upload and never clicks Schedule during the upload call", async (test) => {
    const page = await newPage(test);
    await page.setContent(studioHtml);

    const result = await upload({
      page,
      payload: payload({ publish_at: "2099-01-01T00:00" }),
    });

    assert.equal(result.status, "ready_to_confirm");
    assert.equal(result.summary.privacy, "schedule");
    assert.equal(result.summary.publish_at, "2099-01-01T00:00");
    assert.equal(result.summary.final_button, "Schedule");
    const actions = await page.evaluate(() => window.__actions);
    assert.ok(actions.includes("visibility-schedule"));
    assert.ok(!actions.includes("final-clicked"));
    const state = await page.evaluate(() => window.__state);
    assert.equal(state.scheduleDate, "2099-01-01T00:00");
  });

  it("confirms only when the open video id matches", async (test) => {
    const page = await newPage(test);
    await page.setContent(studioHtml);
    const uploaded = await upload({ page, payload: payload() });

    const done = await confirm({
      page,
      payload: { id: uploaded.id, expect: "publish" },
    });

    assert.equal(done.status, "done");
    assert.equal(done.summary.button, "Save");
    const actions = await page.evaluate(() => window.__actions);
    assert.ok(actions.includes("final-clicked"));
    assert.equal(await page.locator("#done").innerText(), "Video saved");
  });

  it("refuses to confirm a different video id and saves a screenshot", async (test) => {
    const home = withStateHome(test);
    const page = await newPage(test);
    await page.setContent(studioHtml);
    await upload({ page, payload: payload() });

    const result = await confirm({
      page,
      payload: { id: "some-other-video", expect: "publish" },
    });

    assert.equal(result.status, "error");
    assert.match(result.message, /expected video some-other-video/);
    assert.ok(result.screenshot.startsWith(path.join(home, "logs")));
    assert.ok(fs.statSync(result.screenshot).size > 0);
  });

  it("adds a missing language and dub without publishing", async (test) => {
    const page = await newPage(test);
    await page.route("https://studio.youtube.com/video/**/translations", (route) =>
      route.fulfill({ status: 200, contentType: "text/html", body: translationsHtml }),
    );

    const result = await addDubs({
      page,
      payload: { id: "studio-video-1", dubs: { hi: audioPath } },
    });

    assert.equal(result.status, "ready_to_confirm");
    assert.equal(result.id, "studio-video-1");
    assert.deepEqual(result.summary.dubs, ["hi"]);
    const actions = await page.evaluate(() => window.__actions);
    assert.deepEqual(actions, [
      "language-added:hi",
      "add-dub:hi",
      "dub-uploaded:hi",
    ]);
    assert.ok(!actions.includes("final-clicked"));
    const state = await page.evaluate(() => window.__state);
    assert.deepEqual(state.dubs, [{ language: "hi", name: "hi.m4a" }]);

    const done = await confirm({
      page,
      payload: { id: "studio-video-1", expect: "dubs" },
    });
    assert.equal(done.status, "done");
    assert.equal(done.summary.action, "dubs");
    const actionsAfter = await page.evaluate(() => window.__actions);
    assert.ok(actionsAfter.includes("final-clicked"));
  });

  it("hands sign-in back to the owner with a screenshot", async (test) => {
    const home = withStateHome(test);
    const page = await newPage(test);
    await page.setContent(signinHtml);

    const result = await upload({ page, payload: payload() });

    assert.equal(result.status, "needs_user");
    assert.deepEqual(result.summary, { reason: "account-chooser" });
    assert.ok(result.screenshot.startsWith(path.join(home, "logs")));
    assert.ok(fs.statSync(result.screenshot).size > 0);
  });
});
