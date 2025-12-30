<script>
    import { onMount, onDestroy } from 'svelte';
    import * as d3 from 'd3';
    import {
        graphData,
        stats,
        activeTypes,
        addToken,
        highlightedTokens,
        showTooltip,
        hideTooltip,
        forceHideTooltip,
        tooltip
    } from '../stores/state.js';
    import AvgPlacement from './AvgPlacement.svelte';
    import { getIconUrl, getDisplayName, hasIconFailed, markIconFailed } from '../stores/assets.js';
    import { get } from 'svelte/store';
    import posthog from '../client/posthog';

    let container;
    let svg;
    let simulation = null;
    let currentZoomTransform = d3.zoomIdentity;
    let nodePositions = new Map();
    let driftAnimationId = null;
    let isDragging = false;
    let panVelocity = { x: 0, y: 0 };
    let lastTransform = null;
    let currentNodes = [];
    let nodeSelection = null;

    // Reactively render when data or filters change
    $: if (svg && $graphData) {
        renderGraph($graphData, $activeTypes);
    }

    // Update highlights without re-rendering
    $: if (nodeSelection) {
        applyHighlights($highlightedTokens);
    }

    onMount(() => {
        svg = d3.select(container);
        if ($graphData) {
            renderGraph($graphData, $activeTypes);
        }

        // Handle resize
        const resizeObserver = new ResizeObserver(() => {
            if ($graphData) {
                renderGraph($graphData, $activeTypes);
            }
        });
        resizeObserver.observe(container);

        return () => {
            resizeObserver.disconnect();
            if (driftAnimationId) {
                cancelAnimationFrame(driftAnimationId);
            }
            if (simulation) {
                simulation.stop();
            }
        };
    });

    onDestroy(() => {
        if (driftAnimationId) {
            cancelAnimationFrame(driftAnimationId);
        }
        if (simulation) {
            simulation.stop();
        }
    });

    function renderGraph(data, types) {
        if (!svg) return;

        svg.selectAll('*').remove();
        nodeSelection = null;

        const width = container.clientWidth;
        const height = container.clientHeight;

        svg.attr('viewBox', [0, 0, width, height]);

        const g = svg.append('g').attr('class', 'graph-container-g');
        g.attr('transform', currentZoomTransform);

        // Grid pattern
        createGridPattern(g, 40);

        // Background
        g.append('rect')
            .attr('class', 'grid-bg')
            .attr('x', -5000).attr('y', -5000)
            .attr('width', 10000).attr('height', 10000)
            .attr('fill', 'url(#grid)')
            .on('touchstart', () => {
                const t = get(tooltip);
                if (t.fromTouch) {
                    forceHideTooltip();
                    g.selectAll('.link').classed('active', false);
                }
            });

        // Zoom behavior
        const zoom = d3.zoom()
            .scaleExtent([0.3, 3])
            .on('start', (event) => {
                if (event.sourceEvent) {
                    isDragging = true;
                    lastTransform = { x: currentZoomTransform.x, y: currentZoomTransform.y };
                }
            })
            .on('zoom', (event) => {
                if (event.sourceEvent && lastTransform) {
                    panVelocity.x = panVelocity.x * 0.6 + (event.transform.x - lastTransform.x) * 0.4;
                    panVelocity.y = panVelocity.y * 0.6 + (event.transform.y - lastTransform.y) * 0.4;
                    lastTransform = { x: event.transform.x, y: event.transform.y };
                }
                currentZoomTransform = event.transform;
                g.attr('transform', event.transform);

                const t = get(tooltip);
                if (!t.fromTouch) {
                    hideTooltip();
                }

                if (simulation && (Math.abs(panVelocity.x) > 0.5 || Math.abs(panVelocity.y) > 0.5)) {
                    simulation.alpha(0.15).restart();
                }
            })
            .on('end', () => {
                isDragging = false;
                lastTransform = null;
            });

        svg.call(zoom);
        svg.call(zoom.transform, currentZoomTransform);
        startAutoFraming(svg, zoom, width, height, types);

        if (!data || data.nodes.length === 0) {
            renderEmptyState(g, width, height);
            return;
        }

        // Arrow markers
        createArrowMarkers(g);

        // Filter nodes - always keep center nodes (active filters) regardless of type filter
        const filteredNodes = data.nodes.filter(n => n.isCenter || types.has(n.type));
        const filteredNodeIds = new Set(filteredNodes.map(n => n.id));

        const nodes = filteredNodes.map(n => {
            const stored = nodePositions.get(n.id);
            return {
                ...n,
                radius: n.isCenter ? 28 : 20,
                x: stored ? stored.x : width / 2 + (Math.random() - 0.5) * 100,
                y: stored ? stored.y : height / 2 + (Math.random() - 0.5) * 100
            };
        });

        const nodeById = new Map(nodes.map(n => [n.id, n]));

        const links = data.edges
            .filter(e => filteredNodeIds.has(e.from) && filteredNodeIds.has(e.to))
            .map(e => ({
                ...e,
                source: nodeById.get(e.from),
                target: nodeById.get(e.to)
            }))
            .filter(l => l.source && l.target);

        // Calculate aggregate delta for each node from connected edges
        // Accumulate for both source and target nodes
        const nodeDeltaMap = new Map();
        links.forEach(link => {
            if (link.delta !== undefined) {
                // Add delta to both ends of the edge
                [link.source.id, link.target.id].forEach(nodeId => {
                    if (!nodeDeltaMap.has(nodeId)) {
                        nodeDeltaMap.set(nodeId, { sum: 0, count: 0 });
                    }
                    const entry = nodeDeltaMap.get(nodeId);
                    entry.sum += link.delta;
                    entry.count += 1;
                });
            }
        });

        // Calculate normalized impact scores (0-1) for visual hierarchy
        let minDelta = Infinity, maxDelta = -Infinity;
        nodeDeltaMap.forEach(({ sum, count }) => {
            const avg = sum / count;
            if (avg < minDelta) minDelta = avg;
            if (avg > maxDelta) maxDelta = avg;
        });
        const deltaRange = Math.max(0.001, maxDelta - minDelta);

        // Apply delta data to nodes
        nodes.forEach(n => {
            const deltaData = nodeDeltaMap.get(n.id);
            if (deltaData && !n.isCenter) {
                n.avgDelta = deltaData.sum / deltaData.count;
                // Normalize: 0 = best (most negative), 1 = worst (most positive)
                n.deltaNorm = (n.avgDelta - minDelta) / deltaRange;
                // Impact score: how far from neutral (0 = neutral, 1 = extreme)
                n.impactScore = Math.abs(n.avgDelta) / Math.max(Math.abs(minDelta), Math.abs(maxDelta), 0.001);
                n.hasDeltaData = true;
            } else {
                // Nodes without delta data (e.g. only connected by equipped edges)
                // Treat as neutral - mid-range size and position
                n.avgDelta = undefined;
                n.deltaNorm = 0.5;
                n.impactScore = undefined; // undefined = no delta data, treat specially
                n.hasDeltaData = false;
            }
        });

        currentNodes = nodes;

        const centerNodes = nodes.filter(n => n.isCenter);
        const hasCenterTeam = centerNodes.length > 1;

        // Force simulation
        simulation = d3.forceSimulation(nodes)
            .force('link', d3.forceLink(links).id(d => d.id).distance(140))
            .force('charge', d3.forceManyBody().strength(-300))
            .force('center', d3.forceCenter(width / 2, height / 2))
            .force('x', d3.forceX(width / 2).strength(0.04))
            .force('y', d3.forceY(height / 2).strength(0.04))
            .force('collision', d3.forceCollide().radius(d => {
                let r = d.radius;
                if (!d.isCenter && d.hasDeltaData) {
                    r = d.radius * (0.5 + d.impactScore * 1.0);
                }
                return r + 8;
            }))
            // Radial force: high impact nodes closer to center, low impact further out
            // Nodes without delta data (equipped-only) stay at neutral distance
            // Use weak strength so placement influences position without collapsing clusters
            .force('radial', d3.forceRadial(
                d => {
                    if (d.isCenter) return 0;
                    if (!d.hasDeltaData) return 150; // Neutral position for equipped-only nodes
                    // High impact (1.0) -> radius 80, Low impact (0.0) -> radius 350
                    const t = 1 - d.impactScore;
                    return 80 + (t * t) * 270;
                },
                width / 2,
                height / 2
            ).strength(d => d.isCenter ? 0 : (d.hasDeltaData ? 0.5 : 0.3)))
            .alphaDecay(0.02)
            .velocityDecay(0.3);

        // Pan inertia
        simulation.force('panInertia', () => {
            nodes.forEach(n => {
                n.vx -= panVelocity.x * 2.5;
                n.vy -= panVelocity.y * 2.5;
            });
            panVelocity.x *= 0.92;
            panVelocity.y *= 0.92;
            if (Math.abs(panVelocity.x) < 0.01) panVelocity.x = 0;
            if (Math.abs(panVelocity.y) < 0.01) panVelocity.y = 0;
        });

        // Center clustering with inter-cluster repulsion
        if (hasCenterTeam) {
            addCenterClusterForce(simulation, centerNodes, nodes, links);
        }

        // Team hull
        let teamHull = null;
        if (hasCenterTeam) {
            teamHull = g.append('path')
                .attr('class', 'team-hull')
                .attr('fill', 'rgba(0, 112, 243, 0.08)')
                .attr('stroke', 'rgba(0, 112, 243, 0.25)')
                .attr('stroke-width', 2)
                .attr('stroke-dasharray', '8,4');
        }

        // Links
        const linkGroup = g.append('g');
        const { link, linkOverlay } = createLinks(linkGroup, links, g);

        // Clip paths - account for impact scaling
        const clipDefs = g.append('defs');
        nodes.forEach(n => {
            let clipRadius = n.radius;
            if (!n.isCenter && n.hasDeltaData) {
                clipRadius = n.radius * (0.5 + n.impactScore * 1.0);
            }
            clipDefs.append('clipPath')
                .attr('id', `clip-${n.id.replace(/[^a-zA-Z0-9]/g, '_')}`)
                .append('circle')
                .attr('r', clipRadius - 2);
        });

        // Nodes
        const node = createNodes(g, nodes, simulation, data);
        nodeSelection = node;
        applyHighlights(get(highlightedTokens));

        // Tick handler
        simulation.on('tick', () => {
            nodes.forEach(n => nodePositions.set(n.id, { x: n.x, y: n.y }));

            if (teamHull && centerNodes.length >= 2) {
                updateTeamHull(teamHull, centerNodes);
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

    function applyHighlights(highlightSet) {
        if (!nodeSelection) return;
        const hasHighlights = highlightSet && highlightSet.size > 0;

        nodeSelection
            .classed('highlight', d => hasHighlights && highlightSet.has(d.id))
            .classed('dimmed', d => hasHighlights && !highlightSet.has(d.id) && !d.isCenter);

        if (hasHighlights) {
            nodeSelection.filter(d => highlightSet.has(d.id)).raise();
        }
    }

    function createGridPattern(g, gridSize) {
        const defs = g.append('defs');
        const pattern = defs.append('pattern')
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
    }

    function createArrowMarkers(g) {
        const defs = g.append('defs');
        const types = [
            { id: 'equipped', color: '#f5a623' },
            { id: 'cooccur', color: '#666666' },
            { id: 'positive', color: '#00d9a5' },
            { id: 'negative', color: '#ff4444' }
        ];

        types.forEach(({ id, color }) => {
            defs.append('marker')
                .attr('id', `arrow-${id}`)
                .attr('viewBox', '0 -5 10 10')
                .attr('refX', 28).attr('refY', 0)
                .attr('markerWidth', 8).attr('markerHeight', 8)
                .attr('orient', 'auto')
                .append('path')
                .attr('fill', color)
                .attr('d', 'M0,-5L10,0L0,5');
        });
    }

    function createLinks(linkGroup, links, g) {
        const link = linkGroup.selectAll('.link-visible')
            .data(links)
            .join('line')
            .attr('class', d => {
                const classes = ['link', d.type];
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
                link.classed('active', false);
                link.filter(ld => ld === d).classed('active', true);
                showEdgeTooltip(event, d, true);
            }, { passive: false });

        return { link, linkOverlay };
    }

    function createNodes(g, nodes, sim, data) {
        const node = g.append('g')
            .selectAll('g')
            .data(nodes)
            .join('g')
            .attr('class', d => {
                const classes = ['node', d.type];
                if (d.isCenter) classes.push('center');
                if (d.hasDeltaData) {
                    classes.push(d.avgDelta <= 0 ? 'delta-positive' : 'delta-negative');
                    // Impact tier for visual hierarchy (low, medium, high)
                    if (d.impactScore > 0.7) classes.push('impact-high');
                    else if (d.impactScore > 0.3) classes.push('impact-medium');
                    else classes.push('impact-low');
                } else {
                    // Nodes without delta data (equipped-only) - neutral styling
                    classes.push('no-delta');
                }
                return classes.join(' ');
            })
            .call(createDrag(sim))
            .on('click', (event, d) => handleNodeClick(event, d, data))
            .on('mouseover', (event, d) => showNodeTooltip(event, d))
            .on('mouseout', hideTooltip)
            .on('touchstart', function(event, d) {
                event.preventDefault();
                event.stopPropagation();
                showNodeTooltip(event, d, true);
            }, { passive: false });

        // Helper to get scaled radius - dramatic scaling from 0.5x to 1.5x
        const getScaledRadius = (d) => {
            if (d.isCenter || d.impactScore === undefined) return d.radius;
            // Low impact = 0.5x, high impact = 1.5x (3x range)
            const scale = 0.5 + (d.impactScore * 1.0);
            return d.radius * scale;
        };

        // Background circle - scale based on impact
        node.append('circle')
            .attr('class', 'node-bg')
            .attr('r', getScaledRadius);

        // Colored ring with dynamic glow
        node.append('circle')
            .attr('class', 'node-ring')
            .attr('r', getScaledRadius)
            .style('filter', d => {
                if (d.isCenter || d.impactScore === undefined) return null;
                // Dynamic glow based on impact and direction - much stronger
                const glowIntensity = 2 + (d.impactScore * d.impactScore * 20);
                const glowColor = d.avgDelta <= 0
                    ? `rgba(0, 217, 165, ${0.2 + d.impactScore * 0.8})`
                    : `rgba(255, 68, 68, ${0.2 + d.impactScore * 0.8})`;
                return `drop-shadow(0 0 ${glowIntensity}px ${glowColor})`;
            });

        // Icons - scale based on impact
        const getNodeIconKey = (d) => {
            if (d?.id && typeof d.id === 'string' && (d.id.startsWith('U:') || d.id.startsWith('I:') || d.id.startsWith('T:'))) {
                return d.id.slice(2);
            }
            if (d?.negated && typeof d.label === 'string') {
                return d.label.replace(/^not\s+/i, '');
            }
            return d?.label;
        };

        node.each(function(d) {
            const iconRadius = getScaledRadius(d);
            const iconKey = getNodeIconKey(d);

            if (hasIconFailed(d.type, iconKey)) {
                d3.select(this).append('circle')
                    .attr('class', 'node-fallback')
                    .attr('r', iconRadius - 4);
            } else {
                const iconUrl = getIconUrl(d.type, iconKey);
                if (iconUrl) {
                    const img = d3.select(this).append('image')
                        .attr('class', 'node-icon')
                        .attr('x', -(iconRadius - 2))
                        .attr('y', -(iconRadius - 2))
                        .attr('width', (iconRadius - 2) * 2)
                        .attr('height', (iconRadius - 2) * 2)
                        .attr('clip-path', `url(#clip-${d.id.replace(/[^a-zA-Z0-9]/g, '_')})`)
                        .attr('href', iconUrl)
                        .attr('preserveAspectRatio', 'xMidYMid slice');

                    img.on('error', function() {
                        markIconFailed(d.type, iconKey);
                        d3.select(this).remove();
                        d3.select(this.parentNode).append('circle')
                            .attr('class', 'node-fallback')
                            .attr('r', iconRadius - 4);
                    });
                }
            }
        });

        // Labels - position based on scaled radius
        node.append('text')
            .attr('dy', d => getScaledRadius(d) + 16)
            .text(d => getDisplayName(d.type, d.label));

        return node;
    }

    function createDrag(sim) {
        return d3.drag()
            .on('start', (event, d) => {
                if (!event.active) sim.alphaTarget(0.3).restart();
                d.fx = d.x;
                d.fy = d.y;
            })
            .on('drag', (event, d) => {
                d.fx = event.x;
                d.fy = event.y;
            })
            .on('end', (event, d) => {
                if (!event.active) sim.alphaTarget(0);
                d.fx = null;
                d.fy = null;
            });
    }

    function handleNodeClick(event, d, data) {
        event.stopPropagation();
        posthog.capture('graph_node_clicked', { node_id: d.id, node_type: d.type, is_center: d.isCenter });
        const edge = data.edges.find(e => (e.from === d.id || e.to === d.id) && !d.isCenter);
        if (edge) {
            addToken(edge.token, 'graph');
        } else if (!d.isCenter) {
            addToken(d.id, 'graph');
        }
    }

    function showNodeTooltip(event, d, fromTouch = false) {
        const { x, y } = getTooltipPosition(event);
        showTooltip(x, y, {
            type: 'node',
            title: getDisplayName(d.type, d.label),
            nodeType: d.type,
            isCenter: d.isCenter,
            avgDelta: d.avgDelta,
            impactScore: d.impactScore
        }, fromTouch);
    }

    function showEdgeTooltip(event, d, fromTouch = false) {
        const { x, y } = getTooltipPosition(event);
        let title = d.label ?? d.token.slice(2);
        if (d.token.startsWith('E:')) {
            const [unit, item] = d.token.slice(2).split('|');
            title = `${getDisplayName('unit', unit)} → ${getDisplayName('item', item)}`;
        } else if (d.token.startsWith('U:')) {
            title = getDisplayName('unit', title);
        } else if (d.token.startsWith('I:')) {
            title = getDisplayName('item', title);
        } else if (d.token.startsWith('T:')) {
            title = getDisplayName('trait', title);
        }

        showTooltip(x, y, {
            type: 'edge',
            title,
            delta: d.delta,
            avgWith: d.avg_with,
            avgBase: d.avg_base,
            nWith: d.n_with,
            nBase: d.n_base
        }, fromTouch);
    }

    function getTooltipPosition(event) {
        let pageX, pageY;
        if (event.touches?.length > 0) {
            pageX = event.touches[0].pageX;
            pageY = event.touches[0].pageY;
        } else if (event.changedTouches?.length > 0) {
            pageX = event.changedTouches[0].pageX;
            pageY = event.changedTouches[0].pageY;
        } else {
            pageX = event.pageX;
            pageY = event.pageY;
        }
        return { x: pageX + 12, y: pageY + 12 };
    }

    function addCenterClusterForce(sim, centerNodes, allNodes, links) {
        // Build map: nodeId -> Set of center node ids it's connected to
        const nodeClusterMap = new Map();
        centerNodes.forEach(c => nodeClusterMap.set(c.id, new Set([c.id])));

        links.forEach(link => {
            const sourceId = link.source.id || link.source;
            const targetId = link.target.id || link.target;
            const sourceIsCenter = centerNodes.some(c => c.id === sourceId);
            const targetIsCenter = centerNodes.some(c => c.id === targetId);

            if (sourceIsCenter && !targetIsCenter) {
                if (!nodeClusterMap.has(targetId)) nodeClusterMap.set(targetId, new Set());
                nodeClusterMap.get(targetId).add(sourceId);
            }
            if (targetIsCenter && !sourceIsCenter) {
                if (!nodeClusterMap.has(sourceId)) nodeClusterMap.set(sourceId, new Set());
                nodeClusterMap.get(sourceId).add(targetId);
            }
        });

        // Count nodes per cluster for dynamic spacing
        const clusterSizes = new Map();
        centerNodes.forEach(c => clusterSizes.set(c.id, 0));
        nodeClusterMap.forEach((clusters, nodeId) => {
            if (clusters.size === 1) {
                const clusterId = [...clusters][0];
                clusterSizes.set(clusterId, (clusterSizes.get(clusterId) || 0) + 1);
            }
        });

        sim.force('centerCluster', () => {
            // Attract centers toward shared centroid
            let cx = 0, cy = 0;
            centerNodes.forEach(n => { cx += n.x || 0; cy += n.y || 0; });
            cx /= centerNodes.length;
            cy /= centerNodes.length;

            centerNodes.forEach(n => {
                n.vx += (cx - n.x) * 0.02;
                n.vy += (cy - n.y) * 0.02;
            });

            // Repel centers based on their cluster sizes
            for (let i = 0; i < centerNodes.length; i++) {
                for (let j = i + 1; j < centerNodes.length; j++) {
                    const a = centerNodes[i], b = centerNodes[j];
                    const sizeA = clusterSizes.get(a.id) || 1;
                    const sizeB = clusterSizes.get(b.id) || 1;
                    // Dynamic min distance: base + scaled by combined cluster size
                    const minDistance = 120 + Math.sqrt(sizeA + sizeB) * 25;

                    const dx = b.x - a.x, dy = b.y - a.y;
                    const dist = Math.sqrt(dx * dx + dy * dy) || 1;
                    if (dist < minDistance) {
                        const force = (minDistance - dist) * 0.4 / dist;
                        a.vx -= dx * force; a.vy -= dy * force;
                        b.vx += dx * force; b.vy += dy * force;
                    }
                }
            }

            // Repel peripheral nodes from different clusters
            const peripheralNodes = allNodes.filter(n => !n.isCenter && nodeClusterMap.has(n.id));
            for (let i = 0; i < peripheralNodes.length; i++) {
                for (let j = i + 1; j < peripheralNodes.length; j++) {
                    const a = peripheralNodes[i], b = peripheralNodes[j];
                    const clustersA = nodeClusterMap.get(a.id);
                    const clustersB = nodeClusterMap.get(b.id);

                    // Skip if nodes share any cluster (they can be close)
                    let sharesCluster = false;
                    for (const c of clustersA) {
                        if (clustersB.has(c)) { sharesCluster = true; break; }
                    }
                    if (sharesCluster) continue;

                    // Repel nodes from different clusters
                    const dx = b.x - a.x, dy = b.y - a.y;
                    const dist = Math.sqrt(dx * dx + dy * dy) || 1;
                    const minDist = 60;
                    if (dist < minDist) {
                        const force = (minDist - dist) * 0.3 / dist;
                        a.vx -= dx * force; a.vy -= dy * force;
                        b.vx += dx * force; b.vy += dy * force;
                    }
                }
            }
        });
    }

    function updateTeamHull(hull, centerNodes) {
        const padding = 45;
        const points = centerNodes.map(n => [n.x, n.y]);

        if (centerNodes.length === 2) {
            const [p1, p2] = points;
            const dx = p2[0] - p1[0], dy = p2[1] - p1[1];
            const dist = Math.sqrt(dx * dx + dy * dy);
            const nx = -dy / dist * padding, ny = dx / dist * padding;

            hull.attr('d', `
                M ${p1[0] + nx} ${p1[1] + ny}
                L ${p2[0] + nx} ${p2[1] + ny}
                A ${padding} ${padding} 0 0 1 ${p2[0] - nx} ${p2[1] - ny}
                L ${p1[0] - nx} ${p1[1] - ny}
                A ${padding} ${padding} 0 0 1 ${p1[0] + nx} ${p1[1] + ny} Z
            `);
        } else {
            const hullPoints = d3.polygonHull(points);
            if (hullPoints) {
                const cx = d3.mean(hullPoints, p => p[0]);
                const cy = d3.mean(hullPoints, p => p[1]);
                const expanded = hullPoints.map(p => {
                    const dx = p[0] - cx, dy = p[1] - cy;
                    const dist = Math.sqrt(dx * dx + dy * dy);
                    const scale = (dist + padding) / dist;
                    return [cx + dx * scale, cy + dy * scale];
                });
                hull.attr('d', d3.line().curve(d3.curveCardinalClosed.tension(0.7))(expanded));
            }
        }
    }

    function startAutoFraming(svgEl, zoom, width, height, types) {
        if (driftAnimationId) cancelAnimationFrame(driftAnimationId);

        const driftSpeed = 0.02;
        const paddingFactor = 0.85;

        function drift() {
            driftAnimationId = requestAnimationFrame(drift);
            if (isDragging || !currentNodes.length) return;

            let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
            currentNodes.forEach(n => {
                if (types.has(n.type)) {
                    if (n.x < minX) minX = n.x;
                    if (n.x > maxX) maxX = n.x;
                    if (n.y < minY) minY = n.y;
                    if (n.y > maxY) maxY = n.y;
                }
            });

            if (minX === Infinity) return;

            const margin = 60;
            minX -= margin; maxX += margin; minY -= margin; maxY += margin;

            const contentWidth = maxX - minX, contentHeight = maxY - minY;
            const centerX = (minX + maxX) / 2, centerY = (minY + maxY) / 2;
            const { k: currentK, x: currentX, y: currentY } = currentZoomTransform;

            const viewX = -currentX / currentK, viewY = -currentY / currentK;
            const viewW = width / currentK, viewH = height / currentK;

            const overlapX = Math.max(0, Math.min(maxX, viewX + viewW) - Math.max(minX, viewX));
            const overlapY = Math.max(0, Math.min(maxY, viewY + viewH) - Math.max(minY, viewY));
            const isLookingAtEmptySpace = overlapX <= 0 || overlapY <= 0;

            const scaleX = (width * paddingFactor) / Math.max(contentWidth, 100);
            const scaleY = (height * paddingFactor) / Math.max(contentHeight, 100);
            let targetK = Math.max(0.3, Math.min(2.5, Math.min(scaleX, scaleY)));

            const isOverviewMode = currentK <= targetK * 1.1;
            if (!isLookingAtEmptySpace && !isOverviewMode) return;

            const targetX = width / 2 - centerX * targetK;
            const targetY = height / 2 - centerY * targetK;
            const nextK = currentK + (targetK - currentK) * driftSpeed;
            const nextX = currentX + (targetX - currentX) * driftSpeed;
            const nextY = currentY + (targetY - currentY) * driftSpeed;

            if (Math.abs(nextK - currentK) > 0.0001 ||
                Math.abs(nextX - currentX) > 0.1 ||
                Math.abs(nextY - currentY) > 0.1) {
                svgEl.call(zoom.transform, d3.zoomIdentity.translate(nextX, nextY).scale(nextK));
            }
        }

        drift();
    }

    function renderEmptyState(g, width, height) {
        g.append('foreignObject')
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
    }
</script>

<div class="graph-section">
    <div class="graph-container" data-walkthrough="graph">
        <div class="avp-hud" class:empty={$stats.games === 0} aria-label="Average placement">
            <div class="avp-hud-stat">
                <span class="avp-hud-label">Avg</span>
                <span class="avp-hud-num">
                    <AvgPlacement value={$stats.avgPlacement} showDelta={$stats.games > 0} />
                </span>
            </div>
            <div class="avp-hud-divider"></div>
            <div class="avp-hud-stat">
                <span class="avp-hud-label">Games</span>
                <span class="avp-hud-num">{$stats.games.toLocaleString()}</span>
            </div>
        </div>
        <svg bind:this={container} id="graph"></svg>
    </div>
</div>

<style>
    .graph-section {
        flex: 1;
        min-height: 0;
        min-width: 0;
    }

    .graph-container {
        background: var(--bg-primary);
        position: relative;
        overflow: hidden;
        height: 100%;
        touch-action: none;
    }

    .avp-hud {
        position: absolute;
        right: 12px;
        top: 12px;
        z-index: 5;
        pointer-events: none;
        display: flex;
        align-items: center;
        gap: 0;
        padding: 0;
        border-radius: 6px;
        border: 1px solid var(--border);
        background: rgba(10, 10, 10, 0.85);
        backdrop-filter: blur(8px);
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.35);
        font-variant-numeric: tabular-nums;
        font-feature-settings: "tnum" 1;
        overflow: visible;
    }

    .avp-hud.empty {
        opacity: 0.4;
    }

    .avp-hud-stat {
        display: flex;
        align-items: baseline;
        gap: 5px;
        padding: 6px 10px;
    }

    .avp-hud-divider {
        width: 1px;
        align-self: stretch;
        margin: 5px 0;
        background: var(--border);
        flex-shrink: 0;
    }

    .avp-hud-label {
        font-size: 9px;
        font-weight: 600;
        letter-spacing: 0.03em;
        text-transform: uppercase;
        color: var(--text-tertiary);
        white-space: nowrap;
    }

    .avp-hud-num {
        font-size: 12px;
        font-weight: 700;
        font-variant-numeric: tabular-nums;
        font-feature-settings: "tnum" 1;
        letter-spacing: -0.01em;
        line-height: 1;
        color: var(--text-primary);
    }

    svg {
        width: 100%;
        height: 100%;
        background: transparent;
        touch-action: none;
        cursor: grab;
    }

    svg:active {
        cursor: grabbing;
    }

    /* Node styles */
    :global(.node) {
        cursor: pointer;
        transition: opacity 0.2s ease;
        touch-action: none;
    }

    :global(.node .node-ring) {
        fill: none;
        stroke-width: 3px;
        transition: all 0.2s ease;
    }

    :global(.node.unit .node-ring) { stroke: var(--unit); }
    :global(.node.item .node-ring) { stroke: var(--item); }
    :global(.node.trait .node-ring) { stroke: var(--trait); }

    :global(.node.center .node-ring) {
        stroke-width: 4px;
        filter: drop-shadow(0 0 8px currentColor);
    }

    :global(.node:hover .node-ring) {
        stroke-width: 5px;
        filter: drop-shadow(0 0 12px currentColor);
    }

    :global(.node.highlight .node-ring) {
        stroke-width: 6px;
        filter: drop-shadow(0 0 16px rgba(0, 112, 243, 0.55));
    }

    :global(.node.dimmed) {
        opacity: 0.28;
    }

    /* Delta-based visual hierarchy */
    :global(.node.delta-positive .node-ring) {
        stroke: var(--success) !important;
    }

    :global(.node.delta-negative .node-ring) {
        stroke: var(--error) !important;
    }

    :global(.node.impact-low) {
        opacity: 0.25;
    }

    :global(.node.impact-low text) {
        opacity: 0.4;
        font-size: 10px;
    }

    :global(.node.impact-medium) {
        opacity: 0.6;
    }

    :global(.node.impact-medium text) {
        opacity: 0.7;
    }

    :global(.node.impact-high) {
        opacity: 1;
    }

    :global(.node.impact-high .node-ring) {
        stroke-width: 4px;
    }

    :global(.node .node-bg) { fill: var(--bg-tertiary); }
    :global(.node .node-icon) { pointer-events: none; }

    :global(.node .node-fallback) {
        stroke-width: 2px;
        transition: all 0.2s ease;
    }

    :global(.node.unit .node-fallback) {
        fill: var(--unit);
        stroke: rgba(255, 107, 157, 0.3);
    }

    :global(.node.item .node-fallback) {
        fill: var(--item);
        stroke: rgba(0, 217, 255, 0.3);
    }

    :global(.node.trait .node-fallback) {
        fill: var(--trait);
        stroke: rgba(168, 85, 247, 0.3);
    }

    :global(.node text) {
        font-size: 12px;
        font-weight: 500;
        fill: var(--text-primary);
        pointer-events: none;
        text-anchor: middle;
        font-family: inherit;
    }

    /* Link styles */
    :global(.link-overlay) {
        fill: none;
        stroke: transparent;
        stroke-width: 20px;
        cursor: pointer;
        pointer-events: stroke;
        touch-action: none;
    }

    :global(.link) {
        fill: none;
        stroke-opacity: 0.3;
        transition: stroke-opacity 0.2s ease, stroke-width 0.2s ease;
        pointer-events: none;
    }

    :global(.link.active) {
        stroke-opacity: 0.8;
        stroke-width: 4px;
    }

    :global(.link.equipped) { stroke: var(--equipped); stroke-width: 3px; }
    :global(.link.cooccur) { stroke: var(--text-tertiary); stroke-width: 1.5px; stroke-dasharray: 6,4; }
    :global(.link.positive) { stroke: var(--success); }
    :global(.link.negative) { stroke: var(--error); }

    /* Empty state */
    :global(.empty-state) {
        text-align: center;
        padding: 20px;
        color: var(--text-secondary);
    }

    :global(.empty-state-title) {
        font-size: 18px;
        font-weight: 600;
        margin-bottom: 8px;
        color: var(--text-primary);
    }

    :global(.empty-state-text) {
        font-size: 14px;
        line-height: 1.6;
    }
</style>
