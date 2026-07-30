<script>
  import { apiUrl } from './api.js';

  // The server's 401 carries no body, so resp.json() on it fails with a JSON
  // parse error that says nothing useful. Name the state instead.
  //
  // Reaching this at all means the browser's own Basic-auth prompt did not
  // resolve it: the queue fetch is a simple request, so a 401 normally makes
  // the browser ask for credentials and retry transparently. Getting here
  // means the prompt was dismissed, or the credentials were wrong.
  const UNAUTHORIZED =
    'not signed in — reload to be prompted again, or open ' +
    apiUrl('/admin/queue') + ' directly to enter the admin credentials';

  let entries = $state([]);
  let error = $state(null);
  let busy = $state(false);
  let promotingId = $state(null);
  let form = $state({
    canonical: '', type: 'root', origin_lang: '', source_form: '',
    gloss: '', forms: '',
  });

  async function refresh() {
    error = null;
    try {
      const resp = await fetch(apiUrl('/admin/queue'), { credentials: 'include' });
      if (resp.status === 401) throw new Error(UNAUTHORIZED);
      const body = await resp.json();
      if (!resp.ok) throw new Error(body.detail ?? `HTTP ${resp.status}`);
      entries = body.entries;
    } catch (err) {
      error = err.message;
    }
  }

  $effect(() => {
    refresh();
  });

  async function post(path, body) {
    busy = true;
    error = null;
    try {
      const resp = await fetch(apiUrl(path), {
        method: 'POST',
        credentials: 'include',
        headers: body ? { 'content-type': 'application/json' } : {},
        body: body ? JSON.stringify(body) : undefined,
      });
      if (resp.status === 401) throw new Error(UNAUTHORIZED);
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail?.[0]?.msg ?? data.detail ?? `HTTP ${resp.status}`);
      await refresh();
      return true;
    } catch (err) {
      error = err.message;
      return false;
    } finally {
      busy = false;
    }
  }

  function openPromote(entry) {
    promotingId = entry.id;
    form = {
      canonical: entry.surface, type: 'root', origin_lang: '',
      source_form: '', gloss: '', forms: entry.surface,
    };
  }

  async function promote(entry) {
    const ok = await post(`/admin/queue/${entry.id}/promote`, {
      canonical: form.canonical,
      type: form.type,
      origin_lang: form.origin_lang,
      source_form: form.source_form || null,
      gloss: form.gloss,
      forms: form.forms.split(',').map((f) => f.trim()).filter(Boolean),
    });
    if (ok) promotingId = null;
  }
</script>

<section class="admin">
  <h2>review queue</h2>
  <p class="hint">
    Approving marks the affix row human-reviewed; promoting creates a new
    curated affix from an unknown span. Both invalidate the lookup cache.
  </p>

  {#if error}
    <div class="box error">{error}</div>
  {/if}

  {#if entries.length === 0}
    <p class="empty">Queue is empty — look some words up and come back.</p>
  {/if}

  {#each entries as e (e.id)}
    <div class="entry">
      <div class="entry-row">
        <span class="surface">{e.surface}</span>
        <span class="seen">seen in “{e.seen_in}”</span>
        {#if e.affix}
          <span class="detail">
            {e.affix.canonical} · {e.affix.type} · {e.affix.origin_lang} —
            “{e.affix.gloss}”
          </span>
          {#if e.affix.reviewed}
            <span class="chip ok">already reviewed</span>
          {/if}
        {:else}
          <span class="chip warn">unknown span</span>
        {/if}
        <span class="actions">
          {#if e.affix}
            <button disabled={busy} onclick={() => post(`/admin/queue/${e.id}/approve`)}>
              approve
            </button>
          {:else}
            <button disabled={busy} onclick={() => openPromote(e)}>promote…</button>
          {/if}
          <button class="quiet" disabled={busy}
            onclick={() => post(`/admin/queue/${e.id}/dismiss`)}>
            dismiss
          </button>
        </span>
      </div>

      {#if promotingId === e.id}
        <div class="promote-form">
          <label>canonical <input bind:value={form.canonical} /></label>
          <label>type
            <select bind:value={form.type}>
              <option>prefix</option>
              <option>root</option>
              <option>suffix</option>
              <option>combining_form</option>
            </select>
          </label>
          <label>origin <input bind:value={form.origin_lang} placeholder="Ancient Greek" /></label>
          <label>source form <input bind:value={form.source_form} placeholder="optional" /></label>
          <label>gloss <input bind:value={form.gloss} placeholder="meaning" /></label>
          <label>forms <input bind:value={form.forms} placeholder="comma,separated" /></label>
          <span class="actions">
            <button disabled={busy || !form.gloss || !form.origin_lang}
              onclick={() => promote(e)}>create reviewed row</button>
            <button class="quiet" onclick={() => (promotingId = null)}>cancel</button>
          </span>
        </div>
      {/if}
    </div>
  {/each}
</section>

<style>
  .admin h2 {
    margin: 0 0 0.25rem;
  }
  .hint {
    color: #6d6558;
    font-size: 0.9rem;
    margin: 0 0 1.25rem;
  }
  .empty {
    color: #a1988a;
    font-style: italic;
  }
  .box.error {
    background: #fbe9e7;
    border: 1px solid #d9a49a;
    padding: 0.6rem 0.9rem;
    border-radius: 8px;
    margin-bottom: 1rem;
  }
  .entry {
    background: #fff;
    border: 1.5px solid #ddd5c7;
    border-radius: 10px;
    padding: 0.7rem 0.9rem;
    margin-bottom: 0.6rem;
  }
  .entry-row {
    display: flex;
    align-items: baseline;
    gap: 0.6rem;
    flex-wrap: wrap;
  }
  .surface {
    font-size: 1.15rem;
    font-weight: 700;
  }
  .seen {
    color: #a1988a;
    font-size: 0.85rem;
  }
  .detail {
    font-size: 0.9rem;
    color: #5a5142;
  }
  .chip {
    font-size: 0.7rem;
    padding: 0.1rem 0.45rem;
    border-radius: 99px;
  }
  .chip.ok {
    background: #e3efdc;
    color: #33511f;
  }
  .chip.warn {
    background: #fdf0d5;
    color: #6b4e0e;
  }
  .actions {
    margin-left: auto;
    display: flex;
    gap: 0.4rem;
  }
  button {
    font: inherit;
    font-size: 0.85rem;
    padding: 0.3rem 0.7rem;
    border: none;
    border-radius: 6px;
    background: #3d3428;
    color: #faf7f2;
    cursor: pointer;
  }
  button.quiet {
    background: #efe9dd;
    color: #5a5142;
  }
  button:disabled {
    opacity: 0.6;
  }
  .promote-form {
    margin-top: 0.7rem;
    padding-top: 0.7rem;
    border-top: 1px dashed #ddd5c7;
    display: flex;
    gap: 0.6rem;
    flex-wrap: wrap;
    align-items: end;
  }
  .promote-form label {
    display: flex;
    flex-direction: column;
    font-size: 0.75rem;
    color: #8a8072;
    gap: 0.2rem;
  }
  .promote-form input,
  .promote-form select {
    font: inherit;
    font-size: 0.9rem;
    padding: 0.3rem 0.5rem;
    border: 1px solid #c9c0b2;
    border-radius: 6px;
    width: 9rem;
  }
</style>
