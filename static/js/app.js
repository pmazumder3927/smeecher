/**
 * Smeecher - TFT Analysis App
 * Main application JavaScript
 */

/* ==========================================================================
   Configuration & Constants
   ========================================================================== */

const CDRAGON_BASE = 'https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default';

/* ==========================================================================
   Application State
   ========================================================================== */

const state = {
    selectedTokens: [],
    graphData: null,
    activeTypes: new Set(['unit', 'item', 'trait']),
    lastSelectedToken: null,

    // Zoom/pan state
    currentZoomTransform: d3.zoomIdentity,
    panVelocity: { x: 0, y: 0 },
    lastTransform: null,

    // Touch/interaction state
    tooltipFromTouch: false,
    isDragging: false,
    driftAnimationId: null,

    // Graph references
    currentSimulation: null,
    currentSvg: null,
    currentNodes: null,
    currentZoom: null,
    graphWidth: 0,
    graphHeight: 0,

    // Position persistence
    nodePositions: new Map(),
    failedIcons: new Set()
};

/* ==========================================================================
   Asset Lookup Maps (populated from CDragon)
   ========================================================================== */

const assetMaps = {
    itemIconMap: new Map(),      // display name OR api name -> icon URL
    itemDisplayMap: new Map(),   // api name -> display name
    traitIconMap: new Map(),     // display name OR api name -> icon URL
    traitDisplayMap: new Map(),  // api name -> display name
    displayNameIndex: new Map()  // lowercase display name -> { apiName, type }
};

/* ==========================================================================
   DOM Elements
   ========================================================================== */

const elements = {
    searchInput: null,
    searchResults: null,
    chipsContainer: null,
    tooltip: null,
    topKInput: null,
    statGames: null,
    statPlacement: null
};

function initElements() {
    elements.searchInput = document.getElementById('search');
    elements.searchResults = document.getElementById('searchResults');
    elements.chipsContainer = document.getElementById('chips');
    elements.tooltip = document.getElementById('tooltip');
    elements.topKInput = document.getElementById('topKInput');
    elements.statGames = document.getElementById('statGames');
    elements.statPlacement = document.getElementById('statPlacement');
}

/* ==========================================================================
   Utility Functions
   ========================================================================== */

/**
 * Color code placement: 1 = best (green), 4.5 = yellow, 8 = worst (red)
 */
function getPlacementColor(placement) {
    const min = 1, max = 8, mid = 4.5;
    const p = Math.max(min, Math.min(max, placement));

    if (p <= mid) {
        const t = (p - min) / (mid - min);
        const r = Math.round(34 + t * 221);
        const g = Math.round(197 - t * 17);
        const b = Math.round(94 - t * 54);
        return `rgb(${r}, ${g}, ${b})`;
    } else {
        const t = (p - mid) / (max - mid);
        const r = 255;
        const g = Math.round(180 - t * 130);
        const b = Math.round(40 + t * 20);
        return `rgb(${r}, ${g}, ${b})`;
    }
}

/* ==========================================================================
   Asset Loading (Community Dragon)
   ========================================================================== */

