<script>
  import changelog from "../changelog.json";

  let query = "";

  const entries = Array.isArray(changelog?.entries) ? changelog.entries : [];
  const generatedAt = changelog?.generatedAt ?? null;
  const notice = changelog?.notice ?? null;

  $: normalizedQuery = query.trim().toLowerCase();

  function formatDateTime(iso) {
    try {
      return new Date(iso).toLocaleString(undefined, {
        year: "numeric",
        month: "short",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch {
      return iso;
    }
  }

  function matchText(text) {
    if (normalizedQuery.length === 0) return true;
    return String(text ?? "").toLowerCase().includes(normalizedQuery);
  }

  function filterStrings(items) {
    const list = Array.isArray(items) ? items : [];
    if (normalizedQuery.length === 0) return list;
    return list.filter((s) => matchText(s));
  }

  $: filteredEntries = entries
    .map((entry) => {
      const highlights = filterStrings(entry?.highlights);
      const sections = (Array.isArray(entry?.sections) ? entry.sections : [])
        .map((s) => ({
          ...s,
          items: filterStrings(s?.items),
        }))
        .filter((s) => (Array.isArray(s?.items) ? s.items.length : 0) > 0);

      return { ...entry, highlights, sections };
    })
    .filter((entry) => {
      if (normalizedQuery.length === 0) return true;
      return (
        matchText(entry?.title) ||
        matchText(entry?.dateRange) ||
        matchText(entry?.note) ||
        (Array.isArray(entry?.highlights) && entry.highlights.length > 0) ||
        (Array.isArray(entry?.sections) && entry.sections.length > 0)
      );
    });
</script>

<section class="page" aria-label="Changelog">
  <div class="page-header">
    <div class="title">
      <div class="heading">Changelog</div>
      <div class="subtitle">
        Automatically AI-generated release notes from the project history
        {#if generatedAt}
          <span class="sep">·</span> updated {formatDateTime(generatedAt)}
        {/if}
      </div>
    </div>

    <div class="controls">
      <input
        class="search"
        type="search"
        placeholder="Search changelog…"
        bind:value={query}
        aria-label="Search changelog"
      />
    </div>
  </div>

  {#if notice}
    <div class="notice" role="note">{notice}</div>
  {/if}

  <div class="content" role="list">
    {#if filteredEntries.length === 0}
      <div class="empty">No entries match “{query}”.</div>
    {:else}
      {#each filteredEntries as entry (entry.id || entry.title)}
        <article class="entry" role="listitem">
          <div class="entry-header">
            <div class="entry-title">{entry.title || "Changelog entry"}</div>
            {#if entry.dateRange}
              <div class="entry-date">{entry.dateRange}</div>
            {/if}
          </div>

          {#if entry.note}
            <div class="note">{entry.note}</div>
          {/if}

          {#if entry.highlights?.length > 0}
            <div class="block">
              <div class="block-title">Highlights</div>
              <ul class="bullets">
                {#each entry.highlights as item}
                  <li>{item}</li>
                {/each}
              </ul>
            </div>
          {/if}

          {#each entry.sections as section (section.title)}
            <div class="block">
              <div class="block-title">{section.title}</div>
              <ul class="bullets">
                {#each section.items as item}
                  <li>{item}</li>
                {/each}
              </ul>
            </div>
          {/each}
        </article>
      {/each}
    {/if}
  </div>
</section>

<style>
  .page {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
    gap: 14px;
  }

  .page-header {
    display: flex;
    gap: 16px;
    align-items: flex-end;
    justify-content: space-between;
    flex-wrap: wrap;
    padding: 14px 16px;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: var(--bg-secondary);
  }

  .heading {
    font-size: 18px;
    font-weight: 800;
    letter-spacing: -0.01em;
  }

  .subtitle {
    font-size: 12px;
    color: var(--text-tertiary);
  }

  .sep {
    opacity: 0.7;
    padding: 0 6px;
  }

  .controls {
    display: flex;
    gap: 10px;
    align-items: center;
    flex-wrap: wrap;
    justify-content: flex-end;
  }

  .search {
    width: min(520px, 80vw);
    padding: 10px 12px;
    border-radius: 10px;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    color: var(--text-primary);
    font-size: 13px;
    outline: none;
  }

  .search:focus {
    border-color: var(--border-hover);
  }

  .content {
    flex: 1;
    min-height: 0;
    overflow: auto;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .notice {
    padding: 12px 14px;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: var(--bg-secondary);
    color: var(--text-secondary);
    font-size: 12px;
    line-height: 1.45;
  }

  .empty {
    padding: 22px 16px;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: var(--bg-secondary);
    color: var(--text-tertiary);
    font-size: 13px;
  }

  .entry {
    padding: 16px 16px 6px;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: var(--bg-secondary);
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .entry-header {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    align-items: baseline;
  }

  .entry-title {
    font-size: 15px;
    font-weight: 800;
    letter-spacing: -0.01em;
    color: var(--text-primary);
  }

  .entry-date {
    font-size: 12px;
    color: var(--text-tertiary);
    white-space: nowrap;
  }

  .note {
    padding: 10px 12px;
    border-radius: 10px;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    color: var(--text-secondary);
    font-size: 12px;
    line-height: 1.45;
  }

  .block {
    padding: 0 0 10px;
    border-bottom: 1px solid var(--border);
  }

  .block:last-child {
    border-bottom: none;
    padding-bottom: 0;
  }

  .block-title {
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 0.08em;
    color: var(--text-tertiary);
    text-transform: uppercase;
    margin-bottom: 8px;
  }

  .bullets {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding-left: 18px;
    color: var(--text-secondary);
    font-size: 13px;
    line-height: 1.35;
  }

  .bullets li {
    overflow-wrap: anywhere;
  }

  @media (max-width: 768px) {
    .page-header {
      padding: 12px 12px;
    }
    .search {
      width: 100%;
    }
    .entry {
      padding: 14px 12px 6px;
    }
  }
</style>
