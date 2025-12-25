<script>
    import { tick } from 'svelte';
    import { createEventDispatcher } from 'svelte';
    import { clearTokens } from '../stores/state.js';

    export let open = false;

    const dispatch = createEventDispatcher();

    const STORAGE_KEY = 'smeecher_walkthrough_seen';

    const steps = [
        {
            id: 'welcome',
            title: 'Welcome to smeecher',
            target: null,
            bullets: [
                'Search for units, items, or traits to add filters.',
                'Your filters become the center of the graph; nearby nodes show what most strongly co-occurs.',
                'Green/negative Δ improves average placement; red/positive Δ worsens it.',
                'Use Items for best item builds; use Explorer for high-performing clusters (comp archetypes).'
            ]
        },
        {
            id: 'search',
            title: 'Start with Search',
            target: 'search',
            bullets: [
                'Type at least 2 characters, then click a result (or press Enter).',
                'Mix types freely: units + traits + items all work together as filters.',
                'Try it: add 1 carry unit you play, then add 1 trait you associate with it.'
            ],
            hint: 'Tip: If voice is enabled, hold Space and say “Jinx Guinsoo’s”.'
        },
        {
            id: 'filters',
            title: 'Understand Active Filters',
            target: 'filters',
            bullets: [
                'Each chip is a filter (they combine as AND). Remove with ×, or Clear all.',
                'If Games gets small, results get noisy—try removing filters to widen the sample.',
                'Equipped filters look like “Unit → Item” and mean that unit is holding the item.'
            ]
        },
        {
            id: 'graph',
            title: 'Read the Graph',
            target: 'graph',
            bullets: [
                'Center nodes are your current filters (always shown).',
                'Click any node to add it as a new filter and re-center the graph.',
                'Hover nodes/edges for Avg Placement Δ and sample size.',
                'Drag to pan; scroll/pinch to zoom.'
            ],
            hint: 'Edges: thicker = larger impact. Negative Δ = better (lower) average placement.'
        },
        {
            id: 'sort',
            title: 'Sort for What You Want',
            target: 'sortMode',
            bullets: [
                'Impact: show the strongest movers (good or bad).',
                'Helpful: surface the biggest improvements (most negative Δ).',
                'Harmful: surface the biggest placement traps (most positive Δ).'
            ]
        },
        {
            id: 'types',
            title: 'Reduce Visual Noise',
            target: 'typeToggles',
            bullets: [
                'Toggle Units / Items / Traits to focus the graph.',
                'Center nodes stay visible even when you hide a type.',
                'Use this when the graph is too dense to read quickly.'
            ]
        },
        {
            id: 'limit',
            title: 'Limit How Much Shows Up',
            target: 'limit',
            bullets: [
                'Limit controls how many nodes/relationships you pull in per update.',
                'Lower it to focus; raise it (or set 0) to explore more.'
            ]
        },
        {
            id: 'items',
            title: 'Find Best Item Builds',
            target: 'itemExplorer',
            bullets: [
                'Add at least 1 unit filter, then open Items.',
                'Builds tab: click a row to apply a full build as filters.',
                'Items tab: click an item to add a single “Unit → Item” filter.',
                'Results respect your other filters (traits/items), so you can ask “best items in this spot?”.'
            ]
        },
        {
            id: 'clusters',
            title: 'Find High Placement Clusters',
            target: 'clusterExplorer',
            bullets: [
                'Open Explorer and click Run to discover archetypes for the current filters.',
                'Sort by best avg or best top4 to find high performers.',
                'Select a cluster to see its Signature tokens and top units/traits/items.',
                'Use Replace/Add to move a cluster’s signature into your Active Filters.'
            ]
        },
        {
            id: 'example',
            title: 'A Good First Workflow (Example)',
            target: 'clusterExplorer',
            bullets: [
                'Start broad (even with zero filters), run Explorer, sort by best top4, and pick a cluster that looks strong.',
                'Replace → inspect the graph for supportive traits/items (Helpful mode is great here).',
                'Open Items on your carry unit to lock in a build, then iterate by adding/removing one filter at a time.'
            ],
            hint: 'If Explorer returns nothing, lower “min” or remove filters to increase sample size.'
        },
        {
            id: 'done',
            title: 'You’re ready',
            target: null,
            bullets: [
                'When in doubt: fewer filters → more games → more reliable signals.',
                'Use Helpful/Harmful to quickly spot what changes placement the most.',
                'Re-open this walkthrough anytime from the header.'
            ]
        }
    ];

    let stepIndex = 0;
    let spotlight = null;
    let missingTarget = false;
    let cardEl;
    let cardStyle = '';
    let rafId = null;
    let lastOpen = false;

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
        cardStyle = '';
    }

    function close(markSeen = true) {
        dispatch('close', { markSeen });
    }

    function finish() {
        close(true);
    }

    async function goToStep(nextIndex) {
        stepIndex = clamp(nextIndex, 0, steps.length - 1);
        await tick();

        const step = steps[stepIndex];
        const target = step?.target;
        if (target) {
            const el = document.querySelector(`[data-walkthrough="${target}"]`);
            if (el?.scrollIntoView) {
                el.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
                await new Promise(requestAnimationFrame);
            }
        }

        await tick();
        updatePositions();

        if (step?.id === 'search') {
            try {
                const input = document.querySelector('[data-walkthrough="search"] input');
                input?.focus?.();
            } catch {
                // ignore
            }
        }
    }

    function next() {
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

    function updatePositions() {
        if (!open) return;

        const step = steps[stepIndex];
        missingTarget = false;

        if (!step?.target) {
            spotlight = null;
            cardStyle = centeredCardStyle();
            return;
        }

        const el = document.querySelector(`[data-walkthrough="${step.target}"]`);
        if (!el) {
            spotlight = null;
            missingTarget = true;
            cardStyle = centeredCardStyle();
            return;
        }

        const rect = el.getBoundingClientRect();
        const pad = 10;
        spotlight = {
            left: Math.round(rect.left - pad),
            top: Math.round(rect.top - pad),
            width: Math.round(rect.width + pad * 2),
            height: Math.round(rect.height + pad * 2)
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
        const cardHeight = measured?.height ?? 260;

        const fitsRight = targetRect.right + margin + cardWidth <= vw - margin;
        const fitsLeft = targetRect.left - margin - cardWidth >= margin;
        const fitsBottom = targetRect.bottom + margin + cardHeight <= vh - margin;
        const fitsTop = targetRect.top - margin - cardHeight >= margin;

        if (fitsRight) {
            const left = Math.round(targetRect.right + margin);
            const top = Math.round(clamp(targetRect.top, margin, vh - margin - cardHeight));
            return `left:${left}px; top:${top}px; width:${cardWidth}px;`;
        }

        if (fitsLeft) {
            const left = Math.round(targetRect.left - margin - cardWidth);
            const top = Math.round(clamp(targetRect.top, margin, vh - margin - cardHeight));
            return `left:${left}px; top:${top}px; width:${cardWidth}px;`;
        }

        if (fitsBottom) {
            const left = Math.round(clamp(targetRect.left, margin, vw - margin - cardWidth));
            const top = Math.round(targetRect.bottom + margin);
            return `left:${left}px; top:${top}px; width:${cardWidth}px;`;
        }

        if (fitsTop) {
            const left = Math.round(clamp(targetRect.left, margin, vw - margin - cardWidth));
            const top = Math.round(targetRect.top - margin - cardHeight);
            return `left:${left}px; top:${top}px; width:${cardWidth}px;`;
        }

        return centeredCardStyle();
    }

    function handleKeydown(event) {
        if (!open) return;
        if (event.key === 'Escape') {
            event.preventDefault();
            close(true);
            return;
        }
        if (event.key === 'ArrowRight' || event.key === 'Enter') {
            event.preventDefault();
            next();
            return;
        }
        if (event.key === 'ArrowLeft') {
            event.preventDefault();
            back();
        }
    }

    function markSeenAndReset() {
        try {
            localStorage.setItem(STORAGE_KEY, '1');
        } catch {
            // ignore
        }
        clearTokens();
        goToStep(1);
    }
</script>

<svelte:window on:resize={scheduleUpdate} on:scroll={scheduleUpdate} on:keydown={handleKeydown} />

{#if open}
    <div class="walkthrough" role="dialog" aria-modal="true" aria-label="Walkthrough">
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
                <button class="icon-btn" on:click={() => close(true)} aria-label="Close walkthrough">×</button>
            </div>

            <div class="title">{steps[stepIndex].title}</div>

            <div class="content">
                {#if missingTarget}
                    <div class="callout">
                        This part of the UI isn’t visible right now. Resize your window (or open the sidebar) and continue.
                    </div>
                {/if}

                {#if steps[stepIndex].bullets?.length}
                    <ul class="bullets">
                        {#each steps[stepIndex].bullets as b}
                            <li>{b}</li>
                        {/each}
                    </ul>
                {/if}

                {#if steps[stepIndex].hint}
                    <div class="hint">{steps[stepIndex].hint}</div>
                {/if}

                {#if steps[stepIndex].id === 'welcome'}
                    <button class="secondary" on:click={markSeenAndReset}>Clear filters & start</button>
                {/if}
            </div>

            <div class="nav">
                <button class="secondary" on:click={back} disabled={stepIndex === 0}>Back</button>
                <button class="primary" on:click={next}>
                    {stepIndex === steps.length - 1 ? 'Finish' : 'Next'}
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
        transition: left 0.18s ease, top 0.18s ease, width 0.18s ease, height 0.18s ease;
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
        transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
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

    .bullets {
        margin: 0;
        padding-left: 18px;
        color: var(--text-secondary);
        font-size: 12px;
        line-height: 1.5;
        display: flex;
        flex-direction: column;
        gap: 6px;
    }

    .hint {
        font-size: 11px;
        color: var(--text-tertiary);
        line-height: 1.4;
        border-left: 2px solid rgba(0, 112, 243, 0.6);
        padding-left: 10px;
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
        transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
    }

    button.primary {
        background: var(--accent);
        border-color: transparent;
        color: #fff;
    }

    button.primary:hover {
        background: var(--accent-hover);
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