async function loadCDragonData() {
    try {
        const [itemsRes, traitsRes] = await Promise.all([
            fetch(`${CDRAGON_BASE}/v1/tftitems.json`),
            fetch(`${CDRAGON_BASE}/v1/tfttraits.json`)
        ]);

        const [items, traits] = await Promise.all([
            itemsRes.json(),
            traitsRes.json()
        ]);

        // Build item lookups
        items.forEach(item => {
            const displayName = item.name?.replace(/<[^>]*>/g, '').trim();
            const nameId = item.nameId || '';
            const iconPath = item.squareIconPath || item.iconPath;

            if (displayName && iconPath) {
                const url = `${CDRAGON_BASE}${iconPath.toLowerCase().replace('/lol-game-data/assets', '')}`;

                assetMaps.itemIconMap.set(displayName.toLowerCase(), url);

                if (nameId) {
                    assetMaps.itemIconMap.set(nameId.toLowerCase(), url);
                    assetMaps.itemDisplayMap.set(nameId.toLowerCase(), displayName);

                    const cleaned = nameId
                        .replace(/^TFT\d*_Item_/i, '')
                        .replace(/^TFT_Item_/i, '');
                    if (cleaned !== nameId) {
                        assetMaps.itemIconMap.set(cleaned.toLowerCase(), url);
                        assetMaps.itemDisplayMap.set(cleaned.toLowerCase(), displayName);
                    }

                    assetMaps.displayNameIndex.set(displayName.toLowerCase(), {
                        apiName: cleaned !== nameId ? cleaned : nameId.replace(/^TFT_Item_/i, ''),
                        type: 'item',
                        displayName: displayName
                    });
                }
            }
        });

        // Build trait lookups
        traits.forEach(trait => {
            const displayName = trait.display_name;
            const traitId = trait.trait_id || '';
            const iconPath = trait.icon_path;

            if (displayName && iconPath) {
                const url = `${CDRAGON_BASE}${iconPath.toLowerCase().replace('/lol-game-data/assets', '')}`;

                assetMaps.traitIconMap.set(displayName.toLowerCase(), url);

                if (traitId) {
                    assetMaps.traitIconMap.set(traitId.toLowerCase(), url);
                    assetMaps.traitDisplayMap.set(traitId.toLowerCase(), displayName);

                    const cleaned = traitId
                        .replace(/^TFT\d*_/i, '')
                        .replace(/^Set\d*_/i, '');
                    if (cleaned !== traitId) {
                        assetMaps.traitIconMap.set(cleaned.toLowerCase(), url);
                        assetMaps.traitDisplayMap.set(cleaned.toLowerCase(), displayName);
                    }

                    if (traitId.startsWith('TFT16_')) {
                        assetMaps.displayNameIndex.set(displayName.toLowerCase(), {
                            apiName: cleaned,
                            type: 'trait',
                            displayName: displayName
                        });
                    }
                }
            }
        });

        console.log(`Loaded ${assetMaps.itemIconMap.size} item mappings, ${assetMaps.traitIconMap.size} trait mappings, ${assetMaps.displayNameIndex.size} searchable display names from CDragon`);
    } catch (error) {
        console.warn('Failed to load CDragon data, icons may not display:', error);
    }
}

/* ==========================================================================
   Icon URL Builders
   ========================================================================== */

function getUnitIconUrl(name) {
    const lower = name.toLowerCase();
    return `${CDRAGON_BASE}/assets/characters/tft16_${lower}/hud/tft16_${lower}_square.tft_set16.png`;
}

function getItemIconUrl(name) {
    const lower = name.toLowerCase();
    if (assetMaps.itemIconMap.has(lower)) {
        return assetMaps.itemIconMap.get(lower);
    }
    return `${CDRAGON_BASE}/assets/maps/tft/icons/items/hexcore/tft_item_${lower.replace(/['\s]/g, '')}.tft_set13.png`;
}

function getTraitIconUrl(name) {
    const lower = name.toLowerCase();
    if (assetMaps.traitIconMap.has(lower)) {
        return assetMaps.traitIconMap.get(lower);
    }
    return `${CDRAGON_BASE}/assets/ux/traiticons/trait_icon_16_${lower.replace(/\s/g, '')}.tft_set16.png`;
}

function getIconUrl(type, name) {
    switch (type) {
        case 'unit': return getUnitIconUrl(name);
        case 'item': return getItemIconUrl(name);
        case 'trait': return getTraitIconUrl(name);
        default: return null;
    }
}

function getDisplayName(type, apiName) {
    const lower = apiName.toLowerCase();
    if (type === 'item' && assetMaps.itemDisplayMap.has(lower)) {
        return assetMaps.itemDisplayMap.get(lower);
    }
    if (type === 'trait' && assetMaps.traitDisplayMap.has(lower)) {
        return assetMaps.traitDisplayMap.get(lower);
    }
    return apiName;
}

/* ==========================================================================
   Search Functionality
   ========================================================================== */

let searchTimeout;

function initSearch() {
    elements.searchInput.addEventListener('input', handleSearchInput);
    elements.searchInput.addEventListener('focus', handleSearchFocus);
    document.addEventListener('click', handleDocumentClick);
}

function handleSearchInput(e) {
    clearTimeout(searchTimeout);
    const query = e.target.value.trim();

    if (query.length < 2) {
        elements.searchResults.classList.remove('visible');
        return;
    }

    searchTimeout = setTimeout(async () => {
        try {
            const response = await fetch(`/search?q=${encodeURIComponent(query)}`);
            const backendResults = await response.json();

            const queryLower = query.toLowerCase();
            const localMatches = [];
            const seenTokens = new Set(backendResults.map(r => r.token));

            for (const [displayNameLower, info] of assetMaps.displayNameIndex) {
                if (displayNameLower.includes(queryLower)) {
                    const prefix = info.type === 'item' ? 'I:' : 'T:';
                    const token = prefix + info.apiName;

                    if (!seenTokens.has(token)) {
                        localMatches.push({
                            token: token,
                            label: info.apiName,
                            type: info.type,
                            count: 0
                        });
                        seenTokens.add(token);
                    }
                }
            }

            const allResults = [...backendResults, ...localMatches];
            renderSearchResults(allResults);
        } catch (error) {
            console.error('Search error:', error);
        }
    }, 150);
}

