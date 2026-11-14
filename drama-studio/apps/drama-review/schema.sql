-- One row per person; the token is their personal link (?k=...). role 'owner' can publish, invite and sign off.
CREATE TABLE IF NOT EXISTS reviewers (
  token TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'reviewer',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
-- Every verdict or comment on one item. item_hash ties it to the exact version reviewed: a regenerated
-- take or frame gets a new hash, so old comments show as "on an earlier version".
CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  episode TEXT NOT NULL,
  stage TEXT NOT NULL,
  item TEXT NOT NULL,
  item_hash TEXT NOT NULL,
  token TEXT NOT NULL,
  verdict TEXT NOT NULL CHECK (verdict IN ('ok', 'change', 'note')),
  comment TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS feedback_stage ON feedback (episode, stage);
-- The owner's sign-off on a whole stage, tied to the manifest version they saw.
CREATE TABLE IF NOT EXISTS signoffs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  episode TEXT NOT NULL,
  stage TEXT NOT NULL,
  manifest_hash TEXT NOT NULL,
  token TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
