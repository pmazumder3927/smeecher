<script>
  import { onMount, onDestroy } from "svelte";
  import { createRealtimeVoice } from "../utils/realtimeVoice.js";
  import { addToken } from "../stores/state.js";
  import { getDisplayName } from "../stores/assets.js";
  import posthog from "../client/posthog";

  // Voice client state
  let voiceClient = null;
  let isSupported = false;

  // Reactive state
  let isConnected = false;
  let isListening = false;
  let errorMessage = null;
  let currentTranscript = "";
  let parsedTokens = [];
  let vocab = null;

  // Unsubscribe functions
  let unsubscribers = [];

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
    unsubscribers.forEach((fn) => fn());
    if (voiceClient) {
      voiceClient.disconnect();
    }
  });

  function handleToolCall(args) {
    const tokens = validateTokens(args);
    parsedTokens = tokens;

    if (tokens.length > 0) {
      for (const t of tokens) {
        addToken(t.token, "voice");
      }
      posthog.capture("voice_input", {
        transcript: currentTranscript,
        tokens_added: tokens.length,
        tokens: tokens.map((t) => t.token),
      });
    }

    // Disconnect after processing
    setTimeout(() => {
      if (voiceClient) {
        voiceClient.disconnect();
      }
      // Clear after brief display
      setTimeout(() => {
        parsedTokens = [];
        currentTranscript = "";
      }, 1500);
    }, 100);
  }

  function validateTokens(args) {
    if (!vocab) return [];

    const tokens = [];
    const seen = new Set();

    // Helper for fuzzy lookup
    function fuzzyLookup(name, lookup) {
      const key = name.toLowerCase().replace(/\s+/g, "");
      if (lookup[key]) return lookup[key];
      if (key.endsWith("s") && lookup[key.slice(0, -1)])
        return lookup[key.slice(0, -1)];
      return null;
    }

    // Units
    for (const unit of args.units || []) {
      const token = fuzzyLookup(unit, vocab.unit_lookup);
      if (token && !seen.has(token)) {
        seen.add(token);
        tokens.push({ token, label: unit, type: "unit" });
      }
    }

    // Items
    for (const item of args.items || []) {
      const token = fuzzyLookup(item, vocab.item_lookup);
      if (token && !seen.has(token)) {
        seen.add(token);
        tokens.push({ token, label: item, type: "item" });
      }
    }

    // Traits
    for (const trait of args.traits || []) {
      const name = typeof trait === "string" ? trait : trait.name;
      const tier = typeof trait === "object" ? trait.tier : null;
      const baseToken = fuzzyLookup(name, vocab.trait_lookup);
      if (baseToken && !seen.has(baseToken)) {
        seen.add(baseToken);
        const token = tier && tier >= 2 ? `${baseToken}:${tier}` : baseToken;
        tokens.push({
          token,
          label: tier ? `${name} ${tier}` : name,
          type: "trait",
        });
      }
    }

    return tokens;
  }

  async function toggleVoice() {
    if (isListening) {
      // Stop listening
      if (voiceClient) {
        voiceClient.disconnect();
      }
    } else {
      // Start listening
      parsedTokens = [];
      currentTranscript = "";
      errorMessage = null;

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

      await voiceClient.connect();
    }
  }

  function getTypeClass(type) {
    return type === "unit"
      ? "unit"
      : type === "item"
        ? "item"
        : type === "trait"
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
            <p class="hint-title">Say units, items, or traits:</p>
            <div class="hint-examples">
              <span class="hint-example">"Jinx, Guinsoo's and Dr. Mundo"</span>
              <span class="hint-example">"Demacia shyvana"</span>
              <span class="hint-example">"Miss Fortune Shojin"</span>
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
