// YouTube Studio browser flows.
//
// The labels and roles below are exercised against simplified HTML fixtures and
// were re-checked against live Studio on 2026-11-03 (see docs/live-verification.md).
// Studio changes its UI without notice; re-check before changing this flow.

import {
  detectHandoff,
  expectScreen,
  makeResult,
  stopForUser,
  stopUnexpected,
} from "../../lib/steps.mjs";
import { setLocalFiles } from "../../lib/files.mjs";

export const startUrl = "https://studio.youtube.com/";

const FINAL_BUTTON = /^(Publish|Save|Schedule)$/;

class Handoff extends Error {
  constructor(reason) {
    super(reason);
    this.reason = reason;
  }
}

class Unexpected extends Error {
  constructor(step, detail) {
    super(detail);
    this.step = step;
  }
}

async function checkHandoff(page) {
  const reason = await detectHandoff(page);
  if (reason) throw new Handoff(reason);
}

async function screen(page, name, checks, step, scope = page) {
  await checkHandoff(page);
  try {
    await expectScreen(scope, name, checks);
  } catch (error) {
    throw new Unexpected(step ?? name, error instanceof Error ? error.message : String(error));
  }
}

async function editorScope(page) {
  const dialog = page.locator("ytcp-uploads-dialog").first();
  if ((await dialog.count()) === 0) {
    await dialog.waitFor({ state: "visible", timeout: 500 }).catch(() => {});
  }
  return (await dialog.count()) > 0 ? dialog : page;
}

async function guarded(page, body) {
  try {
    return await body();
  } catch (error) {
    if (error instanceof Handoff) return stopForUser(page, error.reason);
    if (error instanceof Unexpected) {
      return stopUnexpected(page, error.step, error.message);
    }
    throw error;
  }
}

function finalButton(scope) {
  return scope.getByRole("button", { name: FINAL_BUTTON }).first();
}

async function readVideoUrl(page) {
  const link = page.getByRole("link", { name: "Video link", includeHidden: true });
  if ((await link.count()) === 0) return null;
  return link.first().getAttribute("href");
}

