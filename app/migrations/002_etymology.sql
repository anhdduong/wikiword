-- Local etymology store (plan milestone 4). Checked before any network
-- retrieval; every successful fetch is cached here, so the local store grows
-- with real lookups. Bulk preprocessing of the kaikki dump (source
-- 'kaikki_dump') is optional and additive.
--
-- Rows with etymology_text NULL are negative cache entries: the source
-- answered definitively and had no etymology (source 'none' = every source
-- answered and none knew the word). Network *errors* are never cached.
CREATE TABLE etymology (
  id             INTEGER PRIMARY KEY,
  word           TEXT NOT NULL,
  lang           TEXT NOT NULL DEFAULT 'English',
  pos            TEXT,                -- kaikki granularity is per part-of-speech
  etymology_text TEXT,
  source         TEXT NOT NULL CHECK (source IN
                   ('kaikki_dump', 'kaikki_api', 'free_dictionary', 'none')),
  source_url     TEXT,               -- the URL actually fetched (NULL for dump rows)
  fetched_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_etymology_word ON etymology(word);