function handleSearchFocus() {
    if (elements.searchResults.children.length > 0) {
        elements.searchResults.classList.add('visible');
    }
}

function handleDocumentClick(e) {
    if (!e.target.closest('.search-container')) {
        elements.searchResults.classList.remove('visible');
    }
}

function renderSearchResults(results) {
    if (results.length === 0) {
        elements.searchResults.innerHTML = '<div class="search-result"><span class="label">No results found</span></div>';
        elements.searchResults.classList.add('visible');
        return;
    }

    elements.searchResults.innerHTML = results.map(r => {
        const typeClass = r.type === 'unit' ? 'unit' :
                          r.type === 'item' ? 'item' :
                          r.type === 'trait' ? 'trait' : 'item';
        const displayLabel = getDisplayName(r.type, r.label);
        const countText = r.count > 0 ? `${r.count.toLocaleString()} games` : '';
        return `
            <div class="search-result" data-token="${r.token}">
                <span class="label">${displayLabel}</span>
                <span class="meta">
                    <span class="type-badge ${typeClass}">${r.type}</span>
                    ${countText ? `<span>${countText}</span>` : ''}
                </span>
            </div>
        `;
    }).join('');

    elements.searchResults.classList.add('visible');

    elements.searchResults.querySelectorAll('.search-result').forEach(el => {
        el.addEventListener('click', () => {
            addToken(el.dataset.token);
            elements.searchInput.value = '';
            elements.searchResults.classList.remove('visible');
        });
    });
}

/* ==========================================================================
   Token Management
   ========================================================================== */

function addToken(token) {
    if (!state.selectedTokens.includes(token)) {
        state.selectedTokens.push(token);
        state.lastSelectedToken = token;
        renderChips();
        fetchGraph().then(() => {
            if (state.currentSvg && state.currentZoom && state.graphWidth && state.graphHeight) {
                if (!state.driftAnimationId) {
                    startAutoFraming(state.currentSvg, state.currentZoom, state.graphWidth, state.graphHeight);
                }
            }
        });
    }
}

function removeToken(token) {
    state.selectedTokens = state.selectedTokens.filter(t => t !== token);
    renderChips();
    fetchGraph();
}

// Expose to global scope for onclick handlers
window.removeToken = removeToken;

function renderChips() {
    if (state.selectedTokens.length === 0) {
        elements.chipsContainer.innerHTML = '<div style="color: var(--text-tertiary); font-size: 12px;">No filters applied. Search and select units, items, or traits to start.</div>';
        return;
    }

    elements.chipsContainer.innerHTML = state.selectedTokens.map(token => {
        const type = token.startsWith('U:') ? 'unit' :
                     token.startsWith('I:') ? 'item' :
                     token.startsWith('T:') ? 'trait' : 'equipped';
        let label;
        if (token.startsWith('E:')) {
            const [unit, item] = token.slice(2).split('|');
            label = `${getDisplayName('unit', unit)} → ${getDisplayName('item', item)}`;
        } else {
            label = getDisplayName(type, token.slice(2));
        }
        return `
            <div class="chip ${type}">
                <span>${label}</span>
                <button onclick="removeToken('${token}')" aria-label="Remove filter">×</button>
            </div>
        `;
    }).join('');
}

/* ==========================================================================
   Type Toggle
   ========================================================================== */

function toggleType(type) {
    if (state.activeTypes.has(type)) {
        state.activeTypes.delete(type);
        const legendEl = document.getElementById(`legend-${type}`);
        if (legendEl) legendEl.classList.add('disabled');
        const btnEl = document.getElementById(`btn-toggle-${type}`);
        if (btnEl) btnEl.classList.remove('active');
    } else {
        state.activeTypes.add(type);
        const legendEl = document.getElementById(`legend-${type}`);
        if (legendEl) legendEl.classList.remove('disabled');
        const btnEl = document.getElementById(`btn-toggle-${type}`);
        if (btnEl) btnEl.classList.add('active');
    }
    if (state.graphData) renderGraph();
}

// Expose to global scope for onclick handlers
window.toggleType = toggleType;

/* ==========================================================================
   Graph Data Fetching
   ========================================================================== */

