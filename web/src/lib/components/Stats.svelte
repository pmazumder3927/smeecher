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
            <AvgPlacement value={$stats.avgPlacement} />
        </span>
    </div>
</div>

<style>
    .stats {
        display: flex;
        align-items: center;
        gap: 12px;
    }

    .stat-inline {
        display: flex;
        align-items: center;
        gap: 5px;
        white-space: nowrap;
    }

    .stat-label {
        font-size: 9px;
        color: var(--text-tertiary);
        text-transform: uppercase;
        letter-spacing: 0.04em;
        font-weight: 600;
    }

    .stat-value {
        font-size: 12px;
        font-weight: 700;
        color: var(--text-primary);
    }

    @media (max-width: 768px) {
        .stats {
            gap: 10px;
        }
    }
</style>