async function readVideoId(page) {
  const href = await readVideoUrl(page);
  if (href) {
    const short = href.match(/youtu\.be\/([^/?#]+)/);
    if (short) return short[1];
    const watch = href.match(/[?&]v=([^&]+)/);
    if (watch) return watch[1];
  }

  // Live Studio shows a "Copy video link" button instead of visible <a> link.
  const copy = page.getByRole("button", { name: "Copy video link" }).first();
  if ((await copy.count()) > 0) {
    try {
      await page
        .context()
        .grantPermissions(["clipboard-read", "clipboard-write"])
        .catch(() => {});
      await copy.click();
      const text = await page.evaluate(() => navigator.clipboard.readText());
      const match =
        text.match(/youtu\.be\/([^/?#]+)/) || text.match(/[?&]v=([^&]+)/);
      if (match) return match[1];
    } catch {
      // fall through to the DOM/URL checks
    }
  }

  const html = await page.content().catch(() => "");
  const htmlMatch =
    html.match(/youtu\.be\/([A-Za-z0-9_-]{11})/) ||
    html.match(/"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"/);
  if (htmlMatch) return htmlMatch[1];

  const path = page.url().match(/\/video\/([^/?#]+)/);
  return path ? path[1] : null;
}

export async function isSignedIn(page) {
  try {
    await page.waitForLoadState("domcontentloaded", { timeout: 15000 }).catch(() => {});
    return await page
      .getByRole("button", { name: "Create" })
      .first()
      .isVisible({ timeout: 15000 });
  } catch {
    return false;
  }
}

export async function readAccountId(page) {
  const match = page.url().match(/\/channel\/([^/?#]+)/);
  return match ? match[1] : null;
}

async function advanceToVisibility(page, scope) {
  for (let step = 0; step < 6; step += 1) {
    const privateOption = scope.getByText("Private", { exact: true });
    if (await privateOption.isVisible({ timeout: 1000 }).catch(() => false)) {
      return;
    }
    const next = scope.getByRole("button", { name: "Next", exact: true });
    if ((await next.count()) === 0) {
      throw new Unexpected("visibility", "no Next button on the way to Visibility");
    }
    await checkHandoff(page);
    await next.click();
    await page.waitForTimeout(1500);
  }
  throw new Unexpected("visibility", "could not reach the Visibility step");
}

async function chooseControl(scope, label) {
  const radio = scope.getByRole("radio", { name: label });
  if ((await radio.count()) > 0) {
    try {
      await radio.first().check();
    } catch {
      await radio.first().click();
    }
    return;
  }
  const text = scope.getByText(label);
  if ((await text.count()) > 0) {
    await text.first().click();
    return;
  }
  throw new Error(`could not find the control for ${label}`);
}

async function chooseVisibility(page, scope, payload) {
  if (payload.publish_at) {
    await chooseControl(scope, "Schedule");
    await checkHandoff(page);
    const schedule = scope.getByLabel(/Schedule date/i);
    if ((await schedule.count()) > 0) {
      await schedule.first().fill(String(payload.publish_at));
    }
    return "schedule";
  }
  const labels = { private: "Private", unlisted: "Unlisted", public: "Public" };
  const label = labels[payload.privacy] ?? "Private";
  await chooseControl(scope, label);
  return String(payload.privacy ?? "private");
}

export async function upload({ page, payload }) {
  return guarded(page, async () => {
    await screen(
      page,
      "Studio dashboard",
      [{ role: "button", name: "Create" }],
      "dashboard",
    );
    await page.getByRole("button", { name: "Create" }).click();

    await screen(
      page,
      "upload menu",
      [{ text: "Upload videos" }],
      "upload-menu",
    );
    const uploadItem = page.getByRole("menuitem", { name: "Upload videos" });
    if ((await uploadItem.count()) > 0) {
      await uploadItem.first().click();
    } else {
      await page.getByRole("button", { name: "Upload videos" }).first().click();
    }

    await checkHandoff(page);
    // A file input has no accessible role; this structural selector is the one
    // exception to the role/label/text rule, and Studio is re-checked in step 13.
    await setLocalFiles(page, page.locator('input[type="file"]').first(), payload.video);

    const scope = await editorScope(page);
    await screen(
      page,
      "video details",
      [
        { role: "textbox", name: /Add a title that describes your video/i },
        { role: "textbox", name: /Tell viewers about your video/i },
      ],
      "details",
      scope,
    );
    await scope
      .getByRole("textbox", { name: /Add a title that describes your video/i })
      .first()
      .fill(payload.title ?? "");
    await scope
      .getByRole("textbox", { name: /Tell viewers about your video/i })
      .first()
      .fill(payload.description ?? "");
    if (payload.thumbnail) {
      const uploadFile = scope.getByRole("button", { name: "Upload file" }).first();
      if ((await uploadFile.count()) > 0) {
        const [chooser] = await Promise.all([
          page.waitForEvent("filechooser", { timeout: 10000 }),
          uploadFile.click(),
        ]);
        await chooser.setFiles(payload.thumbnail);
      } else {
        await scope.getByLabel("Thumbnail").setInputFiles(payload.thumbnail);
      }
    }

    const missingPlaylists = [];
    for (const playlist of payload.playlists ?? []) {
      const checkbox = scope.getByRole("checkbox", { name: playlist });
      if ((await checkbox.count()) === 0) {
        missingPlaylists.push(playlist);
      } else {
        await checkbox.first().check();
      }
    }

    const kidsLabel = payload.made_for_kids
      ? /^Yes, it'?s made for kids$/i
      : /^No, it'?s not made for kids$/i;
    await chooseControl(scope, kidsLabel);

    const showMore = scope.getByRole("button", {
      name: /Show advanced settings|Show more/i,
    });
    if ((await showMore.count()) > 0) await showMore.first().click();

    const syntheticLabel = payload.synthetic_media
      ? /^Yes, AI was used$/i
      : /^No, AI wasn't used$/i;
    const legacySyntheticLabel = payload.synthetic_media
      ? /^Yes, it includes altered or synthetic content$/i
      : /^No, it does not include altered or synthetic content$/i;
    let syntheticSet = false;
    try {
      await chooseControl(scope, syntheticLabel);
      syntheticSet = true;
    } catch {
      try {
        await chooseControl(scope, legacySyntheticLabel);
        syntheticSet = true;
      } catch {
        syntheticSet = false;
      }
    }

    if (payload.language) {
      const language = scope.getByRole("combobox", { name: "Video language" });
      if ((await language.count()) > 0) await language.selectOption(payload.language);
    }

    const videoId = await readVideoId(page);
    if (!videoId) throw new Error("could not read the uploaded video id");
    const videoUrl = `https://youtu.be/${videoId}`;

    await checkHandoff(page);
    await advanceToVisibility(page, scope);
    await screen(
      page,
      "visibility",
      [
        { text: "Visibility" },
        { text: /Private|Unlisted|Public|Schedule/i },
      ],
      "visibility",
      scope,
    );
    const visibility = await chooseVisibility(page, scope, payload);

    await checkHandoff(page);
    const button = finalButton(scope);
    try {
      await button.waitFor({ state: "visible", timeout: 30000 });
    } catch {
      const buttons = await scope
        .getByRole("button")
        .evaluateAll((els) =>
          els
            .map((e) => ({
              text: (e.innerText || "").trim(),
              aria: e.getAttribute("aria-label"),
              disabled: e.getAttribute("aria-disabled"),
            }))
            .filter((e) => e.text || e.aria),
        );
      const radios = await scope
        .getByRole("radio")
        .evaluateAll((els) =>
          els.map((e) => ({
            aria: e.getAttribute("aria-label"),
            checked: e.checked,
          })),
        );
      throw new Unexpected(
        "final-button",
        `no final button; buttons=${JSON.stringify(buttons)} radios=${JSON.stringify(radios)}`,
      );
    }
    const buttonLabel = ((await button.innerText()) || "").trim();

    return makeResult("ready_to_confirm", {
      id: videoId,
      summary: {
        title: payload.title ?? "",
        video_url: videoUrl,
        privacy: visibility,
        publish_at: payload.publish_at ?? null,
        playlists: payload.playlists ?? [],
        missing_playlists: missingPlaylists,
        thumbnail: payload.thumbnail ?? null,
        made_for_kids: Boolean(payload.made_for_kids),
        synthetic_media: Boolean(payload.synthetic_media),
        synthetic_media_set: syntheticSet,
        language: payload.language ?? null,
        final_button: buttonLabel,
      },
      message: `review the ${buttonLabel || "final"} step in the open Chrome window, then confirm`,
    });
  });
}

export async function confirm({ page, payload }) {
  return guarded(page, async () => {
    await checkHandoff(page);
    if (!payload.id) throw new Error("payload.id is required");
    const found = await readVideoId(page);
    if (found !== payload.id) {
      return stopUnexpected(
        page,
        "confirm",
        `expected video ${payload.id} but the open screen is ${found ?? "unknown"}`,
      );
    }
    const scope = await editorScope(page);
    const button = finalButton(scope);
    await button.waitFor({ state: "visible", timeout: 10000 });
    const label = ((await button.innerText()) || "").trim();
    await button.click();
    await screen(
      page,
      "publish done",
      [{ text: /published|saved|scheduled/i }],
      "confirm-done",
    );
    return makeResult("done", {
      id: payload.id,
      summary: { action: payload.expect ?? "publish", button: label },
      message: `YouTube accepted the ${label.toLowerCase() || "publish"} action`,
    });
  });
}

export async function addDubs({ page, payload }) {
  return guarded(page, async () => {
    if (!payload.id) throw new Error("payload.id is required");
    const dubs = payload.dubs ?? {};
    await page.goto(`https://studio.youtube.com/video/${payload.id}/translations`, {
      waitUntil: "domcontentloaded",
    });
    await screen(
      page,
      "translations",
      [{ text: "Translations" }, { text: "Languages" }],
      "translations",
    );

    const added = [];
    for (const [language, audio] of Object.entries(dubs)) {
      let row = page.getByRole("listitem").filter({ hasText: language });
      if ((await row.count()) === 0) {
        await page.getByRole("button", { name: "Add language" }).click();
        await screen(
          page,
          "add language",
          [{ text: "Add language" }, { label: "Language" }],
          "add-language",
        );
        await page.getByLabel("Language").selectOption(language);
        await page.getByRole("button", { name: "Add", exact: true }).click();
        row = page.getByRole("listitem").filter({ hasText: language });
        await row.first().waitFor({ state: "visible", timeout: 5000 });
      }

      await checkHandoff(page);
      await row.first().getByRole("button", { name: "Add dub" }).click();
      await screen(
        page,
        "add dub",
        [{ text: "Add dub" }, { label: "Dub audio" }],
        "add-dub",
      );
      await setLocalFiles(page, page.getByLabel("Dub audio"), audio);
      await page.getByRole("button", { name: "Upload" }).click();
      added.push(language);
    }

    await checkHandoff(page);
    const button = finalButton(page);
    if ((await button.count()) > 0) {
      await button.waitFor({ state: "visible", timeout: 5000 });
    }
    return makeResult("ready_to_confirm", {
      id: payload.id,
      summary: { dubs: added },
      message: "review the dubbed tracks in the open Chrome window, then confirm the publish",
    });
  });
}

export const commands = {
  upload,
  confirm,
  "add-dubs": addDubs,
};