async function fetchGraph() {
    const tokensParam = state.selectedTokens.join(',');
    const topK = parseInt(elements.topKInput.value) || 0;
    console.log('Fetching graph with tokens:', tokensParam, 'top_k:', topK);

    try {
        const response = await fetch(`/graph?tokens=${encodeURIComponent(tokensParam)}&top_k=${topK}&t=${Date.now()}`);
        state.graphData = await response.json();

        elements.statGames.textContent = state.graphData.base.n.toLocaleString();
        const avgPlace = state.graphData.base.avg_placement;
        elements.statPlacement.textContent = avgPlace.toFixed(2);
        elements.statPlacement.style.color = getPlacementColor(avgPlace);

        renderGraph();
        return true;
    } catch (error) {
        console.error('Graph fetch error:', error);
        return false;
    }
}

/* ==========================================================================
   Auto-Framing Camera
   ========================================================================== */

function startAutoFraming(svg, zoom, width, height) {
    if (state.driftAnimationId) {
        cancelAnimationFrame(state.driftAnimationId);
    }

    const driftSpeed = 0.02;
    const paddingFactor = 0.85;

    function drift() {
        state.driftAnimationId = requestAnimationFrame(drift);

        if (state.isDragging || !state.currentNodes || state.currentNodes.length === 0) {
            return;
        }

        let minX = Infinity, maxX = -Infinity;
        let minY = Infinity, maxY = -Infinity;

        state.currentNodes.forEach(n => {
            if (state.activeTypes.has(n.type)) {
                if (n.x < minX) minX = n.x;
                if (n.x > maxX) maxX = n.x;
                if (n.y < minY) minY = n.y;
                if (n.y > maxY) maxY = n.y;
            }
        });

        if (minX === Infinity) return;

        const margin = 60;
        minX -= margin;
        maxX += margin;
        minY -= margin;
        maxY += margin;

        const contentWidth = maxX - minX;
        const contentHeight = maxY - minY;
        const centerX = (minX + maxX) / 2;
        const centerY = (minY + maxY) / 2;

        const currentK = state.currentZoomTransform.k;
        const currentX = state.currentZoomTransform.x;
        const currentY = state.currentZoomTransform.y;

        const viewX = -currentX / currentK;
        const viewY = -currentY / currentK;
        const viewW = width / currentK;
        const viewH = height / currentK;

        const overlapX = Math.max(0, Math.min(maxX, viewX + viewW) - Math.max(minX, viewX));
        const overlapY = Math.max(0, Math.min(maxY, viewY + viewH) - Math.max(minY, viewY));
        const isLookingAtEmptySpace = (overlapX <= 0 || overlapY <= 0);

        const safeWidth = Math.max(contentWidth, 100);
        const safeHeight = Math.max(contentHeight, 100);

        const scaleX = (width * paddingFactor) / safeWidth;
        const scaleY = (height * paddingFactor) / safeHeight;
        let targetK = Math.min(scaleX, scaleY);
        targetK = Math.max(0.3, Math.min(2.5, targetK));

        const isOverviewMode = currentK <= (targetK * 1.1);

        if (!isLookingAtEmptySpace && !isOverviewMode) {
            return;
        }

        const targetX = (width / 2) - (centerX * targetK);
        const targetY = (height / 2) - (centerY * targetK);

        const nextK = currentK + (targetK - currentK) * driftSpeed;
        const nextX = currentX + (targetX - currentX) * driftSpeed;
        const nextY = currentY + (targetY - currentY) * driftSpeed;

        if (Math.abs(nextK - currentK) > 0.0001 ||
            Math.abs(nextX - currentX) > 0.1 ||
            Math.abs(nextY - currentY) > 0.1) {

            const newTransform = d3.zoomIdentity.translate(nextX, nextY).scale(nextK);
            svg.call(zoom.transform, newTransform);
        }
    }

    drift();
}

/* ==========================================================================
   Graph Rendering
   ========================================================================== */

