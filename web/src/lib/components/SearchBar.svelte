<script>
  import { onMount } from "svelte";
  import { fly } from "svelte/transition";
  import { searchTokens } from "../api.js";
  import { addToken, addTokens } from "../stores/state.js";
  import { getDisplayName } from "../stores/assets.js";
  import { getSearchIndex } from "../utils/searchIndexCache.js";
  import posthog from "../client/posthog";
  import VoiceInput from "./VoiceInput.svelte";

  let query = "";
  let results = [];
  let showResults = false;
  let hasFocus = false;
  let selectedIndex = -1;
  let analyticsTimeout;
  let currentSegment = "";
  let recognizedPreview = [];

  let searchIndex = [];
  let searchReady = false;
  let exactLookup = new Map();
  let tokenLookup = new Map();

  const MAX_SPAN_WORDS = 4;
  const STOP_WORDS = new Set([
    "and",
    "or",
    "plus",
    "then",
    "etc",
    "&",
    "a",
    "an",
    "the",
    "with",
  ]);

  function isNegationPrefix(text) {
    const t = (text || "").trim();
    return t.startsWith("-") || t.startsWith("!");
  }

  function stripNegationPrefix(text) {
    return String(text || "").replace(/^[-!]+/, "");
  }

  function applyNegation(token, negated) {
    return negated ? `-${token}` : token;
  }

  function stripTokenNegation(token) {
    if (!token) return token;
    return token.startsWith("-") || token.startsWith("!")
      ? token.slice(1)
      : token;
  }

  function normalizeSearchText(text) {
    return (text || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  }

  // More forgiving normalization for possessives/plurals:
  // "Kraken's Fury" -> "krakenfury", "Runaans Hurricane" -> "runaanhurricane"
  function normalizeSearchTextLoose(text) {
    const cleaned = (text || "")
      .toLowerCase()
      .replace(/['’]s\b/g, "") // drop possessive
      .replace(/[^a-z0-9]+/g, " ")
      .trim();
    if (!cleaned) return "";
    const parts = cleaned.split(/\s+/).filter(Boolean);
    const stemmed = parts.map((p) => {
      if (p.length > 3 && p.endsWith("s") && !p.endsWith("ss"))
        return p.slice(0, -1);
      return p;
    });
    return stemmed.join("");
  }

  function getActiveSegment(text) {
    const parts = text.split(/[\s,;]+/);
    return parts[parts.length - 1] || "";
  }

  function getTokenTypeFromToken(token) {
    const t = stripTokenNegation(token);
    if (t.startsWith("U:")) return "unit";
    if (t.startsWith("I:")) return "item";
    if (t.startsWith("T:")) return "trait";
    if (t.startsWith("E:")) return "equipped";
    return "unknown";
  }

  function parseEquippedToken(token) {
    const t = stripTokenNegation(token);
    if (!t?.startsWith?.("E:")) return null;
    const parts = t.slice(2).split("|");
    if (parts.length !== 2) return null;
    const [unit, item] = parts;
    if (!unit || !item) return null;
    return { unit, item };
  }

  function formatEquippedLabel(token) {
    const parsed = parseEquippedToken(token);
    if (!parsed) return token;
    const unitLabel = getDisplayName("unit", parsed.unit);
    const itemLabel = getDisplayName("item", parsed.item);
    return `${unitLabel} → ${itemLabel}`;
  }

  function pruneImpliedTokens(tokens) {
    const implied = new Set();
    for (const t of tokens) {
      const parsed = parseEquippedToken(t);
      if (!parsed) continue;
      implied.add(`U:${parsed.unit}`);
      implied.add(`I:${parsed.item}`);
    }
    return tokens.filter((t) => t.startsWith("E:") || !implied.has(t));
  }

  function getResultLabel(result) {
    if (!result) return "";
    if (result.type === "equipped" || result.token?.startsWith?.("E:")) {
      return formatEquippedLabel(result.token);
    }
    return getDisplayName(result.type, result.label);
  }

  function getCompletedPrefixText(text) {
    if (!text) return "";
    const endsWithDelimiter = /[\s,;]$/.test(text);
    if (endsWithDelimiter) return text;
    const parts = text.split(/[\s,;]+/).filter(Boolean);
    parts.pop(); // remove active segment
    return parts.join(" ");
  }

  function buildRecognizedPreview(text) {
    if (!searchReady) return [];
    const prefixText = getCompletedPrefixText(text);
    if (!prefixText.trim()) return [];

    const { tokens } = parseTokensFromText(prefixText);
    return tokens
      .map((token) => {
        const negated = isNegationPrefix(token);
        const baseToken = stripTokenNegation(token);
        const entry = tokenLookup.get(baseToken);

        if (baseToken.startsWith("E:")) {
          return {
            token,
            type: "equipped",
            label: negated
              ? `Not ${formatEquippedLabel(baseToken)}`
              : formatEquippedLabel(baseToken),
          };
        }
        let label = entry?.label ?? baseToken.slice(2);
        if (negated) label = `Not ${label}`;
        return {
          token,
          type: entry?.type ?? getTokenTypeFromToken(baseToken),
          label,
        };
      })
      .filter((t) => t.type !== "unknown");
  }

  function scoreMatch(entry, qNorm) {
    const qLoose = normalizeSearchTextLoose(qNorm);
    if (
      entry.labelNorm === qNorm ||
      entry.tokenSuffixNorm === qNorm ||
      entry.labelNormLoose === qLoose ||
      entry.tokenSuffixNormLoose === qLoose
    )
      return 0;
    if (
      entry.labelNorm.startsWith(qNorm) ||
      entry.tokenSuffixNorm.startsWith(qNorm)
    )
      return 1;
    if (
      entry.labelNormLoose.startsWith(qLoose) ||
      entry.tokenSuffixNormLoose.startsWith(qLoose)
    )
      return 1;
    return 2;
  }

  function searchLocal(segment) {
    const qNorm = normalizeSearchText(segment);
    const qLoose = normalizeSearchTextLoose(segment);
    if (!qNorm) return [];

    const matches = [];
    for (const entry of searchIndex) {
      if (entry.type === "equipped") continue;
      if (
        entry.labelNorm.includes(qNorm) ||
        entry.tokenSuffixNorm.includes(qNorm) ||
        (qLoose &&
          (entry.labelNormLoose.includes(qLoose) ||
            entry.tokenSuffixNormLoose.includes(qLoose)))
      ) {
        matches.push(entry);
      }
    }

    matches.sort((a, b) => {
      const sa = scoreMatch(a, qNorm);
      const sb = scoreMatch(b, qNorm);
      if (sa !== sb) return sa - sb;
      if (a.count !== b.count) return b.count - a.count;
      return a.label.length - b.label.length;
    });

    return matches
      .slice(0, 20)
      .map(({ token, label, type, count }) => ({ token, label, type, count }));
  }

  function resolvePhraseToToken(phrase) {
    const key = normalizeSearchText(phrase);
    const keyLoose = normalizeSearchTextLoose(phrase);
    if (!key) return null;

    const exact = exactLookup.get(key);
    if (exact) return exact;
    const exactLoose = keyLoose ? exactLookup.get(keyLoose) : null;
    if (exactLoose) return exactLoose;

    // Unique prefix match (lets users type "aat" -> Aatrox, etc.)
    let best = null;
    let bestCount = -1;
    let matches = 0;
    for (const entry of searchIndex) {
      if (entry.type === "equipped") continue;
      if (
        entry.labelNorm.startsWith(key) ||
        entry.tokenSuffixNorm.startsWith(key) ||
        (keyLoose &&
          (entry.labelNormLoose.startsWith(keyLoose) ||
            entry.tokenSuffixNormLoose.startsWith(keyLoose)))
      ) {
        matches += 1;
        if (entry.count > bestCount) {
          best = entry.token;
          bestCount = entry.count;
        }
        if (matches > 1) {
          // Ambiguous: force the user to type more or pick from suggestions
          return null;
        }
      }
    }
    return best;
  }

  function resolvePhraseToTokenExact(phrase) {
    const key = normalizeSearchText(phrase);
    if (!key) return null;
    return exactLookup.get(key) ?? null;
  }

  function parseTokensFromText(text) {
    const words = text
      .split(/[\s,;]+/)
      .map((w) => w.trim())
      .filter(Boolean);

    const tokens = [];
    let i = 0;
    while (i < words.length) {
      let negated = false;
      if (words[i] === "-" || words[i] === "!") {
        negated = true;
        i += 1;
        if (i >= words.length) break;
      }

      const firstWord = words[i] || "";
      if (isNegationPrefix(firstWord)) {
        negated = true;
      }

      const wordKey = stripNegationPrefix(firstWord)
        .toLowerCase()
        .replace(/[^a-z0-9]/g, "");
      if (STOP_WORDS.has(wordKey)) {
        i += 1;
        continue;
      }

      let matchedToken = null;
      let matchedSpan = 0;
      for (
        let span = Math.min(MAX_SPAN_WORDS, words.length - i);
        span >= 1;
        span -= 1
      ) {
        const phraseWords = words.slice(i, i + span);
        if (negated && phraseWords.length > 0) {
          phraseWords[0] = stripNegationPrefix(phraseWords[0]);
        }
        const phrase = phraseWords.join(" ");
        const token = resolvePhraseToToken(phrase);
        if (token) {
          matchedToken = token;
          matchedSpan = span;
          break;
        }
      }

      if (!matchedToken) break;

      tokens.push(applyNegation(matchedToken, negated));
      i += matchedSpan;
    }

    const leftover = words.slice(i).join(" ");
    return { tokens, leftover };
  }

  function parseTokensFromTextExact(text) {
    const words = text
      .split(/[\s,;]+/)
      .map((w) => w.trim())
      .filter(Boolean);

    const tokens = [];
    let i = 0;
    while (i < words.length) {
      let negated = false;
      if (words[i] === "-" || words[i] === "!") {
        negated = true;
        i += 1;
        if (i >= words.length) break;
      }

      const firstWord = words[i] || "";
      if (isNegationPrefix(firstWord)) {
        negated = true;
      }

      const wordKey = stripNegationPrefix(firstWord)
        .toLowerCase()
        .replace(/[^a-z0-9]/g, "");
      if (STOP_WORDS.has(wordKey)) {
        i += 1;
        continue;
      }

      let matchedToken = null;
      let matchedSpan = 0;
      for (
        let span = Math.min(MAX_SPAN_WORDS, words.length - i);
        span >= 1;
        span -= 1
      ) {
        const phraseWords = words.slice(i, i + span);
        if (negated && phraseWords.length > 0) {
          phraseWords[0] = stripNegationPrefix(phraseWords[0]);
        }
        const phrase = phraseWords.join(" ");
        const token = resolvePhraseToTokenExact(phrase);
        if (token) {
          matchedToken = token;
          matchedSpan = span;
          break;
        }
      }

      if (!matchedToken) break;

      tokens.push(applyNegation(matchedToken, negated));
      i += matchedSpan;
    }

    const leftover = words.slice(i).join(" ");
    return { tokens, leftover };
  }

  function maybeAutoCommitTokens() {
    if (!searchReady) return false;
    if (!/[\s,;]+/.test(query)) return false;

    const endsWithDelimiter = /[\s,;]$/.test(query);
    const { tokens, leftover } = parseTokensFromTextExact(query);
    if (tokens.length === 0) return false;

    // Don't auto-commit a single exact match unless the user clearly "ended" it.
    if (!leftover && !endsWithDelimiter) return false;

    addMany(tokens, "search");
    query = leftover;
    return true;
  }

  function trackSearchOnce(resultCount) {
    clearTimeout(analyticsTimeout);
    analyticsTimeout = setTimeout(() => {
      posthog.capture("search", {
        query,
        result_count: resultCount,
        mode: searchReady ? "local" : "backend",
      });
    }, 250);
  }

  function clearResults() {
    results = [];
    showResults = false;
    selectedIndex = -1;
  }

  function clearInput() {
    query = "";
    currentSegment = "";
    recognizedPreview = [];
    clearResults();
  }

  function addMany(tokens, source) {
    let unique = Array.from(new Set(tokens || [])).filter(Boolean);
    unique = pruneImpliedTokens(unique);
    if (unique.length === 0) return;
    if (unique.length === 1) {
      addToken(unique[0], source);
      return;
    }
    addTokens(unique, source);
    posthog.capture("search_bulk_add", {
      tokens_added: unique.length,
      tokens: unique,
    });
  }

  async function handleInput() {
    selectedIndex = -1;

    // Snappy multi-add: as soon as a completed token is recognized, add it and
    // keep the input focused on the remaining tail.
    if (maybeAutoCommitTokens()) {
      clearResults();
    }

    const segment = getActiveSegment(query);
    currentSegment = segment;
    recognizedPreview = buildRecognizedPreview(query);

    try {
      if (searchReady) {
        results = segment ? searchLocal(segment) : [];
        showResults =
          hasFocus && (query.length > 0 || recognizedPreview.length > 0);
        if (showResults) selectedIndex = 0;
        trackSearchOnce(results.length);
        return;
      }

      // Fallback: backend search (only used if the index fails to load)
      if (!segment || segment.length < 2) {
        clearResults();
        return;
      }

      const backendResults = await searchTokens(segment);
      results = backendResults.filter((r) => r.type !== "equipped");
      showResults = hasFocus && (results.length > 0 || query.length > 0);
      if (showResults) selectedIndex = 0;
      trackSearchOnce(results.length);
    } catch (error) {
      console.error("Search error:", error);
    }
  }

  function handleKeydown(event) {
    if (event.key === "Enter") {
      const text = query.trim();
      if (!text) return;

      const segment = getActiveSegment(query);

      // If the user is just picking a single suggestion, preserve existing behavior
      const looksLikeSingle = !/[\s,;]+/.test(text);
      if (
        looksLikeSingle &&
        showResults &&
        results.length > 0 &&
        selectedIndex >= 0
      ) {
        event.preventDefault();
        handleSelect(results[selectedIndex].token, event);
        return;
      }

      if (!searchReady) return;

      const { tokens, leftover } = parseTokensFromText(text);

      // If we couldn't fully resolve the tail, let Enter choose the highlighted suggestion
      let selectedToken = null;
      let nextQuery = leftover;
      if (leftover && showResults && results.length > 0 && selectedIndex >= 0) {
        const leftoverWords = leftover.split(/[\s,;]+/).filter(Boolean);
        const lastLeftover = leftoverWords[leftoverWords.length - 1] || "";
        if (lastLeftover === segment) {
          const selected = results[selectedIndex].token;
          const selectedEntry = tokenLookup.get(selected);
          const leftoverNorm = normalizeSearchText(leftover);

          selectedToken = applyNegation(
            selected,
            isNegationPrefix(lastLeftover)
          );
          if (
            selectedEntry &&
            leftoverNorm &&
            (selectedEntry.labelNorm.startsWith(leftoverNorm) ||
              selectedEntry.tokenSuffixNorm.startsWith(leftoverNorm))
          ) {
            nextQuery = "";
          } else {
            leftoverWords.pop();
            nextQuery = leftoverWords.join(" ");
          }
        }
      }

      const toAdd = selectedToken ? [...tokens, selectedToken] : tokens;
      if (toAdd.length === 0) return;

      event.preventDefault();
      addMany(toAdd, "search");
      query = nextQuery;
      clearResults();
      handleInput();
      return;
    }

    if (!showResults || results.length === 0) return;

    if (event.key === "ArrowDown") {
      event.preventDefault();
      selectedIndex = (selectedIndex + 1) % results.length;
      scrollIntoView(selectedIndex);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      selectedIndex = (selectedIndex - 1 + results.length) % results.length;
      scrollIntoView(selectedIndex);
    } else if (event.key === "Escape") {
      showResults = false;
    }
  }

  function scrollIntoView(index) {
    const el = document.getElementById(`result-${index}`);
    if (el) {
      el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }

  function handleSelect(token, event = null) {
    const excludeMode =
      (event?.altKey ?? false) || isNegationPrefix(getActiveSegment(query));
    const finalToken = applyNegation(token, excludeMode);

    // If the user typed multiple words (e.g. "diana aat") and clicks a suggestion,
    // resolve and add everything we can in one shot.
    if (searchReady) {
      const words = query
        .trim()
        .split(/[\s,;]+/)
        .filter(Boolean);
      const prefix = words.length > 1 ? words.slice(0, -1).join(" ") : "";
      const prefixTokens = prefix ? parseTokensFromText(prefix).tokens : [];
      addMany([...prefixTokens, finalToken], "search");
    } else {
      addToken(finalToken, "search");
    }

    posthog.capture("search_result_selected", {
      token: finalToken,
      source: "search",
    });
    clearInput();
  }

  function handleFocus() {
    hasFocus = true;
    showResults =
      query.length > 0 || recognizedPreview.length > 0 || results.length > 0;
  }

  function handleClickOutside(event) {
    if (!event.target.closest(".search-container")) {
      showResults = false;
      hasFocus = false;
    }
  }

  function getTypeClass(type) {
    return type === "unit"
      ? "unit"
      : type === "item"
        ? "item"
        : type === "trait"
          ? "trait"
          : type === "equipped"
            ? "equipped"
            : "item";
  }

  onMount(async () => {
    try {
      const index = await getSearchIndex();
      searchIndex = index.map((e) => ({
        ...e,
        labelNorm: normalizeSearchText(e.label),
        labelNormLoose: normalizeSearchTextLoose(e.label),
        tokenSuffixNorm: normalizeSearchText(e.token.slice(2)),
        tokenSuffixNormLoose: normalizeSearchTextLoose(e.token.slice(2)),
        count: e.count || 0,
      }));
      exactLookup = new Map();
      tokenLookup = new Map();
      for (const entry of searchIndex) {
        if (entry.type !== "equipped") {
          if (entry.labelNorm && !exactLookup.has(entry.labelNorm))
            exactLookup.set(entry.labelNorm, entry.token);
          if (entry.labelNormLoose && !exactLookup.has(entry.labelNormLoose))
            exactLookup.set(entry.labelNormLoose, entry.token);
          if (entry.tokenSuffixNorm && !exactLookup.has(entry.tokenSuffixNorm))
            exactLookup.set(entry.tokenSuffixNorm, entry.token);
          if (
            entry.tokenSuffixNormLoose &&
            !exactLookup.has(entry.tokenSuffixNormLoose)
          )
            exactLookup.set(entry.tokenSuffixNormLoose, entry.token);
        }
        if (entry.token && !tokenLookup.has(entry.token))
          tokenLookup.set(entry.token, entry);
      }
      searchReady = true;
    } catch (e) {
      console.warn(
        "Failed to load search index, falling back to backend search:",
        e
      );
      searchReady = false;
    }
  });
</script>

<svelte:document on:click={handleClickOutside} />

<div class="search-wrapper">
  <div
    class="search-container"
    class:has-cue={hasFocus && searchReady}
    data-walkthrough="search"
  >
    <input
      type="text"
      bind:value={query}
      on:input={handleInput}
      on:focus={handleFocus}
      on:keydown={handleKeydown}
      placeholder="ashe -tryndamere3"
      autocomplete="off"
    />
    {#if hasFocus && searchReady}
      <div class="input-cue" aria-hidden="true">
        <kbd>Space</kbd><span class="cue-text">add</span>
        <span class="cue-dot">•</span>
        <kbd>Enter</kbd><span class="cue-text">add</span>
      </div>
    {/if}

    {#if showResults}
      <div class="search-results">
        {#if searchReady}
          <div class="search-meta">
            {#if recognizedPreview.length > 0}
              <span class="meta-label">Recognized</span>
              <div class="recognized-chips">
                {#each recognizedPreview as t (t.token)}
                  <span
                    class="pending-chip {getTypeClass(t.type)}"
                    transition:fly={{ y: -4, duration: 140 }}
                  >
                    {getDisplayName(t.type, t.label)}
                  </span>
                {/each}
              </div>
              {#if isNegationPrefix(currentSegment)}
                <span class="exclude-badge">Exclude</span>
              {/if}
              <span class="meta-action"
                ><kbd>Space</kbd> auto-adds • <kbd>Enter</kbd> adds</span
              >
            {:else}
              <span class="meta-label">Multi-add</span>
              <span class="meta-text"
                >Type multiple names — <kbd>Space</kbd> auto-adds completed terms</span
              >
              {#if isNegationPrefix(currentSegment)}
                <span class="exclude-badge">Exclude</span>
              {/if}
            {/if}
          </div>
        {/if}

        {#if results.length > 0}
          {#each results as result, i}
            <button
              id="result-{i}"
              class="search-result"
              class:selected={i === selectedIndex}
              on:click={(e) => handleSelect(result.token, e)}
              on:mouseenter={() => (selectedIndex = i)}
            >
              <span
                class="label"
                class:equipped-label={result.type === "equipped"}
              >
                {getResultLabel(result)}
              </span>
              <span class="meta">
                <span class="type-badge {getTypeClass(result.type)}"
                  >{result.type}</span
                >
                {#if result.count > 0}
                  <span>{result.count.toLocaleString()} games</span>
                {/if}
              </span>
            </button>
          {/each}
        {:else}
          <div class="search-result no-results">
            <span class="label">
              {#if recognizedPreview.length > 0 && !currentSegment}
                Type another name, or press Enter to add
              {:else}
                No results found
              {/if}
            </span>
          </div>
        {/if}
      </div>
    {/if}
  </div>
  <VoiceInput />
</div>

<style>
  .search-wrapper {
    display: flex;
    gap: 8px;
    align-items: center;
    flex: 1;
    min-width: 0;
  }

  .search-container {
    position: relative;
    flex: 1;
    min-width: 0;
  }

  .search-container.has-cue input {
    padding-right: 150px;
  }

  input {
    width: 100%;
    padding: 8px 12px;
    font-size: 13px;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--text-primary);
    outline: none;
    transition: all 0.2s ease;
    font-family: inherit;
  }

  input::placeholder {
    color: var(--text-tertiary);
  }

  input:focus {
    border-color: var(--accent);
    background: var(--bg-tertiary);
    box-shadow: 0 4px 16px rgba(0, 112, 243, 0.15);
  }

  .input-cue {
    position: absolute;
    right: 10px;
    top: 50%;
    transform: translateY(-50%);
    display: flex;
    align-items: center;
    gap: 6px;
    pointer-events: none;
    opacity: 0.85;
    z-index: 3;
  }

  .cue-text {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.02em;
    color: var(--text-tertiary);
    margin-right: 2px;
  }

  .cue-dot {
    color: var(--text-tertiary);
    opacity: 0.8;
    margin: 0 2px;
  }

  .search-results {
    position: absolute;
    top: calc(100% + 4px);
    left: 0;
    right: 0;
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: 8px;
    max-height: 320px;
    overflow-y: auto;
    z-index: 100;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
  }

  .search-meta {
    position: sticky;
    top: 0;
    z-index: 2;
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 12px;
    background: rgba(17, 17, 17, 0.92);
    backdrop-filter: blur(10px);
    border-bottom: 1px solid var(--border);
    flex-wrap: wrap;
  }

  .meta-label {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--text-tertiary);
  }

  .exclude-badge {
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 3px 8px;
    border-radius: 999px;
    border: 1px solid rgba(255, 68, 68, 0.35);
    background: rgba(255, 68, 68, 0.12);
    color: rgba(255, 120, 120, 0.95);
    white-space: nowrap;
  }

  .meta-text {
    font-size: 12px;
    color: var(--text-secondary);
    opacity: 0.9;
  }

  .meta-action {
    margin-left: auto;
    font-size: 12px;
    color: var(--text-secondary);
    opacity: 0.9;
    white-space: nowrap;
  }

  kbd {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 1px 6px;
    border-radius: 6px;
    border: 1px solid var(--border);
    background: rgba(0, 0, 0, 0.25);
    font-size: 11px;
    font-weight: 600;
    color: var(--text-secondary);
    line-height: 1.4;
  }

  .recognized-chips {
    display: flex;
    flex: 1;
    min-width: 0;
    gap: 6px;
    flex-wrap: wrap;
    align-items: center;
  }

  .pending-chip {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 5px 10px;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: rgba(255, 255, 255, 0.03);
    font-size: 11px;
    font-weight: 600;
    color: var(--text-primary);
    white-space: nowrap;
  }

  .pending-chip::before {
    content: "";
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--text-tertiary);
    opacity: 0.95;
    flex-shrink: 0;
  }

  .pending-chip.unit::before {
    background: var(--unit);
  }

  .pending-chip.item::before {
    background: var(--item);
  }

  .pending-chip.trait::before {
    background: var(--trait);
  }

  .pending-chip.equipped::before {
    background: var(--equipped);
  }

  .search-result {
    width: 100%;
    padding: 12px 16px;
    cursor: pointer;
    display: flex;
    justify-content: space-between;
    align-items: center;
    transition: background 0.15s ease;
    border: none;
    border-bottom: 1px solid var(--border);
    background: transparent;
    text-align: left;
    font-family: inherit;
  }

  .search-result:last-child {
    border-bottom: none;
  }

  .search-result:hover:not(.no-results),
  .search-result.selected:not(.no-results) {
    background: var(--bg-tertiary);
  }

  .search-result.no-results {
    cursor: default;
  }

  .label {
    font-weight: 500;
    font-size: 14px;
    color: var(--text-primary);
  }

  .label.equipped-label {
    font-weight: 650;
    letter-spacing: -0.01em;
  }

  .meta {
    font-size: 12px;
    color: var(--text-secondary);
    display: flex;
    gap: 8px;
    align-items: center;
  }

  .type-badge {
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .type-badge.unit {
    background: rgba(255, 107, 157, 0.15);
    color: var(--unit);
  }

  .type-badge.item {
    background: rgba(0, 217, 255, 0.15);
    color: var(--item);
  }

  .type-badge.trait {
    background: rgba(168, 85, 247, 0.15);
    color: var(--trait);
  }

  .type-badge.equipped {
    background: rgba(245, 166, 35, 0.15);
    color: var(--equipped);
  }

  .search-results::-webkit-scrollbar {
    width: 8px;
  }

  .search-results::-webkit-scrollbar-track {
    background: var(--bg-primary);
  }

  .search-results::-webkit-scrollbar-thumb {
    background: var(--border);
    border-radius: 4px;
  }

  @media (max-width: 768px) {
    .search-wrapper {
      flex: 1 1 100%;
      order: 1;
    }

    .search-container.has-cue input {
      padding-right: 12px;
    }

    .input-cue {
      display: none;
    }

    input {
      padding: 10px 14px;
      font-size: 14px;
    }
  }
</style>
