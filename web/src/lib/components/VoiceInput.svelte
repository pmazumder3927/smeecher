<script>
  import { onMount, onDestroy } from "svelte";
  import { createRealtimeVoice } from "../utils/realtimeVoice.js";
  import {
    addToken,
    equipItemOnUnit,
    excludeItemOnUnit,
    clusterExplorerOpen,
    clusterExplorerRunRequest,
    itemExplorerOpen,
    itemExplorerTab,
    itemExplorerSortMode,
    itemExplorerUnit,
    setItemTypeFilters,
  } from "../stores/state.js";
  import { getDisplayName } from "../stores/assets.js";
  import { parseToken } from "../utils/tokens.js";
  import posthog from "../client/posthog";

  // Voice client state
  let voiceClient = null;
  let isSupported = false;
  let isStarting = false;
  let lastStartAt = 0;
  const START_COOLDOWN_MS = 1500;

  // Reactive state
  let isConnected = false;
  let isListening = false;
  let errorMessage = null;
  let currentTranscript = "";
  let parsedTokens = [];
  let vocab = null;

  // Unsubscribe functions
  let unsubscribers = [];

  const ITEM_TYPE_LABELS = {
    full: "Full items",
    radiant: "Radiant",
    artifact: "Artifacts",
    emblem: "Emblems",
    component: "Components",
  };

  // Helper for fuzzy lookup
  function fuzzyLookup(name, lookup) {
    if (!name || !lookup) return null;
    const raw = String(name).toLowerCase();
    const keys = [
      raw.replace(/\s+/g, ""),
      raw.replace(/[^a-z0-9]/g, ""),
    ].filter(Boolean);

    for (const key of keys) {
      if (lookup[key]) return lookup[key];
      if (key.endsWith("s") && lookup[key.slice(0, -1)]) return lookup[key.slice(0, -1)];
      if (key.endsWith("es") && lookup[key.slice(0, -2)]) return lookup[key.slice(0, -2)];
    }

    return null;
  }

  onMount(async () => {
    if (typeof window !== "undefined" && window.RTCPeerConnection) {
      isSupported = true;

      // Fetch vocabulary for token validation
      try {
        const res = await fetch("/voice-vocab");
        if (res.ok) {
          vocab = await res.json();
        }
      } catch (e) {
        console.warn("Failed to fetch voice vocab:", e);
      }
    }
  });

  onDestroy(() => {
    cleanupVoiceClient();
  });

  function cleanupVoiceClient() {
    if (voiceClient) {
      voiceClient.disconnect();
      voiceClient = null;
    }
    unsubscribers.forEach((fn) => fn());
    unsubscribers = [];
    isStarting = false;
  }

  function handleToolCall(args) {
    const includeArgs = {
      units: Array.isArray(args?.units) ? args.units : [],
      items: Array.isArray(args?.items) ? args.items : [],
      traits: Array.isArray(args?.traits) ? args.traits : [],
    };

    const excludeArgs = {
      units: Array.isArray(args?.exclude_units)
        ? args.exclude_units
        : Array.isArray(args?.excludeUnits)
          ? args.excludeUnits
          : [],
      items: Array.isArray(args?.exclude_items)
        ? args.exclude_items
        : Array.isArray(args?.excludeItems)
          ? args.excludeItems
          : [],
      traits: Array.isArray(args?.exclude_traits)
        ? args.exclude_traits
        : Array.isArray(args?.excludeTraits)
          ? args.excludeTraits
          : [],
    };

    const includeTokens = validateTokens(includeArgs);
    const excludeTokens = validateTokens(excludeArgs, { negated: true });

    const equippedIn = Array.isArray(args?.equipped) ? args.equipped : [];
    const equippedOut = Array.isArray(args?.exclude_equipped)
      ? args.exclude_equipped
      : Array.isArray(args?.excludeEquipped)
        ? args.excludeEquipped
        : [];

    const equipped = deriveEquippedPairs(equippedIn, currentTranscript, includeTokens);
    const excludedEquipped = deriveEquippedPairs(equippedOut, "", excludeTokens);

    // If the user specified a star-level unit in an equipped pair (e.g. "Tryndamere 2★ with Guinsoo's"),
    // also add that star-level unit token as an explicit filter.
    const tokenSet = new Set(includeTokens.map((t) => t.token));
    for (const e of equipped) {
      if (!e?.unitToken?.startsWith?.("U:")) continue;
      const parsed = parseToken(e.unitToken);
      if (parsed?.type !== "unit" || !parsed.stars) continue;
      if (tokenSet.has(e.unitToken)) continue;
      tokenSet.add(e.unitToken);
      includeTokens.push({
        token: e.unitToken,
        label: `${getDisplayName("unit", parsed.unit)} ${parsed.stars}★`,
        type: "unit",
      });
    }

    const equippedItemNames = new Set([
      ...equipped.map((e) => e.item),
      ...excludedEquipped.map((e) => e.item),
    ]);

    const includeTokensToAdd = includeTokens.filter(
      (t) => !(t.type === "item" && equippedItemNames.has(t.token.slice(2)))
    );
    const excludeTokensToAdd = excludeTokens.filter(
      (t) => !(t.type === "item" && equippedItemNames.has(t.token.slice(2)))
    );

    const equippedTokens = equipped.map((e) => `E:${e.unit}|${e.item}`);
    const excludedEquippedTokens = excludedEquipped.map((e) => `-E:${e.unit}|${e.item}`);

    const uiActions = deriveUiActions(args, currentTranscript, includeTokensToAdd, equipped);

    if (includeTokensToAdd.length > 0) {
      for (const t of includeTokensToAdd) {
        addToken(t.token, "voice");
      }
    }

    if (excludeTokensToAdd.length > 0) {
      for (const t of excludeTokensToAdd) {
        addToken(t.token, "voice");
      }
    }

    if (equipped.length > 0) {
      for (const e of equipped) {
        equipItemOnUnit(e.unit, e.item, "voice");
      }
    }

    if (excludedEquipped.length > 0) {
      for (const e of excludedEquipped) {
        excludeItemOnUnit(e.unit, e.item, "voice");
      }
    }

    // Apply UI actions after tokens so "focus unit" can select newly-added units.
    applyUiActions(uiActions);

    // Include UI actions in the preview so the user can see what happened.
    parsedTokens = [
      ...includeTokensToAdd,
      ...excludeTokensToAdd,
      ...previewEquipped(equipped),
      ...previewEquipped(excludedEquipped, { negated: true }),
      ...previewUiActions(uiActions),
    ];

    if (
      includeTokensToAdd.length > 0 ||
      excludeTokensToAdd.length > 0 ||
      equipped.length > 0 ||
      excludedEquipped.length > 0 ||
      uiActions?.didSomething
    ) {
      const allTokens = [
        ...includeTokensToAdd.map((t) => t.token),
        ...excludeTokensToAdd.map((t) => t.token),
        ...equippedTokens,
        ...excludedEquippedTokens,
      ];
      posthog.capture("voice_input", {
        transcript: currentTranscript,
        tokens_added: allTokens.length,
        tokens: allTokens,
        equipped: equipped,
        excluded_equipped: excludedEquipped,
        ui_actions: uiActions,
      });
    }

    // Disconnect after processing
    setTimeout(() => {
      cleanupVoiceClient();
      // Clear after brief display
      setTimeout(() => {
        parsedTokens = [];
        currentTranscript = "";
      }, 1500);
    }, 100);
  }

  function normalizeItemTypeKey(raw) {
    if (!raw) return null;
    const key = String(raw).trim().toLowerCase();
    if (key === "artifacts" || key === "artefact" || key === "artefacts")
      return "artifact";
    if (key === "components" || key === "component") return "component";
    if (key === "emblems" || key === "emblem") return "emblem";
    if (key === "radiants" || key === "radiant") return "radiant";
    if (key === "full" || key === "fullitems" || key === "full items")
      return "full";
    if (ITEM_TYPE_LABELS[key]) return key;
    return null;
  }

  function deriveUiActions(args, transcript, validatedTokens, equippedPairs) {
    const t = (transcript || "").toLowerCase();

    const hasCompIntent =
      /\b(comp|comps|composition|compositions|archetype|archetypes|cluster|clusters)\b/.test(
        t
      );
    const openCluster =
      args?.open_cluster_explorer === true ||
      args?.openClusterExplorer === true ||
      hasCompIntent;
    const runCluster =
      args?.run_cluster_explorer === true ||
      args?.runClusterExplorer === true ||
      hasCompIntent;

    const itemTypesRaw = Array.isArray(args?.item_types)
      ? args.item_types
      : Array.isArray(args?.itemTypes)
        ? args.itemTypes
        : [];
    const itemTypes = itemTypesRaw
      .map(normalizeItemTypeKey)
      .filter(Boolean);

    const hasItemIntent =
      /\b(items?|item|builds?|artifact|artifacts?|artefact|artefacts?|emblem|emblems|radiant|radiants|component|components|second item|third item|next item)\b/.test(
        t
      );

    const openItems =
      args?.open_item_explorer === true ||
      args?.openItemExplorer === true ||
      itemTypes.length > 0 ||
      typeof args?.item_explorer_tab === "string" ||
      typeof args?.item_explorer_sort_mode === "string" ||
      hasItemIntent;

    const tabRaw =
      args?.item_explorer_tab ??
      args?.itemExplorerTab ??
      (/\bbuilds?\b/.test(t) ? "builds" : null) ??
      (itemTypes.length > 0 || /\bitems?\b/.test(t) ? "items" : null);
    const tab = tabRaw === "builds" || tabRaw === "items" ? tabRaw : null;

    const sortRaw =
      args?.item_explorer_sort_mode ??
      args?.itemExplorerSortMode ??
      (openItems
        ? (/\bworst|bad\b/.test(t) ? "harmful" : null) ??
          (/\bimpact\b/.test(t) ? "impact" : null) ??
          (/\bbest|top\b/.test(t) ? "helpful" : null)
        : null);
    const sort =
      sortRaw === "helpful" || sortRaw === "harmful" || sortRaw === "impact"
        ? sortRaw
        : null;

    const focusUnit = openItems
      ? (() => {
          const unitTok = validatedTokens?.find?.((x) => x.type === "unit")?.token;
          if (unitTok?.startsWith?.("U:")) {
            const parsed = parseToken(unitTok);
            return parsed?.unit ?? null;
          }
          const raw =
            Array.isArray(args?.units) && args.units.length > 0 ? args.units[0] : null;
          if (typeof raw !== "string") return null;
          return raw.replace(/\s+\d+\s*★*\s*$/, "").trim() || null;
        })()
      : null;

    const didSomething =
      openItems || itemTypes.length > 0 || tab || sort || openCluster || runCluster;

    return {
      didSomething,
      open_item_explorer: openItems || undefined,
      item_explorer_tab: tab || undefined,
      item_explorer_sort_mode: sort || undefined,
      item_types: itemTypes.length > 0 ? itemTypes : undefined,
      focus_unit: focusUnit || undefined,
      open_cluster_explorer: openCluster || undefined,
      run_cluster_explorer: runCluster || undefined,
    };
  }

  function applyUiActions(actions) {
    if (!actions?.didSomething) return;

    if (actions.open_cluster_explorer || actions.run_cluster_explorer) {
      clusterExplorerOpen.set(true);
    }
    if (actions.run_cluster_explorer) {
      clusterExplorerRunRequest.update((n) => n + 1);
    }

    if (actions.open_item_explorer) {
      itemExplorerOpen.set(true);
    }

    if (actions.focus_unit) {
      itemExplorerUnit.set(actions.focus_unit);
    }

    if (actions.item_explorer_tab) {
      itemExplorerTab.set(actions.item_explorer_tab);
    } else if (actions.item_types?.length) {
      itemExplorerTab.set("items");
    }

    if (actions.item_explorer_sort_mode) {
      itemExplorerSortMode.set(actions.item_explorer_sort_mode);
    }

    if (actions.item_types?.length) {
      setItemTypeFilters(actions.item_types);
    }
  }

  function previewUiActions(actions) {
    const preview = [];
    if (!actions?.didSomething) return preview;

    if (actions.open_cluster_explorer) {
      preview.push({
        token: "ui:cluster_explorer",
        label: "Open Explorer panel",
        type: "ui",
      });
    }

    if (actions.run_cluster_explorer) {
      preview.push({
        token: "ui:cluster_explorer_run",
        label: "Run Explorer",
        type: "ui",
      });
    }

    if (actions.open_item_explorer) {
      preview.push({
        token: "ui:item_explorer",
        label: "Open Items panel",
        type: "ui",
      });
    }

    if (actions.item_types?.length) {
      for (const typeKey of actions.item_types) {
        preview.push({
          token: `filter:item_type:${typeKey}`,
          label: ITEM_TYPE_LABELS[typeKey] ?? typeKey,
          type: "item_filter",
        });
      }
    }

    if (actions.item_explorer_tab) {
      preview.push({
        token: `ui:item_explorer_tab:${actions.item_explorer_tab}`,
        label: `Tab: ${actions.item_explorer_tab}`,
        type: "ui",
      });
    }

    if (actions.item_explorer_sort_mode) {
      const label =
        actions.item_explorer_sort_mode === "helpful"
          ? "Best first"
          : actions.item_explorer_sort_mode === "harmful"
            ? "Worst first"
            : "Most impact";
      preview.push({
        token: `ui:item_explorer_sort:${actions.item_explorer_sort_mode}`,
        label: `Sort: ${label}`,
        type: "ui",
      });
    }

    if (actions.focus_unit) {
      preview.push({
        token: `ui:focus_unit:${actions.focus_unit}`,
        label: `Focus: ${actions.focus_unit}`,
        type: "ui",
      });
    }

    return preview;
  }

  function deriveEquippedPairs(rawEquipped, transcript, validatedTokens) {
    if (!vocab) return [];

    const pairs = [];

    for (const entry of rawEquipped) {
      if (!entry || typeof entry !== "object") continue;
      const unitName = entry.unit ?? entry.champion ?? null;
      const itemName = entry.item ?? null;
      if (!unitName || !itemName) continue;

      const unitToken = fuzzyLookup(unitName, vocab.unit_lookup);
      const itemToken = fuzzyLookup(itemName, vocab.item_lookup);
      if (!unitToken?.startsWith?.("U:")) continue;
      if (!itemToken?.startsWith?.("I:")) continue;
      const parsedUnit = parseToken(unitToken);
      pairs.push({
        unit: parsedUnit?.unit ?? unitToken.slice(2),
        unitToken,
        item: itemToken.slice(2),
      });
    }

    if (pairs.length > 0) return pairs;

    const t = (transcript || "").toLowerCase();
    const looksEquipped =
      /\b(with|holding|equipped|equipped with|has|wearing)\b/.test(t);
    if (!looksEquipped) return [];

    const units = validatedTokens.filter((x) => x.type === "unit");
    const items = validatedTokens.filter((x) => x.type === "item");
    if (units.length === 0 || items.length === 0) return [];

    const baseUnits = new Set();
    for (const u of units) {
      if (!u?.token?.startsWith?.("U:")) continue;
      const parsed = parseToken(u.token);
      if (parsed?.type === "unit" && parsed.unit) baseUnits.add(parsed.unit);
    }
    if (baseUnits.size !== 1) return [];
    const [unit] = Array.from(baseUnits);

    return items.map((i) => ({ unit, item: i.token.slice(2) }));
  }

  function previewEquipped(pairs, options = {}) {
    if (!Array.isArray(pairs) || pairs.length === 0) return [];
    const negated = !!options?.negated;
    return pairs.map((p) => {
      const label = `${getDisplayName("unit", p.unit)} → ${getDisplayName(
        "item",
        p.item
      )}`;
      return {
        token: `${negated ? "-" : ""}E:${p.unit}|${p.item}`,
        label: negated ? `Not ${label}` : label,
        type: "equipped",
      };
    });
  }

  function validateTokens(args, options = {}) {
    if (!vocab) return [];

    const negated = !!options?.negated;
    const tokens = [];
    const seen = new Set();

    // Units
    for (const unit of args.units || []) {
      const token = fuzzyLookup(unit, vocab.unit_lookup);
      if (token && !seen.has(token)) {
        seen.add(token);
        tokens.push({
          token: `${negated ? "-" : ""}${token}`,
          label: negated ? `Not ${unit}` : unit,
          type: "unit",
        });
      }
    }

    // Items
    for (const item of args.items || []) {
      const token = fuzzyLookup(item, vocab.item_lookup);
      if (token && !seen.has(token)) {
        seen.add(token);
        tokens.push({
          token: `${negated ? "-" : ""}${token}`,
          label: negated ? `Not ${item}` : item,
          type: "item",
        });
      }
    }

    // Traits
    for (const trait of args.traits || []) {
      const name = typeof trait === "string" ? trait : trait.name;
      const tier = typeof trait === "object" ? trait.tier : null;
      const baseToken = fuzzyLookup(name, vocab.trait_lookup);
      if (!baseToken) continue;

      let token = baseToken;
      let label = name;

      const tierNum = tier == null ? null : parseInt(tier, 10);
      if (Number.isFinite(tierNum)) {
        const tierToken = vocab.trait_tier_lookup
          ? fuzzyLookup(`${name} ${tierNum}`, vocab.trait_tier_lookup)
          : null;
        if (tierToken) {
          token = tierToken;
          label = `${name} ${tierNum}`;
        } else if (tierNum >= 2) {
          // Back-compat fallback: treat as internal tier index.
          token = `${baseToken}:${tierNum}`;
          label = `${name} ${tierNum}`;
        }
      }

      if (!seen.has(token)) {
        seen.add(token);
        tokens.push({
          token: `${negated ? "-" : ""}${token}`,
          label: negated ? `Not ${label}` : label,
          type: "trait",
        });
      }
    }

    return tokens;
  }

  async function toggleVoice() {
    if (isListening || isStarting) {
      // Stop listening
      cleanupVoiceClient();
    } else {
      const now = Date.now();
      if (now - lastStartAt < START_COOLDOWN_MS) {
        errorMessage = "Please wait a moment before using voice again.";
        return;
      }
      lastStartAt = now;

      // Start listening
      parsedTokens = [];
      currentTranscript = "";
      errorMessage = null;

      cleanupVoiceClient();
      voiceClient = createRealtimeVoice(handleToolCall);

      // Subscribe to stores
      unsubscribers.push(
        voiceClient.isConnected.subscribe((v) => (isConnected = v))
      );
      unsubscribers.push(
        voiceClient.isListening.subscribe((v) => (isListening = v))
      );
      unsubscribers.push(
        voiceClient.error.subscribe((v) => (errorMessage = v))
      );
      unsubscribers.push(
        voiceClient.transcript.subscribe((v) => (currentTranscript = v))
      );

      isStarting = true;
      await voiceClient.connect();
      isStarting = false;
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
          : type === "item_filter"
            ? "item"
            : type === "ui"
              ? "trait"
          : "item";
  }

  // Keyboard handler for spacebar
  function handleKeyDown(event) {
    // Don't trigger if typing in an input
    if (event.target.tagName === "INPUT" || event.target.tagName === "TEXTAREA") {
      return;
    }

    if (event.code === "Space" && !event.repeat && !isListening) {
      event.preventDefault();
      toggleVoice();
    }
  }

  function handleKeyUp(event) {
    if (event.code === "Space" && isListening) {
      event.preventDefault();
      toggleVoice();
    }
  }
</script>

<svelte:window on:keydown={handleKeyDown} on:keyup={handleKeyUp} />

{#if isSupported}
  <div class="voice-input">
    <button
      class="voice-btn"
      class:listening={isListening}
      class:connected={isConnected}
      on:click={toggleVoice}
      title={isListening ? "Release to stop (Space)" : "Hold Space or click to speak"}
      aria-label="Voice input"
    >
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
      >
        <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
        <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
        <line x1="12" y1="19" x2="12" y2="23" />
        <line x1="8" y1="23" x2="16" y2="23" />
      </svg>
      {#if isListening}
        <span class="pulse"></span>
      {/if}
    </button>
    <span class="kbd-hint" class:listening={isListening}>Space</span>

    {#if isListening || parsedTokens.length > 0}
      <div class="voice-overlay">
        <div class="overlay-header">
          {#if isListening && !isConnected}
            <span class="connecting-indicator">Connecting...</span>
          {:else if isListening}
            <span class="listening-indicator">Listening...</span>
          {:else}
            <span class="done-indicator">Added!</span>
          {/if}
        </div>

	        {#if isListening && !currentTranscript}
	          <div class="voice-hints">
	            <p class="hint-title">Try saying:</p>
		            <div class="hint-examples">
		              <span class="hint-example">"Ashe best artifacts"</span>
		              <span class="hint-example">"Best Yasuo comp"</span>
		              <span class="hint-example">"Yasuo with Infinity Edge best second item"</span>
		              <span class="hint-example">"Tryndamere 2 without Ashe 3"</span>
		              <span class="hint-example">"5 Demacia Shyvana"</span>
		            </div>
		          </div>
		        {/if}

        {#if currentTranscript}
          <div class="transcript">
            "{currentTranscript}"
          </div>
        {/if}

        {#if parsedTokens.length > 0}
          <div class="parsed-tokens">
            {#each parsedTokens as token}
              <span class="preview-chip {getTypeClass(token.type)}">
                {getDisplayName(token.type, token.label)}
              </span>
            {/each}
          </div>
        {:else if currentTranscript && !isListening}
          <div class="no-matches">No matches found</div>
        {/if}
      </div>
    {/if}

    {#if errorMessage}
      <div class="error-toast">{errorMessage}</div>
    {/if}
  </div>
{/if}

<style>
  .voice-input {
    position: relative;
    display: flex;
    align-items: center;
  }

  .voice-btn {
    width: 36px;
    height: 36px;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: var(--bg-tertiary);
    color: var(--text-secondary);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.2s ease;
    position: relative;
    overflow: hidden;
  }

  .voice-btn:hover {
    border-color: var(--accent);
    color: var(--accent);
    background: rgba(0, 112, 243, 0.1);
  }

  .voice-btn.listening {
    border-color: #22c55e;
    background: rgba(34, 197, 94, 0.15);
    color: #22c55e;
  }

  .voice-btn.connected {
    border-color: #22c55e;
  }

  .kbd-hint {
    font-size: 11px;
    font-weight: 600;
    font-family: ui-monospace, monospace;
    color: var(--text-secondary);
    background: var(--bg-tertiary);
    padding: 4px 8px;
    border-radius: 4px;
    border: 1px solid var(--border);
    margin-left: 8px;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.2);
  }

  .kbd-hint.listening {
    color: #22c55e;
    border-color: rgba(34, 197, 94, 0.4);
    background: rgba(34, 197, 94, 0.1);
  }

  .voice-btn svg {
    width: 18px;
    height: 18px;
  }

  .pulse {
    position: absolute;
    inset: -4px;
    border-radius: 12px;
    border: 2px solid #22c55e;
    animation: pulse 1.5s ease-out infinite;
  }

  @keyframes pulse {
    0% {
      opacity: 1;
      transform: scale(1);
    }
    100% {
      opacity: 0;
      transform: scale(1.3);
    }
  }

  .voice-overlay {
    position: absolute;
    top: calc(100% + 8px);
    right: 0;
    min-width: 280px;
    max-width: 400px;
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px;
    z-index: 1000;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
    animation: slideIn 0.15s ease-out;
  }

  @keyframes slideIn {
    from {
      opacity: 0;
      transform: translateY(-8px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }

  .overlay-header {
    margin-bottom: 12px;
  }

  .connecting-indicator {
    color: var(--text-secondary);
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.1em;
  }

  .listening-indicator {
    color: #22c55e;
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .listening-indicator::before {
    content: "";
    width: 8px;
    height: 8px;
    background: #22c55e;
    border-radius: 50%;
    animation: blink 1s infinite;
  }

  @keyframes blink {
    0%,
    50% {
      opacity: 1;
    }
    51%,
    100% {
      opacity: 0.3;
    }
  }

  .done-indicator {
    color: #22c55e;
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.1em;
  }

  .transcript {
    font-size: 14px;
    color: var(--text-primary);
    font-style: italic;
    margin-bottom: 12px;
    padding: 8px 12px;
    background: var(--bg-tertiary);
    border-radius: 6px;
  }

  .parsed-tokens {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  .preview-chip {
    display: inline-flex;
    align-items: center;
    padding: 4px 10px;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    border-radius: 5px;
    font-size: 12px;
    font-weight: 500;
    position: relative;
    color: var(--text-primary);
  }

  .preview-chip::before {
    content: "";
    position: absolute;
    left: 0;
    top: 0;
    bottom: 0;
    width: 3px;
    border-radius: 5px 0 0 5px;
  }

  .preview-chip.unit::before {
    background: var(--unit);
  }

  .preview-chip.item::before {
    background: var(--item);
  }

  .preview-chip.trait::before {
    background: var(--trait);
  }

  .preview-chip.equipped::before {
    background: var(--equipped);
  }

  .voice-hints {
    text-align: center;
  }

  .hint-title {
    font-size: 12px;
    color: var(--text-secondary);
    margin: 0 0 10px 0;
  }

  .hint-examples {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .hint-example {
    font-size: 13px;
    color: var(--text-tertiary);
    font-style: italic;
    padding: 6px 10px;
    background: var(--bg-tertiary);
    border-radius: 6px;
    border: 1px dashed var(--border);
  }

  .no-matches {
    color: var(--text-tertiary);
    font-size: 12px;
    font-style: italic;
  }

  .error-toast {
    position: absolute;
    top: calc(100% + 8px);
    right: 0;
    background: rgba(255, 68, 68, 0.95);
    color: white;
    padding: 8px 14px;
    border-radius: 8px;
    font-size: 12px;
    font-weight: 500;
    z-index: 1001;
    animation: fadeIn 0.2s ease-out;
    white-space: nowrap;
  }

  @keyframes fadeIn {
    from {
      opacity: 0;
    }
    to {
      opacity: 1;
    }
  }

  @media (max-width: 768px) {
    .voice-overlay {
      right: auto;
      left: 50%;
      transform: translateX(-50%);
      min-width: 260px;
    }

    @keyframes slideIn {
      from {
        opacity: 0;
        transform: translateX(-50%) translateY(-8px);
      }
      to {
        opacity: 1;
        transform: translateX(-50%) translateY(0);
      }
    }
  }
</style>
