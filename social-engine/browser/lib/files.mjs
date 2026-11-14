// File inputs on a Chrome attached over CDP.
//
// Playwright refuses setInputFiles above 50 MB when the browser was reached with
// connectOverCDP, because it cannot tell the browser shares this filesystem.
// Our Chrome always runs on this machine (lib/chrome.mjs), so large files are
// handed over by path with CDP's DOM.setFileInputFiles instead.

import fs from "node:fs";

export const PLAYWRIGHT_TRANSFER_LIMIT = 50 * 1024 * 1024;

const MARK = "data-social-engine-upload";

export async function setLocalFiles(page, locator, files) {
  const paths = Array.isArray(files) ? files : [files];
  const largest = Math.max(...paths.map((file) => fs.statSync(file).size));
  if (largest < PLAYWRIGHT_TRANSFER_LIMIT) {
    await locator.setInputFiles(paths);
    return;
  }

  const token = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  await locator.evaluate((el, [mark, value]) => el.setAttribute(mark, value), [MARK, token]);
  const session = await page.context().newCDPSession(page);
  try {
    // Studio keeps its file input inside shadow roots, so search them too.
    const { result } = await session.send("Runtime.evaluate", {
      expression: `(() => {
        const selector = '[${MARK}="${token}"]';
        const walk = (root) => {
          const hit = root.querySelector(selector);
          if (hit) return hit;
          for (const el of root.querySelectorAll("*")) {
            if (el.shadowRoot) {
              const found = walk(el.shadowRoot);
              if (found) return found;
            }
          }
          return null;
        };
        return walk(document);
      })()`,
    });
    if (!result.objectId) throw new Error("file input not found for CDP upload");
    await session.send("DOM.setFileInputFiles", { files: paths, objectId: result.objectId });
  } finally {
    await locator.evaluate((el, mark) => el.removeAttribute(mark), MARK).catch(() => {});
    await session.detach().catch(() => {});
  }
}
