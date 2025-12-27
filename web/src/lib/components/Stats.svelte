<script>
    import { stats } from '../stores/state.js';
    import AvgPlacement from './AvgPlacement.svelte';
</script>

<div class="stats">
    <div class="stat-inline">
        <span class="stat-label">Games</span>
        <span class="stat-value">{$stats.games.toLocaleString()}</span>
    </div>
    <div class="stat-inline">
        <span class="stat-label">Avg Place</span>
        <span class="stat-value">
            <AvgPlacement value={$stats.avgPlacement} showDelta={$stats.games > 0} />
        </span>
    </div>
</div>

<style>
    .stats {
        display: contents;
    }

    .stat-inline {
        display: flex;
        align-items: center;
        gap: 6px;
        padding: 0 12px;
        border-left: 1px solid var(--border);
        white-space: nowrap;
    }

    .stat-label {
        font-size: 10px;
        color: var(--text-tertiary);
        text-transform: uppercase;
        letter-spacing: 0.04em;
        font-weight: 500;
    }

    .stat-value {
        font-size: 14px;
        font-weight: 700;
        color: var(--text-primary);
    }

    @media (max-width: 768px) {
        .stat-inline {
            border-left: none;
            padding: 0;
            order: 3;
        }
    }
</style>
