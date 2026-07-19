-- The concept: authoritative meaning + origin. This is the asset we're building.
CREATE TABLE affix (
  id           INTEGER PRIMARY KEY,
  canonical    TEXT NOT NULL,               -- "mono-"
  type         TEXT NOT NULL CHECK (type IN ('prefix', 'root', 'suffix', 'combining_form')),
  origin_lang  TEXT NOT NULL,               -- "Ancient Greek"
  source_form  TEXT,                        -- "monos" (transliteration; original script goes in notes)
  gloss        TEXT NOT NULL,               -- "one, single, alone"
  notes        TEXT,
  citations    TEXT NOT NULL DEFAULT '[]',  -- JSON array of real URLs
  reviewed     INTEGER NOT NULL DEFAULT 0,  -- 1 = human-curated, 0 = LLM-proposed
  -- Same canonical+type can appear twice with different senses (in- "not" vs
  -- in- "into"), so the natural key includes the gloss.
  UNIQUE (canonical, type, gloss)
);

-- Surface strings -> concept. Handles allomorphy: mono-/mon-, in-/im-/il-/ir-.
CREATE TABLE affix_form (
  form      TEXT NOT NULL,                  -- "mono", "mon", "lith", "litho"
  affix_id  INTEGER NOT NULL REFERENCES affix(id) ON DELETE CASCADE,
  PRIMARY KEY (form, affix_id)
);
CREATE INDEX idx_affix_form ON affix_form(form);

-- Cache doubles as a growing dictionary; model_version lets us invalidate.
CREATE TABLE word_cache (
  word          TEXT PRIMARY KEY,
  status        TEXT NOT NULL CHECK (status IN ('grounded', 'partial', 'unverified')),
  payload       TEXT NOT NULL,              -- full response contract (JSON)
  model_version TEXT NOT NULL,
  created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Review queue: every LLM-proposed morpheme we couldn't verify lands here
-- and gets curated up into an authoritative affix row over time.
CREATE TABLE review_queue (
  id         INTEGER PRIMARY KEY,
  surface    TEXT NOT NULL,
  seen_in    TEXT NOT NULL,                 -- the word that produced it
  proposed   TEXT NOT NULL,                 -- JSON: LLM's proposed origin/gloss
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
