<script>
  import { tick } from "svelte";
  import { createEventDispatcher } from "svelte";
  import {
    clearTokens,
    setTokens,
    selectedTokens,
    sortMode,
    topK,
    activeTypes,
    lastAction,
  } from "../stores/state.js";

  export let open = false;

  const dispatch = createEventDispatcher();

  const steps = [
    {
      id: "welcome",
      title: "Welcome",
      target: null,
      body: "This is a passion project I built to make stats exploration more fun. It's a work in progress, so there may be bugs and missing features. For now, let's get started.",
      primaryLabel: "Start",
    },
    {
      id: "pick-unit",
      title: "Add a champion",
      target: "search",
      task: "Search a champion and select it.",
      hint: "Try “Lissandra or Fiddlesticks”.",
      done: "Nice, the graph is now filtered for the champion selected",
    },
    {
      id: "click-node",
      title: "Explore the graph",
      body: "This graph shows Avg Placement (AVP) relationships from your filters to other nodes. Nodes with more impact are closer to the center, while nodes with less impact are further away and smaller.",
      target: "graph",
      task: "To continue, click another node in the graph.",
      done: "Now you have an updated graph with your two selected filters",
    },
    {
      id: "filters",
      title: "Edit filters",
      target: "filters",
      task: "Remove the last filter you added (×).",
      hint: "This will take you back to the previous graph",
      done: "Filters combine as AND. Add/remove one thing at a time to see what changes.",
    },
    {
      id: "sort",
      title: "Change what you see",
      target: "sortMode",
      task: "Click “Helpful”.",
      body: "Here you can change the sorting of the nodes displayed from your current spot. Impactful will show you nodes with the most absolute delta in placement. Helpful will show you nodes with the most negative delta in placement. Harmful will show you nodes with the most positive delta in placement.",
      done: "Now you can see the nodes that have the highest positive impact on your placement",
    },
    {
      id: "clusters",
      title: "Find high placement clusters",
      target: "clusterExplorer",
      body: "The explorer is a very powerful tool that takes your current filters and finds groups of nodes that can be played with them to minimize your avp. It allows you to see what you might want to play towards from a given spot.",
      task: "Open Explorer and click Run.",
      done: "Clusters are algorithmically-generated archetypes. Click one to see more details about what it contains",
    },
    {
      id: "types",
      title: "Filtering by type",
      target: "typeToggles",
      task: "Toggle Traits off.",
      body: "You can filter the graph by type to focus on only the nodes you're interested in. This is useful when the graph gets dense or you only want to see units, items, or traits.",
      done: "Use type toggles to focus the graph when it gets dense.",
    },
    {
      id: "limit",
      title: "Showing more/less nodes",
      target: "limit",
      body: "The limit controls how many nodes/relationships show up from your current branch. Increasing it will show you more nodes, but it will also make the graph slower",
      task: "Change Limit (try increasing it).",
      done: "Limit controls how many nodes/relationships show up per refresh.",
    },
    {
      id: "items",
      title: "Find item builds",
      target: "itemExplorer",
      task: "Open Items and click a Build (or add an item).",
      hint: "Builds applies a full set; Items adds a single Unit → Item filter.",
      done: "Great — now you can ask “best items for this unit in this exact context?”.",
    },
    {
      id: "done",
      title: "Done",
      target: null,
      body: "Re-open this tour anytime from the header.",
      primaryLabel: "Finish",
    },
  ];

  let stepIndex = 0;
  let spotlight = null;
  let missingTarget = false;
  let cardEl;
  let cardStyle = "";
  let rafId = null;
  let lastOpen = false;
  let resizeObserver;
  let mutationObserver;
  let savedTokens = null;

  let baseline = {
    lastActionTs: 0,
    topK: 0,
  };

  let stepDone = false;
  let canNext = true;
  let lastStepDone = false;

  const clamp = (n, min, max) => Math.max(min, Math.min(max, n));

  $: if (open && !lastOpen) {
    lastOpen = true;
    goToStep(0);
  }

  $: if (!open && lastOpen) {
    lastOpen = false;
    spotlight = null;
    missingTarget = false;
    stepIndex = 0;
    cardStyle = "";
    baseline = { lastActionTs: 0, topK: 0 };
    stepDone = false;
    canNext = true;
    teardownObservers();
  }

  function close(markSeen = true) {
    dispatch("close", { markSeen });
  }

  function finish() {
    close(true);
  }

  function isStepComplete(step, baselineState, last, mode, k, types) {
    if (!step?.task) return true;

    switch (step.id) {
      case "pick-unit": {
        const fresh =
          (last?.timestamp ?? 0) > (baselineState?.lastActionTs ?? 0);
        const okSource = last?.source === "search" || last?.source === "voice";
        const addedUnit =
          (last?.type === "token_added" &&
            typeof last?.token === "string" &&
            last.token.startsWith("U:")) ||
          (last?.type === "tokens_added" &&
            Array.isArray(last?.tokens) &&
            last.tokens.some(
              (t) => typeof t === "string" && t.startsWith("U:")
            ));
        return fresh && okSource && addedUnit;
      }

      case "click-node": {
        const fresh =
          (last?.timestamp ?? 0) > (baselineState?.lastActionTs ?? 0);
        return (
          fresh && last?.source === "graph" && last?.type === "token_added"
        );
      }

      case "filters": {
        const fresh =
          (last?.timestamp ?? 0) > (baselineState?.lastActionTs ?? 0);
        return fresh && last?.type === "token_removed";
      }

      case "sort":
        return mode === "helpful";

      case "types":
        return !types?.has?.("trait");

      case "limit":
        return k !== (baselineState?.topK ?? k);

      case "items": {
        const fresh =
          (last?.timestamp ?? 0) > (baselineState?.lastActionTs ?? 0);
        const okSource =
          last?.source === "item_explorer" ||
          last?.source === "item_explorer_build";
        const okType =
          last?.type === "token_added" || last?.type === "tokens_added";
        return fresh && okSource && okType;
      }

      case "clusters": {
        const fresh =
          (last?.timestamp ?? 0) > (baselineState?.lastActionTs ?? 0);
        if (!fresh) return false;
        if (last?.source !== "cluster") return false;
        if (last?.type === "clusters_run") return true;
        return last?.type === "tokens_set" || last?.type === "tokens_added";
      }

      default:
        return true;
    }
  }

  $: stepDone = open
    ? isStepComplete(
        steps[stepIndex],
        baseline,
        $lastAction,
        $sortMode,
        $topK,
        $activeTypes
      )
    : false;

  $: canNext = !steps[stepIndex]?.task || stepDone;

  $: if (open && stepDone !== lastStepDone) {
    lastStepDone = stepDone;
    tick().then(scheduleUpdate);
  }

  async function goToStep(nextIndex) {
    stepIndex = clamp(nextIndex, 0, steps.length - 1);
    await tick();

    const step = steps[stepIndex];
    const target = step?.target;
    if (target) {
      const el = document.querySelector(`[data-walkthrough="${target}"]`);
      if (el?.scrollIntoView) {
        el.scrollIntoView({
          behavior: "smooth",
          block: "center",
          inline: "nearest",
        });
        await new Promise(requestAnimationFrame);
      }
    }

    await tick();
    updatePositions();
    setupObserversForStep();

    baseline = {
      lastActionTs: $lastAction?.timestamp ?? 0,
      topK: $topK,
    };

    if (step?.target === "search") {
      try {
        const input = document.querySelector(
          '[data-walkthrough="search"] input'
        );
        input?.focus?.();
      } catch {
        // ignore
      }
    }
  }

  function next() {
    if (!canNext) return;
    if (stepIndex >= steps.length - 1) return finish();
    goToStep(stepIndex + 1);
  }

  function back() {
    if (stepIndex <= 0) return;
    goToStep(stepIndex - 1);
  }

  function scheduleUpdate() {
    if (!open) return;
    if (rafId) return;
    rafId = requestAnimationFrame(() => {
      rafId = null;
      updatePositions();
    });
  }

  function teardownObservers() {
    if (resizeObserver) {
      resizeObserver.disconnect();
      resizeObserver = null;
    }
    if (mutationObserver) {
      mutationObserver.disconnect();
      mutationObserver = null;
    }
  }

  function getStepElements(step) {
    if (!step?.target) return [];
    const root = document.querySelector(`[data-walkthrough="${step.target}"]`);
    if (!root) return [];

    const els = [root];

    // Search step needs the dropdown too (it overflows the container, so its rect is separate).
    if (step.target === "search") {
      const dropdown = root.querySelector(".search-results");
      if (dropdown) els.push(dropdown);
    }

    return els;
  }

  function setupObserversForStep() {
    teardownObservers();
    if (!open) return;

    const step = steps[stepIndex];
    const els = getStepElements(step);
    if (els.length === 0) return;

    resizeObserver = new ResizeObserver(() => scheduleUpdate());
    for (const el of els) resizeObserver.observe(el);

    // Only watch DOM changes for lightweight targets (avoid observing the graph subtree).
    if (step?.target === "search") {
      const root = els[0];
      mutationObserver = new MutationObserver(() => {
        // Dropdown appears/disappears; refresh observed elements and reposition.
        setupObserversForStep();
        scheduleUpdate();
      });
      mutationObserver.observe(root, { childList: true, subtree: true });
    }
  }

  function updatePositions() {
    if (!open) return;

    const step = steps[stepIndex];
    missingTarget = false;

    if (!step?.target) {
      spotlight = null;
      cardStyle = centeredCardStyle();
      return;
    }

    const elements = getStepElements(step);
    if (elements.length === 0) {
      spotlight = null;
      missingTarget = true;
      cardStyle = centeredCardStyle();
      return;
    }

    const rects = elements
      .map((e) => e.getBoundingClientRect())
      .filter((r) => r.width > 0 && r.height > 0);

    if (rects.length === 0) {
      spotlight = null;
      missingTarget = true;
      cardStyle = centeredCardStyle();
      return;
    }

    const rect = rects.reduce(
      (acc, r) => {
        const left = Math.min(acc.left, r.left);
        const top = Math.min(acc.top, r.top);
        const right = Math.max(acc.right, r.right);
        const bottom = Math.max(acc.bottom, r.bottom);
        return {
          left,
          top,
          right,
          bottom,
          width: right - left,
          height: bottom - top,
        };
      },
      {
        left: rects[0].left,
        top: rects[0].top,
        right: rects[0].right,
        bottom: rects[0].bottom,
        width: rects[0].width,
        height: rects[0].height,
      }
    );

    const pad = 10;
    spotlight = {
      left: Math.round(rect.left - pad),
      top: Math.round(rect.top - pad),
      width: Math.round(rect.width + pad * 2),
      height: Math.round(rect.height + pad * 2),
    };

    cardStyle = anchoredCardStyle(rect);
  }

  function centeredCardStyle() {
    const margin = 16;
    const vw = window.innerWidth || 1200;
    const vh = window.innerHeight || 800;
    const cardWidth = Math.min(420, vw - margin * 2);
    const left = Math.round((vw - cardWidth) / 2);
    const top = Math.round(Math.max(margin, vh * 0.18));
    return `left:${left}px; top:${top}px; width:${cardWidth}px;`;
  }

  function anchoredCardStyle(targetRect) {
    const vw = window.innerWidth || 1200;
    const vh = window.innerHeight || 800;
    const margin = 14;

    const measured = cardEl?.getBoundingClientRect();
    const cardWidth = Math.min(420, vw - margin * 2);
    const cardHeight = measured?.height ?? 220;

    const fitsRight = targetRect.right + margin + cardWidth <= vw - margin;
    const fitsLeft = targetRect.left - margin - cardWidth >= margin;
    const fitsBottom = targetRect.bottom + margin + cardHeight <= vh - margin;
    const fitsTop = targetRect.top - margin - cardHeight >= margin;

    if (fitsRight) {
      const left = Math.round(targetRect.right + margin);
      const top = Math.round(
        clamp(targetRect.top, margin, vh - margin - cardHeight)
      );
      return `left:${left}px; top:${top}px; width:${cardWidth}px;`;
    }

    if (fitsLeft) {
      const left = Math.round(targetRect.left - margin - cardWidth);
      const top = Math.round(
        clamp(targetRect.top, margin, vh - margin - cardHeight)
      );
      return `left:${left}px; top:${top}px; width:${cardWidth}px;`;
    }

    if (fitsBottom) {
      const left = Math.round(
        clamp(targetRect.left, margin, vw - margin - cardWidth)
      );
      const top = Math.round(targetRect.bottom + margin);
      return `left:${left}px; top:${top}px; width:${cardWidth}px;`;
    }

    if (fitsTop) {
      const left = Math.round(
        clamp(targetRect.left, margin, vw - margin - cardWidth)
      );
      const top = Math.round(targetRect.top - margin - cardHeight);
      return `left:${left}px; top:${top}px; width:${cardWidth}px;`;
    }

    return centeredCardStyle();
  }

  function handleKeydown(event) {
    if (!open) return;
    if (event.key === "Escape") {
      event.preventDefault();
      close(true);
      return;
    }
    if (event.key === "ArrowRight" || event.key === "Enter") {
      event.preventDefault();
      if (canNext) next();
      return;
    }
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      back();
    }
  }

  function resetAndStart() {
    clearTokens();
    goToStep(1);
  }

  function clearForExplorer() {
    if (!savedTokens) savedTokens = [...$selectedTokens];
    clearTokens();
  }

  function keepOnlyChampion() {
    if (!savedTokens) savedTokens = [...$selectedTokens];
    const tokens = $selectedTokens ?? [];
    let unitToken =
      tokens.find((t) => typeof t === "string" && t.startsWith("U:")) ?? null;
    if (!unitToken) {
      const equipped =
        tokens.find((t) => typeof t === "string" && t.startsWith("E:")) ?? null;
      if (equipped) {
        const unit = equipped.slice(2).split("|")[0];
        if (unit) unitToken = `U:${unit}`;
      }
    }
    if (unitToken) {
      setTokens([unitToken], "walkthrough");
    } else {
      clearTokens();
    }
  }

  function restoreFilters() {
    if (!savedTokens) return;
    setTokens(savedTokens, "walkthrough");
    savedTokens = null;
  }