function renderGraph() {
    const svg = d3.select('#graph');
    svg.selectAll('*').remove();

    const width = svg.node().clientWidth;
    const height = svg.node().clientHeight;

    svg.attr('viewBox', [0, 0, width, height]);

    const container = svg.append('g').attr('class', 'graph-container-g');
    container.attr('transform', state.currentZoomTransform);

    // Grid pattern
    const patternDefs = container.append('defs');
    const gridSize = 40;
    const pattern = patternDefs.append('pattern')
        .attr('id', 'grid')
        .attr('width', gridSize)
        .attr('height', gridSize)
        .attr('patternUnits', 'userSpaceOnUse');

    pattern.append('rect')
        .attr('width', gridSize)
        .attr('height', gridSize)
        .attr('fill', '#0a0a0a');

    pattern.append('line')
        .attr('x1', gridSize).attr('y1', 0)
        .attr('x2', gridSize).attr('y2', gridSize)
        .attr('stroke', '#1a1a1a').attr('stroke-width', 1);

    pattern.append('line')
        .attr('x1', 0).attr('y1', gridSize)
        .attr('x2', gridSize).attr('y2', gridSize)
        .attr('stroke', '#1a1a1a').attr('stroke-width', 1);

    container.append('rect')
        .attr('class', 'grid-bg')
        .attr('x', -5000).attr('y', -5000)
        .attr('width', 10000).attr('height', 10000)
        .attr('fill', 'url(#grid)')
        .on('touchstart', function() {
            if (state.tooltipFromTouch) {
                state.tooltipFromTouch = false;
                hideTooltip();
                container.selectAll('.link').classed('active', false);
            }
        });

    state.graphWidth = width;
    state.graphHeight = height;

    // Zoom behavior
    const zoom = d3.zoom()
        .scaleExtent([0.3, 3])
        .on('start', (event) => {
            if (event.sourceEvent) {
                state.isDragging = true;
                state.lastTransform = { x: state.currentZoomTransform.x, y: state.currentZoomTransform.y };
            }
        })
        .on('zoom', (event) => {
            if (event.sourceEvent && state.lastTransform) {
                const dx = event.transform.x - state.lastTransform.x;
                const dy = event.transform.y - state.lastTransform.y;
                state.panVelocity.x = state.panVelocity.x * 0.6 + dx * 0.4;
                state.panVelocity.y = state.panVelocity.y * 0.6 + dy * 0.4;
            }
            if (event.sourceEvent) {
                state.lastTransform = { x: event.transform.x, y: event.transform.y };
            }

            state.currentZoomTransform = event.transform;
            container.attr('transform', event.transform);

            if (!state.tooltipFromTouch) {
                hideTooltip();
            }

            if (state.currentSimulation && (Math.abs(state.panVelocity.x) > 0.5 || Math.abs(state.panVelocity.y) > 0.5)) {
                state.currentSimulation.alpha(0.15).restart();
            }
        })
        .on('end', () => {
            state.isDragging = false;
            state.lastTransform = null;
        });

    svg.call(zoom);
    state.currentZoom = zoom;
    state.currentSvg = svg;
    svg.call(zoom.transform, state.currentZoomTransform);
    startAutoFraming(svg, zoom, width, height);

    if (!state.graphData || state.graphData.nodes.length === 0) {
        container.append('foreignObject')
            .attr('x', width / 2 - 200)
            .attr('y', height / 2 - 60)
            .attr('width', 400)
            .attr('height', 120)
            .append('xhtml:div')
            .attr('class', 'empty-state')
            .html(`
                <div class="empty-state-title">No data to display</div>
                <div class="empty-state-text">Search and select units, items, or traits to visualize relationships</div>
            `);
        return;
    }

    // Arrow markers
    const defs = container.append('defs');

    ['equipped', 'cooccur', 'positive', 'negative'].forEach(type => {
        const marker = defs.append('marker')
            .attr('id', `arrow-${type}`)
            .attr('viewBox', '0 -5 10 10')
            .attr('refX', 28).attr('refY', 0)
            .attr('markerWidth', 8).attr('markerHeight', 8)
            .attr('orient', 'auto');

        const color = type === 'equipped' ? '#f5a623' :
                     type === 'cooccur' ? '#666666' :
                     type === 'positive' ? '#00d9a5' : '#ff4444';

        marker.append('path')
            .attr('fill', color)
            .attr('d', 'M0,-5L10,0L0,5');
    });

    // Filter nodes by active types
    const filteredNodes = state.graphData.nodes.filter(n => state.activeTypes.has(n.type));
    const filteredNodeIds = new Set(filteredNodes.map(n => n.id));

    const nodes = filteredNodes.map(n => {
        const stored = state.nodePositions.get(n.id);
        return {
            ...n,
            radius: n.isCenter ? 28 : 20,
            x: stored ? stored.x : width / 2 + (Math.random() - 0.5) * 100,
            y: stored ? stored.y : height / 2 + (Math.random() - 0.5) * 100
        };
    });

    const nodeById = new Map(nodes.map(n => [n.id, n]));

    const links = state.graphData.edges
        .filter(e => filteredNodeIds.has(e.from) && filteredNodeIds.has(e.to))
        .map(e => ({
            ...e,
            source: nodeById.get(e.from),
            target: nodeById.get(e.to)
        })).filter(l => l.source && l.target);

    const centerNodes = nodes.filter(n => n.isCenter);
    const hasCenterTeam = centerNodes.length > 1;

    // Force simulation
    const simulation = d3.forceSimulation(nodes)
        .force('link', d3.forceLink(links).id(d => d.id).distance(140))
        .force('charge', d3.forceManyBody().strength(-300))
        .force('center', d3.forceCenter(width / 2, height / 2))
        .force('x', d3.forceX(width / 2).strength(0.04))
        .force('y', d3.forceY(height / 2).strength(0.04))
        .force('collision', d3.forceCollide().radius(d => d.radius + 8))
        .alphaDecay(0.02)
        .velocityDecay(0.3);

    state.currentSimulation = simulation;
    state.currentNodes = nodes;

    // Pan inertia force
    simulation.force('panInertia', () => {
        const strength = 2.5;
        const damping = 0.92;

        nodes.forEach(n => {
            n.vx -= state.panVelocity.x * strength;
            n.vy -= state.panVelocity.y * strength;
        });

        state.panVelocity.x *= damping;
        state.panVelocity.y *= damping;

        if (Math.abs(state.panVelocity.x) < 0.01) state.panVelocity.x = 0;
        if (Math.abs(state.panVelocity.y) < 0.01) state.panVelocity.y = 0;
    });

    // Center clustering force
    if (hasCenterTeam) {
        const minDistance = 120;

        simulation.force('centerCluster', () => {
            let cx = 0, cy = 0;
            centerNodes.forEach(n => { cx += n.x || 0; cy += n.y || 0; });
            cx /= centerNodes.length;
            cy /= centerNodes.length;

            const pullStrength = 0.03;
            centerNodes.forEach(n => {
                n.vx += (cx - n.x) * pullStrength;
                n.vy += (cy - n.y) * pullStrength;
            });

            const pushStrength = 0.5;
            for (let i = 0; i < centerNodes.length; i++) {
                for (let j = i + 1; j < centerNodes.length; j++) {
                    const a = centerNodes[i], b = centerNodes[j];
                    const dx = b.x - a.x;
                    const dy = b.y - a.y;
                    const dist = Math.sqrt(dx * dx + dy * dy) || 1;

                    if (dist < minDistance) {
                        const force = (minDistance - dist) * pushStrength / dist;
                        a.vx -= dx * force;
                        a.vy -= dy * force;
                        b.vx += dx * force;
                        b.vy += dy * force;
                    }
                }
            }
        });
    }

    // Team hull
    let teamHull = null;
    if (hasCenterTeam) {
        teamHull = container.append('path')
            .attr('class', 'team-hull')
            .attr('fill', 'rgba(0, 112, 243, 0.08)')
            .attr('stroke', 'rgba(0, 112, 243, 0.25)')
            .attr('stroke-width', 2)
            .attr('stroke-dasharray', '8,4');
    }

    // Links
    const linkGroup = container.append('g');

    const link = linkGroup.selectAll('.link-visible')
        .data(links)
        .join('line')
        .attr('class', d => {
            const classes = [`link ${d.type}`];
            if (d.delta !== undefined) {
                classes.push(d.delta <= 0 ? 'positive' : 'negative');
            }
            return classes.join(' ');
        })
        .attr('stroke-width', d => {
            if (d.delta !== undefined) {
                return Math.max(2, Math.min(6, Math.abs(d.delta) * 5));
            }
            return d.type === 'equipped' ? 3 : 1.5;
        })
        .attr('marker-end', d => {
            if (d.delta !== undefined) {
                return `url(#arrow-${d.delta <= 0 ? 'positive' : 'negative'})`;
            }
            return `url(#arrow-${d.type})`;
        });

    const linkOverlay = linkGroup.selectAll('.link-overlay')
        .data(links)
        .join('line')
        .attr('class', 'link-overlay')
        .on('mouseover', (event, d) => {
            link.filter(ld => ld === d).classed('active', true);
            showEdgeTooltip(event, d);
        })
        .on('mouseout', (event, d) => {
            link.filter(ld => ld === d).classed('active', false);
            hideTooltip();
        })
        .on('touchstart', function(event, d) {
            event.preventDefault();
            event.stopPropagation();
            state.tooltipFromTouch = true;
            link.classed('active', false);
            link.filter(ld => ld === d).classed('active', true);
            showEdgeTooltip(event, d);
        }, { passive: false });

    // Clip paths
    const clipDefs = container.append('defs');
    nodes.forEach(n => {
        clipDefs.append('clipPath')
            .attr('id', `clip-${n.id.replace(/[^a-zA-Z0-9]/g, '_')}`)
            .append('circle')
            .attr('r', n.radius - 2);
    });

    // Nodes
    const node = container.append('g')
        .selectAll('g')
        .data(nodes)
        .join('g')
        .attr('class', d => `node ${d.type} ${d.isCenter ? 'center' : ''}`)
        .call(drag(simulation))
        .on('click', handleNodeClick)
        .on('mouseover', showNodeTooltip)
        .on('mouseout', hideTooltip)
        .on('touchstart', function(event, d) {
            event.preventDefault();
            event.stopPropagation();
            state.tooltipFromTouch = true;
            showNodeTooltip(event, d);
        }, { passive: false });

    node.append('circle')
        .attr('class', 'node-bg')
        .attr('r', d => d.radius);

    node.append('circle')
        .attr('class', 'node-ring')
        .attr('r', d => d.radius);

    // Node icons
    node.each(function(d) {
        const iconKey = `${d.type}:${d.label}`;
        if (state.failedIcons.has(iconKey)) {
            d3.select(this).append('circle')
                .attr('class', 'node-fallback')
                .attr('r', d.radius - 4);
        } else {
            const iconUrl = getIconUrl(d.type, d.label);
            if (iconUrl) {
                const img = d3.select(this).append('image')
                    .attr('class', 'node-icon')
                    .attr('x', -(d.radius - 2))
                    .attr('y', -(d.radius - 2))
                    .attr('width', (d.radius - 2) * 2)
                    .attr('height', (d.radius - 2) * 2)
                    .attr('clip-path', `url(#clip-${d.id.replace(/[^a-zA-Z0-9]/g, '_')})`)
                    .attr('href', iconUrl)
                    .attr('preserveAspectRatio', 'xMidYMid slice');

                img.on('error', function() {
                    state.failedIcons.add(iconKey);
                    d3.select(this).remove();
                    d3.select(this.parentNode).append('circle')
                        .attr('class', 'node-fallback')
                        .attr('r', d.radius - 4);
                });
            }
        }
    });

    node.append('text')
        .attr('dy', d => d.radius + 16)
        .text(d => getDisplayName(d.type, d.label));

    // Simulation tick
    simulation.on('tick', () => {
        nodes.forEach(n => {
            state.nodePositions.set(n.id, { x: n.x, y: n.y });
        });

        // Update team hull
        if (teamHull && centerNodes.length >= 2) {
            const padding = 45;
            const points = centerNodes.map(n => [n.x, n.y]);

            if (centerNodes.length === 2) {
                const [p1, p2] = points;
                const dx = p2[0] - p1[0];
                const dy = p2[1] - p1[1];
                const dist = Math.sqrt(dx * dx + dy * dy);
                const nx = -dy / dist * padding;
                const ny = dx / dist * padding;

                teamHull.attr('d', `
                    M ${p1[0] + nx} ${p1[1] + ny}
                    L ${p2[0] + nx} ${p2[1] + ny}
                    A ${padding} ${padding} 0 0 1 ${p2[0] - nx} ${p2[1] - ny}
                    L ${p1[0] - nx} ${p1[1] - ny}
                    A ${padding} ${padding} 0 0 1 ${p1[0] + nx} ${p1[1] + ny}
                    Z
                `);
            } else {
                const hull = d3.polygonHull(points);
                if (hull) {
                    const cx = d3.mean(hull, p => p[0]);
                    const cy = d3.mean(hull, p => p[1]);
                    const expanded = hull.map(p => {
                        const dx = p[0] - cx;
                        const dy = p[1] - cy;
                        const dist = Math.sqrt(dx * dx + dy * dy);
                        const scale = (dist + padding) / dist;
                        return [cx + dx * scale, cy + dy * scale];
                    });

                    const line = d3.line().curve(d3.curveCardinalClosed.tension(0.7));
                    teamHull.attr('d', line(expanded));
                }
            }
        }

        linkOverlay
            .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
            .attr('x2', d => d.target.x).attr('y2', d => d.target.y);

        link
            .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
            .attr('x2', d => d.target.x).attr('y2', d => d.target.y);

        node.attr('transform', d => `translate(${d.x},${d.y})`);
    });
}

