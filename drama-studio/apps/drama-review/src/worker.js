// Drama review board API. Static pages come from public/; this Worker serves /api/* and /media/*.
//
// Storage: R2 holds each published stage (<episode>/<stage>/manifest.json plus its media files) and
// episodes.json (the index). D1 holds reviewers, feedback and the owner's stage sign-offs.
// Auth: every request carries a personal reviewer token (header X-Review-Key, or ?k= for media tags).

const STAGES = ["script", "dialogue", "voices", "images", "videos", "lipsync", "final"];
const SAFE = /^[a-z0-9][a-z0-9._-]{0,80}$/i;
const BATCH = 100;                     // R2 objects deleted per request (see DELETE /api/admin/episode)

const json = (data, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json; charset=utf-8" } });
const fail = (status, error) => json({ error }, status);

async function reviewer(request, env) {
  const url = new URL(request.url);
  const token = request.headers.get("x-review-key") || url.searchParams.get("k") || "";
  if (!token) return null;
  return env.DB.prepare("SELECT token, name, role FROM reviewers WHERE token = ?").bind(token).first();
}

async function readJson(env, key) {
  const obj = await env.MEDIA.get(key);
  return obj ? obj.json() : null;
}

function stageKey(episode, stage) {
  if (!SAFE.test(episode) || !STAGES.includes(stage)) return null;
  return `${episode}/${stage}/manifest.json`;
}

// Media with Range support, so long clips seek and play on phones.
async function media(request, env, key) {
  const range = request.headers.get("range");
  const obj = await env.MEDIA.get(key, range ? { range: request.headers } : {});
  if (!obj) return fail(404, "not found");
  const headers = new Headers();
  obj.writeHttpMetadata(headers);
  headers.set("etag", obj.httpEtag);
  headers.set("accept-ranges", "bytes");
  headers.set("cache-control", "private, max-age=86400");
  if (range && obj.range) {
    const { offset = 0, length = obj.size - offset } = obj.range;
    headers.set("content-range", `bytes ${offset}-${offset + length - 1}/${obj.size}`);
    headers.set("content-length", String(length));
    return new Response(obj.body, { status: 206, headers });
  }
  headers.set("content-length", String(obj.size));
  return new Response(obj.body, { headers });
}

async function feedbackFor(env, episode, stage) {
  const { results } = await env.DB.prepare(
    `SELECT f.id, f.item, f.item_hash, f.verdict, f.comment, f.created_at, r.name, r.role
     FROM feedback f JOIN reviewers r ON r.token = f.token
     WHERE f.episode = ? AND f.stage = ? ORDER BY f.id`,
  ).bind(episode, stage).all();
  return results;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const parts = url.pathname.split("/").filter(Boolean);
    const me = await reviewer(request, env);
    if (!me) return fail(401, "This link is not valid. Ask for your personal review link.");

    if (parts[0] === "media") {
      return media(request, env, decodeURIComponent(parts.slice(1).join("/")));
    }
    if (parts[0] !== "api") return fail(404, "not found");
    const route = parts.slice(1);

    // GET /api/me
    if (route[0] === "me") return json({ name: me.name, role: me.role });

    // GET /api/episodes
    if (route[0] === "episodes" && request.method === "GET") {
      return json((await readJson(env, "episodes.json")) || []);
    }

    // Owner-only administration.
    if (route[0] === "admin") {
      if (me.role !== "owner") return fail(403, "owner only");
      // PUT /api/admin/put?key=<r2 key>&type=<content type>: upload one published file.
      if (route[1] === "put" && request.method === "PUT") {
        const key = url.searchParams.get("key") || "";
        if (!key || key.includes("..") || key.startsWith("/")) return fail(400, "bad key");
        await env.MEDIA.put(key, request.body, {
          httpMetadata: { contentType: url.searchParams.get("type") || "application/octet-stream" },
        });
        return json({ ok: true, key });
      }
      // POST /api/admin/reviewers {name}: invite a person, returns their token.
      if (route[1] === "reviewers" && request.method === "POST") {
        const { name } = await request.json();
        if (!name || String(name).length > 60) return fail(400, "name required");
        const token = crypto.randomUUID().replaceAll("-", "");
        await env.DB.prepare("INSERT INTO reviewers (token, name) VALUES (?, ?)").bind(token, String(name)).run();
        return json({ name, token });
      }
      // DELETE /api/admin/episode/<episode>: remove a whole episode from the board — its row in episodes.json, then the
      // R2 objects under its prefix, one batch per call. Feedback and sign-offs in D1 stay as the review record.
      // One batch per call because a request has a small CPU budget: the first version deleted everything in one
      // loop and was cut off after the files but before the index (2026-11-02), leaving a dead entry. The index goes
      // first now, and the client repeats while `remaining` is true.
      if (route[1] === "episode" && request.method === "DELETE") {
        const episode = route[2] || "";
        if (!SAFE.test(episode)) return fail(400, "bad episode");
        const index = (await readJson(env, "episodes.json")) || [];
        const kept = index.filter((e) => e.id !== episode);
        if (kept.length !== index.length) {
          await env.MEDIA.put("episodes.json", JSON.stringify(kept, null, 1), {
            httpMetadata: { contentType: "application/json" },
          });
        }
        const page = await env.MEDIA.list({ prefix: `${episode}/`, limit: BATCH });
        const keys = page.objects.map((o) => o.key);
        if (keys.length) await env.MEDIA.delete(keys);
        return json({ ok: true, episode, deleted: keys.length, remaining: page.truncated,
                      removed_from_index: index.length - kept.length });
      }
      // GET /api/admin/reviewers
      if (route[1] === "reviewers" && request.method === "GET") {
        const { results } = await env.DB.prepare("SELECT name, role, created_at FROM reviewers ORDER BY created_at").all();
        return json(results);
      }
      return fail(404, "not found");
    }

    // /api/stage/<episode>/<stage>[/feedback|/signoff]
    if (route[0] === "stage") {
      const [, episode, stage, action] = route;
      const key = stageKey(episode || "", stage || "");
      if (!key) return fail(400, "bad episode or stage");

      if (!action && request.method === "GET") {
        const manifest = await readJson(env, key);
        if (!manifest) return fail(404, "This stage has not been published yet.");
        const feedback = await feedbackFor(env, episode, stage);
        const { results: signoffs } = await env.DB.prepare(
          `SELECT s.manifest_hash, s.created_at, r.name FROM signoffs s JOIN reviewers r ON r.token = s.token
           WHERE s.episode = ? AND s.stage = ? ORDER BY s.id`,
        ).bind(episode, stage).all();
        const mineRows = await env.DB.prepare(
          "SELECT id FROM feedback WHERE episode = ? AND stage = ? AND token = ?",
        ).bind(episode, stage, me.token).all();
        const mine = new Set(mineRows.results.map((r) => r.id));
        return json({ manifest, feedback: feedback.map((f) => ({ ...f, mine: mine.has(f.id) })), signoffs });
      }

      // POST /api/stage/<ep>/<stage>/feedback {item, item_hash, verdict, comment}
      if (action === "feedback" && request.method === "POST") {
        const { item, item_hash, verdict, comment = "" } = await request.json();
        if (!item || !item_hash || !["ok", "change", "note"].includes(verdict)) return fail(400, "bad feedback");
        if (verdict === "change" && !String(comment).trim()) return fail(400, "Please say what should change.");
        await env.DB.prepare(
          "INSERT INTO feedback (episode, stage, item, item_hash, token, verdict, comment) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ).bind(episode, stage, String(item), String(item_hash), me.token, verdict, String(comment).slice(0, 2000)).run();
        return json({ ok: true });
      }

      // POST /api/stage/<ep>/<stage>/signoff {manifest_hash}: the owner approves the whole stage.
      if (action === "signoff" && request.method === "POST") {
        if (me.role !== "owner") return fail(403, "Only the owner can approve a stage.");
        const { manifest_hash } = await request.json();
        const manifest = await readJson(env, key);
        if (!manifest || manifest.manifest_hash !== manifest_hash) {
          return fail(409, "A newer version was published. Reload and check it first.");
        }
        await env.DB.prepare("INSERT INTO signoffs (episode, stage, manifest_hash, token) VALUES (?, ?, ?, ?)")
          .bind(episode, stage, manifest_hash, me.token).run();
        return json({ ok: true });
      }
    }
    return fail(404, "not found");
  },
};
