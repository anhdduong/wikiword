# wikiword

Etymology breakdown tool. **Read `plan.md` first** — especially the core
principle: the LLM never originates etymological facts; it only reranks
closed-set candidates and synthesizes prose over retrieved facts.

## Layout
- `app/db.py` — SQLite connection + migration runner (numbered `.sql` files in
  `app/migrations/`, tracked in `schema_migrations`).
- `app/seed.py` — idempotent seed of `affix`/`affix_form` from
  `seed/affixes.csv`. Keyed on (canonical, type, gloss). Applies the CSV's
  `reviewed` column on INSERT, but never touches `reviewed`/`citations` on
  existing rows — a running database's curation state belongs to the review
  flow. The update path DELETEs and rewrites a row's forms from the CSV, so
  a form added straight to the database and not mirrored back into
  affixes.csv is destroyed on the next run.
- `app/lexicon.py` — in-memory lexicon: affix forms from the DB + free words
  from `seed/en_50k.txt` (frequency-ranked, length >= 3; rank doubles as the
  false-friend signal).
- `app/segment.py` — the §3 algorithm: morpheme DAG + positional state
  machine + hand-tuned cost function, k-shortest via lazy best-first search
  (equivalent to Yen's on these tiny graphs). NOTE one deliberate deviation
  from plan §3.2: prefixes may recur mid-word — grammar is
  `(prefix* root-like+)+ suffix*` — because photo|syn|the|sis requires it.
  A whole word may be a single free piece only when its frequency rank
  clears FREE_WHOLE_MAX_RANK — everyday words (that, phone) get an honest
  "no decomposition" reading that junk splits can't beat, while learned
  vocabulary (therapist, monolith) must still decompose. Cost constants are
  pinned by the §3.6 tests; change them only with the tests open.
- `app/main.py` — FastAPI service (`create_app(db_path)` factory; lexicon
  loaded once at startup, SQLite connection per request). Cache rows are only
  served when their `model_version` matches `app/version.py`'s current hash —
  which covers the segment cost constants, so tuning them auto-invalidates.
- `app/llm.py` — shared LLM plumbing: provider detection + structured-output
  call helper. Anthropic when credentials exist; else Gemini via the AI
  Studio REST API (free tier, stdlib urllib) when GEMINI_API_KEY or
  GOOGLE_API_KEY is set — model WIKIWORD_GEMINI_MODEL (default
  gemini-flash-lite-latest: Google's moving alias — pinned ids rot as
  Google retires models for new users — and the lite tier has the highest
  free-tier daily quota), the Claude model ids are ignored on that
  provider. `python -m app.llm` self-tests the provider and lists the
  key's available models on Gemini failure.
  Provider + Gemini model are hashed into model_version. tests/conftest.py
  stubs `is_enabled` and `call_structured` so tests can never reach a real
  API.
- `app/rerank.py` — llm_rerank (closed-set choice; default model
  `claude-opus-4-8`, override WIKIWORD_RERANK_MODEL). The LLM is only
  consulted when the top-2 cost gap is below RERANK_MARGIN — a clear
  winner stands without API spend. Any failure → cost order stands; a
  transient failure serves the response but skips the cache write. No
  credentials → LLM calls disabled, responses cached and labeled.
- `app/assemble.py` — llm_assemble (constrained synthesis of literal_meaning
  only; override WIKIWORD_ASSEMBLE_MODEL). Only synthesizes from grounded
  facts; no verified meanings → null field, no API spend. Transient failure
  → served uncached, same as rerank. Both prompts are version-controlled and
  hashed into model_version. modern_usage is never synthesized — see compose.
- `app/compose.py` — deterministic prose: modern_usage on EVERY response
  (LLM or not) = first Free Dictionary definition, quoted verbatim (fetched
  even when no morpheme has a gloss — it's independently grounded); when LLM
  calls are disabled, also literal_meaning = affix-table glosses joined in
  word order. Nothing generated, so nothing can be invented. Transport
  failure/5xx on the definition fetch → served uncached (same rule as
  rerank/assemble); 404 is a definitive miss. COMPOSE_VERSION is hashed into
  model_version.
- `app/ground.py` — ground() + status(): affix table is truth (table-backed
  morphemes are verified with the row's meaning/origin + fetched citations);
  retrieval corroborates and adds citation URLs only when the text actually
  mentions the morpheme; homograph senses (Latin ad- vs Old English al-,
  both surface "al") resolve to the row whose origin language the
  mentioning prose names, and same-language pairs (Greek theos "god" vs
  thea "sight", both surface "the") resolve on which source_form the prose
  names — so a split row's source_form should list every spelling worth
  matching. Prose matching is diacritic-insensitive: Wiktionary writes
  Latin with macrons (lex -> lēx), which otherwise silently defeats the
  whole mechanism — neither sense matches and row order wins by accident.
  Free pieces are typed "word" (not roots), verify
  only via corroboration, and never get an invented meaning; conflicts are
  detected from prose sentences only
  (never the wiktextract "Etymology tree" lineage block) and cap status at
  partial; unknown spans + reviewed=0 morphemes land in review_queue
  (deduped by surface). Bump GROUND_VERSION when behavior changes.
- `app/retrieve.py` — etymology retrieval: local `etymology` table first, then
  kaikki.org per-word JSONL, then Free Dictionary API. Successful fetches are
  cached back (lazy-growing local store); definitive empty answers are
  negative-cached; network errors are never cached. `http_get` is injectable
  for offline tests. `prose()` strips wiktextract's "Etymology tree" block —
  store raw, display/assemble prose only.
- `scripts/ingest_kaikki.py` — optional bulk preprocessing of a kaikki dump
  (.jsonl/.jsonl.gz) into the etymology table; retrieval works without it.
- `scripts/verify_citations.py` — fetches each row's Wiktionary entry, attaches
  the URL as a citation only on HTTP 200 (grounding rule: only fetched URLs
  become citations).
- `scripts/pull_wiktionary_affixes.py` — drafts new affix rows from
  Wiktionary's English prefix/suffix categories (MediaWiki API) + each
  entry's kaikki JSON, into `seed/draft_affixes.csv` (same schema; not
  seeded — a human moves curated rows into affixes.csv). Resume-able;
  skips already-curated/drafted rows. Merge in batches and re-run
  eval_batch afterwards — every new form is a new way to split words.
- `scripts/warm_cache.py` — pre-warms word_cache through the real pipeline
  (top-N wordlist words or a custom list); skips already-cached words, so
  re-runs are incremental; stops early on consecutive degraded responses
  (LLM quota exhausted) and resumes on the next run.
- `app/admin.py` — review-queue curation API (/admin/queue + approve /
  promote / dismiss). approve flips an affix row to reviewed=1; promote
  creates a new reviewed=1 row + forms from an unknown span. Every mutation
  clears word_cache and reloads the in-memory lexicon. Unauthenticated —
  local-trust tool; front it with a proxy before exposing.
- `front/` — Svelte 5 + Vite SPA (the plan's "SvelteKit (minimal)" slot; no
  SSR needed for a single view). Renders the §6 contract: status badge,
  per-morpheme verified/unverified badges, conflict warnings, prose fields,
  citations, collapsible candidate list. `front/dist` (built) is mounted by
  FastAPI at `/`, so production is a single server; `npm run dev` in front/
  proxies /lookup to :8000 for development.
- `wikiword.db` — the local database (generated; delete and re-run seed to
  rebuild).

## Commands
- Run API + UI: `.venv/bin/uvicorn app.main:app --reload` (GET /lookup?word=...,
  UI at / when front/dist exists)
- Front-end dev: `cd front && npm run dev` (build: `npm run build`)
- Test: `.venv/bin/pytest -q`
- Migrate + seed: `.venv/bin/python -m app.seed`
- Attach citations: `.venv/bin/python -m scripts.verify_citations`

## Conventions
- Python 3.12, stdlib-first (sqlite3, urllib); add deps only when a milestone
  needs them.
- Schema changes = new numbered migration file, never edit an applied one.
- New seed rows start at `reviewed = 0`. Curation happens in the DB (admin
  flow or direct SQL), but must then be **mirrored back into affixes.csv** —
  both the `reviewed` column and any added forms. The CSV is the only
  reproducible record of the lexicon: `reviewed` feeds the segmenter's
  UNREVIEWED_PENALTY, so a CSV that has drifted from the DB means tests
  segment differently from production and cannot pin its behaviour. Verify
  with a fresh `seed()` into an empty DB — it should reproduce the deployed
  affix table row for row.
- Curating a large batch? Do NOT use the admin API: every mutation there runs
  `DELETE FROM word_cache`, discarding the prewarmed LLM cache. Edit the DB
  directly, then clear only the rows whose output actually changed and
  re-warm the LLM-mode ones (`scripts/warm_cache.py --words`).