/* ==========================================================================
   Drag Behavior
   ========================================================================== */

function drag(simulation) {
    return d3.drag()
        .on('start', (event, d) => {
            if (!event.active) simulation.alphaTarget(0.3).restart();
            d.fx = d.x;
            d.fy = d.y;
        })
        .on('drag', (event, d) => {
            d.fx = event.x;
            d.fy = event.y;
        })
        .on('end', (event, d) => {
            if (!event.active) simulation.alphaTarget(0);
            d.fx = null;
            d.fy = null;
        });
}

/* ==========================================================================
   Node/Edge Interaction
   ========================================================================== */

function handleNodeClick(event, d) {
    event.stopPropagation();

    const edge = state.graphData.edges.find(e =>
        (e.from === d.id || e.to === d.id) && !d.isCenter
    );

    if (edge) {
        addToken(edge.token);
    } else if (!d.isCenter) {
        addToken(d.id);
    }
}

function formatEdgeTitle(token) {
    if (token.startsWith('E:')) {
        const [unit, item] = token.slice(2).split('|');
        return `${getDisplayName('unit', unit)} → ${getDisplayName('item', item)}`;
    }
    return token.slice(2);
}

/* ==========================================================================
   Tooltips
   ========================================================================== */

function showEdgeTooltip(event, d) {
    const isImprovement = d.delta <= 0;
    const color = isImprovement ? 'var(--success)' : 'var(--error)';
    const deltaSign = d.delta > 0 ? '+' : '';

    elements.tooltip.innerHTML = `
        <div class="tooltip-title">${formatEdgeTitle(d.token)}</div>
        <div class="tooltip-row">
            <span class="label">Placement Impact</span>
            <span class="value" style="color: ${color}; font-weight: 800; font-size: 15px;">
                ${deltaSign}${d.delta.toFixed(2)}
            </span>
        </div>
        <div class="tooltip-row">
            <span class="label">With this combo</span>
            <span class="value">${d.avg_with.toFixed(2)}</span>
        </div>
        <div class="tooltip-row">
            <span class="label">Base average</span>
            <span class="value">${d.avg_base.toFixed(2)}</span>
        </div>
        <div class="tooltip-row">
            <span class="label">Sample size</span>
            <span class="value">${d.n_with.toLocaleString()} / ${d.n_base.toLocaleString()}</span>
        </div>
    `;
    elements.tooltip.style.display = 'block';
    positionTooltip(event);
}

