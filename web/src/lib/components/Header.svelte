<script>
    import { createEventDispatcher } from 'svelte';

    const dispatch = createEventDispatcher();

    function openWalkthrough() {
        dispatch('openWalkthrough');
    }
</script>

<header>
    <div class="logo">
        <div class="logo-text">smeecher</div>
    </div>
    <button class="help" on:click={openWalkthrough} aria-label="Open walkthrough">
        Walkthrough
    </button>
    <a href="https://www.pramit.gg" target="_blank" rel="noopener" class="by-line">
        by JoyFired
    </a>
</header>

<style>
    header {
        display: flex;
        align-items: center;
        justify-content: center;
        margin-bottom: 24px;
        padding: 8px 0 16px;
        flex-shrink: 0;
        position: relative;
    }

    .logo {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 12px;
    }

    .logo-text {
        font-size: 42px;
        font-weight: 800;
        letter-spacing: -0.05em;
        color: var(--text-primary);
        text-transform: lowercase;
        line-height: 1;
    }

    .by-line {
        position: absolute;
        left: calc(50% + 110px);
        font-size: 12px;
        font-style: italic;
        color: var(--text-tertiary);
        text-decoration: none;
        opacity: 0.5;
        transition: opacity 0.2s ease, color 0.2s ease;
        letter-spacing: 0.02em;
        white-space: nowrap;
    }

    .by-line:hover {
        opacity: 1;
        color: var(--text-secondary);
    }

    .help {
        position: absolute;
        right: 0;
        top: 50%;
        transform: translateY(-50%);
        padding: 6px 10px;
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        background: var(--bg-tertiary);
        border: 1px solid var(--border);
        border-radius: 8px;
        color: var(--text-secondary);
        cursor: pointer;
        transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
        font-family: inherit;
    }

    .help:hover {
        background: var(--bg-secondary);
        border-color: var(--border-hover);
        color: var(--text-primary);
    }

    @media (max-width: 768px) {
        header {
            justify-content: center;
            padding: 4px 0 12px;
            margin-bottom: 16px;
        }

        .logo-text {
            font-size: 28px;
        }

        .by-line {
            left: calc(50% + 70px);
            font-size: 11px;
        }

        .help {
            padding: 6px 8px;
            font-size: 10px;
            border-radius: 7px;
        }
    }
</style>
