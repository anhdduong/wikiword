<script>
  import Admin from './Admin.svelte';
  import { apiUrl } from './api.js';

  // Curation is a maintainer's tool, not part of the public site. The API's
  // /admin routes are separately gated by HTTP Basic auth, so this only
  // removes the tab visitors would otherwise click into a 401 prompt.
  // `npm run dev` still shows it.
  const SHOW_ADMIN = import.meta.env.DEV;

  let view = $state('lookup');
  let word = $state('');
  let loading = $state(false);
  let data = $state(null);
  let error = $state(null);
  let showCandidates = $state(false);

  const STATUS_LABEL = {
    grounded: 'grounded — every morpheme verified',
    partial: 'partially verified',
    unverified: 'unverified',
  };

  // app/main.py joins the not-found warning into status_note alongside the
  // grounding notes. It gets its own prominent callout here, so strip it
  // from the muted line rather than saying it twice.
  const UNRECOGNIZED_NOTE =
    'word not found in any dictionary source — possible misspelling';

  function mutedNote(note) {
    if (!note) return null;
    const rest = note
      .split('; ')
      .filter((part) => part !== UNRECOGNIZED_NOTE)
      .join('; ');
    return rest || null;
  }

  async function lookup(event) {
    event?.preventDefault();
    const w = word.trim().toLowerCase();
    if (!w) return;
    loading = true;
    error = null;
    data = null;
    showCandidates = false;
    try {
      const resp = await fetch(apiUrl(`/lookup?word=${encodeURIComponent(w)}`));
      const body = await resp.json();
      if (!resp.ok) throw new Error(body.detail ?? `HTTP ${resp.status}`);
      data = body;
    } catch (err) {
      error = err.message;
    } finally {
      loading = false;
    }
  }

  // Only free-word pieces are drillable. A bound morpheme (syn-, -sis) is
  // not a headword, so looking it up returns a junk decomposition rather
  // than the entry the user expected.
  function drillInto(surface) {
    word = surface;
    lookup();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function pieceLabel(piece) {
    return piece.surface + (piece.linker ? `·${piece.linker}` : '');
  }

  function hostname(url) {
    try {
      return new URL(url).hostname.replace(/^www\./, '');
    } catch {
      return url;
    }
  }
</script>

<main>
  <header>
    <h1>wikiword</h1>
    <p class="tagline">etymology breakdown — every fact grounded, or labeled</p>
    <nav>
      <button class="tab" class:active={view === 'lookup'}
        onclick={() => (view = 'lookup')}>lookup</button>
      {#if SHOW_ADMIN}
        <button class="tab" class:active={view === 'admin'}
          onclick={() => (view = 'admin')}>review queue</button>
      {/if}
    </nav>
  </header>

  {#if SHOW_ADMIN && view === 'admin'}
    <Admin />
  {:else}
  <form onsubmit={lookup}>
    <input
      type="text"
      bind:value={word}
      placeholder="monolithic, photosynthesis, therapist…"
      autocomplete="off"
      spellcheck="false"
    />
    <button type="submit" disabled={loading}>
      {loading ? 'looking up…' : 'break down'}
    </button>
  </form>

  {#if error}
    <div class="box error">{error}</div>
  {/if}

  {#if data}
    <section class="result">
      <div class="word-row">
        <h2>{data.word}</h2>
        <span class="badge status-{data.status}">{STATUS_LABEL[data.status] ?? data.status}</span>
      </div>

      {#if data.unrecognized}
        <div class="box notfound">
          <p class="notfound-head">
            <span class="notfound-icon" aria-hidden="true">⚠</span>
            <strong>“{data.word}” isn’t a word we could find</strong>
          </p>
          <p class="notfound-body">
            No dictionary source has an entry for it, so it’s likely a
            misspelling. The breakdown below is what these letters
            <em>would</em> mean if it were a word.
          </p>
          {#if data.suggestions?.length}
            <div class="suggestions">
              <span class="suggestions-label">did you mean</span>
              {#each data.suggestions as s}
                <button class="suggestion" onclick={() => { word = s; lookup(); }}>
                  {s}
                </button>
              {/each}
            </div>
          {/if}
        </div>
      {/if}

      {#if mutedNote(data.status_note)}
        <p class="status-note">{mutedNote(data.status_note)}</p>
      {/if}

      {#if data.conflicts?.length}
        <div class="box conflict">
          <strong>⚠ source conflict</strong>
          {#each data.conflicts as c}
            <p>
              <b>{c.morpheme}</b>: our table says <i>{c.table_origin}</i>, but the
              retrieved etymology mentions {c.text_mentions.join(', ')} —
              “{c.snippet}”
            </p>
          {/each}
        </div>
      {/if}

      <div class="morphemes">
        {#each data.morphemes as m}
          <div class="morpheme {m.verified ? 'verified' : 'unverified'}">
            <div class="morpheme-head">
              {#if m.type === 'word'}
                <button
                  class="surface surface-link"
                  title="break down “{m.surface}”"
                  aria-label="break down {m.surface}"
                  onclick={() => drillInto(m.surface)}
                >{m.surface}<span class="drill-hint" aria-hidden="true">↳</span></button>
              {:else}
                <span class="surface">{m.surface}</span>
              {/if}
              <span class="chip kind">{m.type}</span>
              <span class="chip {m.verified ? 'ok' : 'warn'}">
                {m.verified ? '✓ verified' : '? unverified'}
              </span>
            </div>
            {#if m.origin}
              <div class="origin">
                {m.origin}{#if m.source_form}&nbsp;<i>{m.source_form}</i>{/if}
              </div>
            {/if}
            {#if m.meaning}
              <div class="meaning">“{m.meaning}”</div>
            {:else if m.type === 'word'}
              <div class="meaning none">everyday English word — click it to break it down</div>
            {:else}
              <div class="meaning none">no verified meaning</div>
            {/if}
            {#if m.notes}
              <div class="notes">{m.notes}</div>
            {/if}
            {#if m.citations?.length}
              <div class="citations">
                {#each m.citations as url}
                  <a href={url} target="_blank" rel="noreferrer">{hostname(url)}</a>
                {/each}
              </div>
            {/if}
          </div>
        {/each}
      </div>

      {#if data.literal_meaning || data.modern_usage}
        <div class="prose">
          <h3>{data.unrecognized ? 'if it were a word' : 'Literal and Modern Meaning'}</h3>
          {#if data.literal_meaning}
            <p><b>{data.unrecognized ? 'These morphemes would mean:' : 'Literal definition:'}</b> {data.literal_meaning}</p>
          {/if}
          {#if data.modern_usage}
            <p><b>Modern usage:</b> {data.modern_usage}</p>
          {/if}
        </div>
      {/if}

      {#if data.etymology?.length}
        <div class="etymology">
          <h3>retrieved etymology</h3>
          {#each data.etymology as e}
            <p>
              {e.text}
              {#if e.url}
                <a href={e.url} target="_blank" rel="noreferrer">[{e.source}]</a>
              {:else}
                <span class="source-tag">[{e.source}]</span>
              {/if}
            </p>
          {/each}
        </div>
      {/if}

      <div class="meta">
        {#if data.rerank}
          <p class="rerank">segmentation chosen by {data.rerank.model}: “{data.rerank.reason}”</p>
        {/if}
        <button class="link" onclick={() => (showCandidates = !showCandidates)}>
          {showCandidates ? 'hide' : 'show'} all {data.candidates.length} candidate segmentations
        </button>
        {#if showCandidates}
          <ol class="candidates">
            {#each data.candidates as c, i}
              <li class:chosen={i === data.chosen_index}>
                {c.pieces.map(pieceLabel).join(' · ')}
                <span class="cost">cost {c.cost}</span>
                {#if i === data.chosen_index}<span class="chip ok">chosen</span>{/if}
              </li>
            {/each}
          </ol>
        {/if}
      </div>
    </section>
  {/if}
  {/if}
</main>

<style>
  :global(body) {
    margin: 0;
    font-family: Charter, Georgia, 'Times New Roman', serif;
    background: #faf7f2;
    color: #26221c;
  }
  main {
    max-width: 44rem;
    margin: 0 auto;
    padding: 2.5rem 1.25rem 4rem;
  }
  header h1 {
    margin: 0;
    font-size: 2rem;
    letter-spacing: -0.02em;
  }
  .tagline {
    margin: 0.25rem 0 0.75rem;
    color: #6d6558;
  }
  nav {
    display: flex;
    gap: 0.4rem;
    margin-bottom: 1.5rem;
  }
  button.tab {
    background: #efe9dd;
    color: #5a5142;
    font-size: 0.85rem;
    padding: 0.3rem 0.8rem;
  }
  button.tab.active {
    background: #3d3428;
    color: #faf7f2;
  }
  form {
    display: flex;
    gap: 0.5rem;
  }
  input {
    flex: 1;
    font: inherit;
    font-size: 1.1rem;
    padding: 0.55rem 0.8rem;
    border: 1.5px solid #c9c0b2;
    border-radius: 8px;
    background: #fff;
  }
  input:focus {
    outline: 2px solid #8a6d3b;
    border-color: transparent;
  }
  button {
    font: inherit;
    padding: 0.55rem 1.1rem;
    border: none;
    border-radius: 8px;
    background: #3d3428;
    color: #faf7f2;
    cursor: pointer;
  }
  button:disabled {
    opacity: 0.6;
    cursor: wait;
  }
  .box {
    margin-top: 1.25rem;
    padding: 0.75rem 1rem;
    border-radius: 8px;
  }
  .error {
    background: #fbe9e7;
    border: 1px solid #d9a49a;
  }
  .conflict {
    background: #fdf0e5;
    border: 1.5px solid #d9863b;
  }
  .conflict p {
    margin: 0.4rem 0 0;
  }
  .result {
    margin-top: 2rem;
  }
  .word-row {
    display: flex;
    align-items: baseline;
    gap: 0.75rem;
    flex-wrap: wrap;
  }
  h2 {
    margin: 0;
    font-size: 2.2rem;
  }
  .badge {
    font-size: 0.8rem;
    padding: 0.2rem 0.6rem;
    border-radius: 99px;
    white-space: nowrap;
  }
  .status-grounded {
    background: #e3efdc;
    color: #33511f;
    border: 1px solid #94b47c;
  }
  .status-partial {
    background: #fdf0d5;
    color: #6b4e0e;
    border: 1px solid #d8b35e;
  }
  .status-unverified {
    background: #f4e2de;
    color: #7a2e1d;
    border: 1px solid #ce8d7d;
  }
  .status-note {
    color: #6d6558;
    font-size: 0.9rem;
    margin: 0.5rem 0 0;
  }
  .notfound {
    background: #fdf0e5;
    border: 1.5px solid #d9863b;
    border-left-width: 5px;
  }
  .notfound-head {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin: 0;
    font-size: 1.05rem;
    color: #7a4410;
  }
  .notfound-icon {
    font-size: 1.15rem;
    line-height: 1;
  }
  .notfound-body {
    margin: 0.4rem 0 0;
    color: #6b5334;
    font-size: 0.95rem;
  }
  .suggestions {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    flex-wrap: wrap;
    margin-top: 0.85rem;
  }
  .suggestions-label {
    font-size: 0.85rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: #8a6d3b;
    margin-right: 0.15rem;
  }
  button.suggestion {
    font-size: 0.95rem;
    padding: 0.28rem 0.75rem;
    border-radius: 99px;
    background: #fff;
    color: #7a4410;
    border: 1.5px solid #d9a86b;
  }
  button.suggestion:hover {
    background: #7a4410;
    border-color: #7a4410;
    color: #fff;
  }
  .morphemes {
    display: flex;
    gap: 0.75rem;
    flex-wrap: wrap;
    margin-top: 1.25rem;
  }
  .morpheme {
    flex: 1 1 11rem;
    background: #fff;
    border-radius: 10px;
    padding: 0.8rem 0.9rem;
    border: 1.5px solid #ddd5c7;
  }
  .morpheme.unverified {
    border-color: #d8b35e;
    background: #fffdf5;
  }
  .morpheme-head {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    flex-wrap: wrap;
  }
  .surface {
    font-size: 1.3rem;
    font-weight: 700;
  }
  /* A drillable morpheme is a real navigation affordance, not a hint: it gets
     a tinted pill, a border and an arrow so it reads as clickable at a
     glance, next to the plain bound morphemes that are not. */
  button.surface-link {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    background: #e9f0f7;
    border: 1px solid #b9cadb;
    padding: 0.05rem 0.5rem 0.1rem;
    border-radius: 8px;
    font: inherit;
    font-size: 1.3rem;
    font-weight: 700;
    color: #2f4a67;
    cursor: pointer;
    text-decoration: none;
    transition: background 0.12s ease, border-color 0.12s ease,
                transform 0.12s ease, box-shadow 0.12s ease;
  }
  .drill-hint {
    font-size: 0.9rem;
    font-weight: 600;
    color: #6b8aa8;
    transition: transform 0.12s ease, color 0.12s ease;
  }
  button.surface-link:hover,
  button.surface-link:focus-visible {
    background: #d8e6f3;
    border-color: #7fa3c4;
    color: #1d3348;
    transform: translateY(-1px);
    box-shadow: 0 2px 6px rgba(47, 74, 103, 0.18);
  }
  button.surface-link:hover .drill-hint,
  button.surface-link:focus-visible .drill-hint {
    color: #2f4a67;
    transform: translateX(2px);
  }
  button.surface-link:focus-visible {
    outline: 2px solid #2f4a67;
    outline-offset: 2px;
  }
  button.surface-link:active {
    transform: translateY(0);
    box-shadow: none;
  }
  .chip {
    font-size: 0.7rem;
    padding: 0.1rem 0.45rem;
    border-radius: 99px;
    background: #efe9dd;
    color: #5a5142;
  }
  .chip.ok {
    background: #e3efdc;
    color: #33511f;
  }
  .chip.warn {
    background: #fdf0d5;
    color: #6b4e0e;
  }
  .origin {
    margin-top: 0.45rem;
    font-size: 0.85rem;
    color: #6d6558;
  }
  .meaning {
    margin-top: 0.3rem;
  }
  .meaning.none {
    color: #a1988a;
    font-style: italic;
  }
  .notes {
    margin-top: 0.3rem;
    font-size: 0.8rem;
    color: #8a6d3b;
  }
  .citations {
    margin-top: 0.5rem;
    display: flex;
    gap: 0.5rem;
    flex-wrap: wrap;
  }
  .citations a,
  .etymology a {
    font-size: 0.78rem;
    color: #52657a;
  }
  .prose {
    margin-top: 1.5rem;
    background: #fff;
    border: 1.5px solid #ddd5c7;
    border-radius: 10px;
    padding: 0.9rem 1.1rem;
  }
  .prose h3 {
    font-size: 0.85rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #8a8072;
    margin: 0 0 0.5rem;
  }
  .prose p {
    margin: 0.35rem 0 0;
  }
  .etymology {
    margin-top: 1.5rem;
  }
  .etymology h3 {
    font-size: 0.85rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #8a8072;
    margin: 0 0 0.4rem;
  }
  .etymology p {
    margin: 0.4rem 0;
    font-size: 0.95rem;
  }
  .source-tag {
    font-size: 0.78rem;
    color: #8a8072;
  }
  .meta {
    margin-top: 1.75rem;
    border-top: 1px solid #e5ddce;
    padding-top: 0.9rem;
    font-size: 0.9rem;
  }
  .rerank {
    color: #6d6558;
    margin: 0 0 0.5rem;
  }
  button.link {
    background: none;
    border: none;
    padding: 0;
    color: #52657a;
    text-decoration: underline;
    cursor: pointer;
    font-size: 0.85rem;
  }
  .candidates {
    margin: 0.6rem 0 0;
    padding-left: 1.4rem;
  }
  .candidates li {
    margin: 0.25rem 0;
    color: #6d6558;
  }
  .candidates li.chosen {
    color: #26221c;
    font-weight: 600;
  }
  .cost {
    font-size: 0.75rem;
    color: #a1988a;
    margin-left: 0.4rem;
  }
</style>