function showNodeTooltip(event, d) {
    elements.tooltip.innerHTML = `
        <div class="tooltip-title">${getDisplayName(d.type, d.label)}</div>
        <div class="tooltip-row">
            <span class="label">Type</span>
            <span class="value">${d.type}</span>
        </div>
        <div class="tooltip-row">
            <span class="label">Action</span>
            <span class="value">${d.isCenter ? 'Center node' : 'Click to add filter'}</span>
        </div>
    `;
    elements.tooltip.style.display = 'block';
    positionTooltip(event);
}

function positionTooltip(event) {
    const tooltipRect = elements.tooltip.getBoundingClientRect();
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;

    let pageX, pageY;
    if (event.touches && event.touches.length > 0) {
        pageX = event.touches[0].pageX;
        pageY = event.touches[0].pageY;
    } else if (event.changedTouches && event.changedTouches.length > 0) {
        pageX = event.changedTouches[0].pageX;
        pageY = event.changedTouches[0].pageY;
    } else {
        pageX = event.pageX;
        pageY = event.pageY;
    }

    let left = pageX + 12;
    let top = pageY + 12;

    if (left + tooltipRect.width > viewportWidth) {
        left = pageX - tooltipRect.width - 12;
    }

    if (top + tooltipRect.height > viewportHeight) {
        top = pageY - tooltipRect.height - 12;
    }

    elements.tooltip.style.left = left + 'px';
    elements.tooltip.style.top = top + 'px';
}

function hideTooltip() {
    elements.tooltip.style.display = 'none';
}

/* ==========================================================================
   Event Listeners
   ========================================================================== */

function initEventListeners() {
    elements.topKInput.addEventListener('change', () => {
        if (state.graphData) fetchGraph();
    });

    let resizeTimeout;
    window.addEventListener('resize', () => {
        clearTimeout(resizeTimeout);
        resizeTimeout = setTimeout(() => {
            if (state.graphData) renderGraph();
        }, 250);
    });
}

/* ==========================================================================
   Initialization
   ========================================================================== */

function init() {
    initElements();
    initSearch();
    initEventListeners();
    renderChips();

    loadCDragonData().then(() => {
        fetchGraph();
    });
}

// Start the app when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}
