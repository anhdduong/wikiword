<script>
  import Admin from './Admin.svelte';
  import { apiUrl } from './api.js';

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
      <button class="tab" class:active={view === 'admin'}
        onclick={() => (view = 'admin')}>review queue</button>
    </nav>
  </header>

  {#if view === 'admin'}
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

      {#if data.status_note}
        <p class="status-note">{data.status_note}</p>
      {/if}

      {#if data.suggestions?.length}
        <p class="suggestions">
          did you mean:
          {#each data.suggestions as s, i}{#if i},
            {/if}<button class="link" onclick={() => { word = s; lookup(); }}>{s}</button>{/each}?
        </p>
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
              <span class="surface">{m.surface}</span>
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
              <div class="meaning none">everyday English word — see dictionary</div>
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
  .suggestions {
    color: #6d6558;
    font-size: 0.95rem;
    margin: 0.4rem 0 0;
  }
  .suggestions button.link {
    font-size: 0.95rem;
    margin-right: 0.5rem;
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