</script>

<svelte:window
  on:resize={scheduleUpdate}
  on:scroll={scheduleUpdate}
  on:keydown={handleKeydown}
/>

{#if open}
  <div
    class="walkthrough"
    role="dialog"
    aria-modal="true"
    aria-label="Walkthrough"
  >
    <div class="backdrop"></div>
    {#if spotlight}
      <div
        class="spotlight"
        style={`left:${spotlight.left}px; top:${spotlight.top}px; width:${spotlight.width}px; height:${spotlight.height}px;`}
      ></div>
    {/if}

    <div class="card" bind:this={cardEl} style={cardStyle}>
      <div class="card-top">
        <div class="step-indicator">
          Step {stepIndex + 1} / {steps.length}
        </div>
        <button
          class="icon-btn"
          on:click={() => close(true)}
          aria-label="Close walkthrough">×</button
        >
      </div>

      <div class="title">{steps[stepIndex].title}</div>

      <div class="content">
        {#if missingTarget}
          <div class="callout">
            This part of the UI isn’t visible right now. Resize your window (or
            open the sidebar) and continue.
          </div>
        {/if}

        {#if steps[stepIndex].body}
          <div class="body">{steps[stepIndex].body}</div>
        {/if}

        {#if steps[stepIndex].task}
          <div class="task-row">
            <div class="task">{steps[stepIndex].task}</div>
            {#if stepDone}
              <div class="badge">Done</div>
            {/if}
          </div>
        {/if}

        {#if steps[stepIndex].hint && !stepDone}
          <div class="hint">{steps[stepIndex].hint}</div>
        {/if}

        {#if steps[stepIndex].done && stepDone}
          <div class="done">{steps[stepIndex].done}</div>
        {/if}

        {#if steps[stepIndex].id === "clusters"}
          <div class="cluster-tools">
            {#if $selectedTokens.length > 1}
              <button class="secondary tiny" on:click={keepOnlyChampion}
                >Keep champ only</button
              >
            {/if}
            <button class="secondary tiny" on:click={clearForExplorer}
              >Clear filters</button
            >
            {#if savedTokens}
              <button class="secondary tiny" on:click={restoreFilters}
                >Restore</button
              >
            {/if}
          </div>
        {/if}

        {#if steps[stepIndex].id === "welcome"}
          <button class="secondary" on:click={resetAndStart}
            >Reset filters</button
          >
        {/if}
      </div>

      <div class="nav">
        <button class="secondary" on:click={back} disabled={stepIndex === 0}
          >Back</button
        >
        <button class="primary" on:click={next} disabled={!canNext}>
          {steps[stepIndex].primaryLabel ??
            (stepIndex === steps.length - 1 ? "Finish" : "Next")}
        </button>
        <button class="link" on:click={() => close(true)}>Skip</button>
      </div>
    </div>
  </div>
{/if}

<style>
  .walkthrough {
    position: fixed;
    inset: 0;
    z-index: 2000;
    pointer-events: none;
  }

  .backdrop {
    position: absolute;
    inset: 0;
    background: rgba(0, 0, 0, 0.1);
  }

  .spotlight {
    position: absolute;
    border-radius: 14px;
    border: 1px solid rgba(0, 112, 243, 0.55);
    box-shadow:
      0 0 0 9999px rgba(0, 0, 0, 0.72),
      0 10px 40px rgba(0, 0, 0, 0.55);
    pointer-events: none;
    transition:
      left 0.18s ease,
      top 0.18s ease,
      width 0.18s ease,
      height 0.18s ease;
  }

  .card {
    position: fixed;
    background: rgba(17, 17, 17, 0.96);
    border: 1px solid var(--border);
    border-radius: 12px;
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.55);
    padding: 14px 14px 12px;
    color: var(--text-primary);
    pointer-events: auto;
    backdrop-filter: blur(10px);
  }

  .card-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 8px;
  }

  .step-indicator {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--text-tertiary);
  }

  .icon-btn {
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text-secondary);
    width: 28px;
    height: 28px;
    cursor: pointer;
    font-size: 18px;
    line-height: 1;
    display: grid;
    place-items: center;
    transition:
      background 0.15s ease,
      color 0.15s ease,
      border-color 0.15s ease;
  }

  .icon-btn:hover {
    background: var(--bg-tertiary);
    color: var(--text-primary);
    border-color: var(--border-hover);
  }

  .title {
    font-size: 16px;
    font-weight: 900;
    letter-spacing: -0.02em;
    margin-bottom: 10px;
  }

  .content {
    display: flex;
    flex-direction: column;
    gap: 10px;
    margin-bottom: 12px;
  }

  .callout {
    border: 1px solid rgba(245, 166, 35, 0.35);
    background: rgba(245, 166, 35, 0.08);
    color: rgba(245, 166, 35, 0.9);
    padding: 10px 12px;
    border-radius: 10px;
    font-size: 12px;
    line-height: 1.4;
  }

  .body {
    font-size: 12px;
    color: var(--text-secondary);
    line-height: 1.5;
  }

  .task-row {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 10px;
  }

  .task {
    font-size: 13px;
    font-weight: 800;
    line-height: 1.35;
    color: var(--text-primary);
  }

  .badge {
    flex: 0 0 auto;
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: rgba(0, 217, 165, 0.95);
    border: 1px solid rgba(0, 217, 165, 0.28);
    background: rgba(0, 217, 165, 0.1);
    padding: 4px 8px;
    border-radius: 999px;
    margin-top: 1px;
  }

  .hint {
    font-size: 11px;
    color: var(--text-tertiary);
    line-height: 1.4;
    border-left: 2px solid rgba(0, 112, 243, 0.6);
    padding-left: 10px;
  }

  .done {
    font-size: 12px;
    color: var(--text-secondary);
    line-height: 1.5;
  }

  .cluster-tools {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 2px;
  }

  button.secondary.tiny {
    padding: 6px 8px;
    font-size: 11px;
    border-radius: 8px;
  }

  .nav {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 10px;
  }

  button.primary,
  button.secondary {
    border-radius: 9px;
    border: 1px solid var(--border);
    padding: 8px 10px;
    font-size: 12px;
    font-weight: 700;
    cursor: pointer;
    font-family: inherit;
    transition:
      background 0.15s ease,
      color 0.15s ease,
      border-color 0.15s ease;
  }

  button.primary {
    background: var(--accent);
    border-color: transparent;
    color: #fff;
  }

  button.primary:hover {
    background: var(--accent-hover);
  }

  button.primary:disabled {
    opacity: 0.45;
    cursor: default;
  }

  button.secondary {
    background: transparent;
    color: var(--text-primary);
  }

  button.secondary:hover {
    background: var(--bg-tertiary);
    border-color: var(--border-hover);
  }

  button.secondary:disabled {
    opacity: 0.4;
    cursor: default;
  }

  button.link {
    background: none;
    border: none;
    color: var(--text-tertiary);
    font-size: 12px;
    cursor: pointer;
    padding: 6px 8px;
    font-family: inherit;
  }

  button.link:hover {
    color: var(--text-secondary);
    text-decoration: underline;
  }
</style>
