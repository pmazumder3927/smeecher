<script>
    import { onMount, onDestroy } from 'svelte';
    import { createAudioRecorder, isRecordingSupported } from '../utils/audioRecorder.js';
    import { parseVoiceAudio } from '../api.js';
    import { addToken } from '../stores/state.js';
    import { getDisplayName } from '../stores/assets.js';
    import posthog from '../client/posthog';

    // Audio recorder state
    let recorder = null;
    let isSupported = false;

    // Reactive state
    let isRecording = false;
    let errorMessage = null;
    let parsedTokens = [];
    let isParsing = false;
    let transcript = '';

    // Recording promise
    let recordingPromise = null;

    onMount(async () => {
        if (typeof window !== 'undefined') {
            isSupported = isRecordingSupported();

            if (isSupported) {
                recorder = createAudioRecorder();

                // Subscribe to stores
                recorder.isRecording.subscribe(v => isRecording = v);
                recorder.error.subscribe(v => errorMessage = v);
            }
        }
    });

    onDestroy(() => {
        if (recorder) {
            recorder.destroy();
        }
    });

    async function startRecording() {
        if (!recorder || !isSupported) return;
        errorMessage = null;
        parsedTokens = [];
        transcript = '';
        recordingPromise = recorder.start();
    }

    async function stopRecording() {
        if (!recorder) return;
        recorder.stop();

        // Wait for the audio blob
        const audioBlob = await recordingPromise;
        recordingPromise = null;

        if (!audioBlob) {
            return;
        }

        // Send to server for transcription and parsing
        isParsing = true;
        try {
            const result = await parseVoiceAudio(audioBlob);
            transcript = result.transcript || '';
            parsedTokens = result.tokens || [];

            if (parsedTokens.length > 0) {
                for (const t of parsedTokens) {
                    addToken(t.token);
                }
                posthog.capture('voice_input', {
                    transcript,
                    tokens_added: parsedTokens.length,
                    tokens: parsedTokens.map(t => t.token)
                });
            }
        } catch (e) {
            console.error('Voice parsing failed:', e);
            errorMessage = e.message || 'Voice parsing failed';
        } finally {
            isParsing = false;
        }

        // Reset state after a brief display
        setTimeout(() => {
            parsedTokens = [];
            transcript = '';
        }, 1500);
    }

    // Push-to-talk handlers
    function handleMouseDown() {
        startRecording();
    }

    function handleMouseUp() {
        if (isRecording) {
            stopRecording();
        }
    }

    function handleMouseLeave() {
        if (isRecording) {
            stopRecording();
        }
    }

    // Keyboard handler for spacebar (global)
    function handleKeyDown(event) {
        // Only trigger if not typing in an input
        if (event.target.tagName === 'INPUT' || event.target.tagName === 'TEXTAREA') {
            return;
        }

        if (event.code === 'Space' && !event.repeat && !isRecording) {
            event.preventDefault();
            startRecording();
        }
    }

    function handleKeyUp(event) {
        if (event.code === 'Space' && isRecording) {
            event.preventDefault();
            stopRecording();
        }
    }

    function getTypeClass(type) {
        return type === 'unit' ? 'unit' :
               type === 'item' ? 'item' :
               type === 'trait' ? 'trait' : 'item';
    }
</script>

<svelte:window on:keydown={handleKeyDown} on:keyup={handleKeyUp} />

{#if isSupported}
    <div class="voice-input">
        <button
            class="voice-btn"
            class:recording={isRecording}
            on:mousedown={handleMouseDown}
            on:mouseup={handleMouseUp}
            on:mouseleave={handleMouseLeave}
            on:touchstart|preventDefault={handleMouseDown}
            on:touchend|preventDefault={handleMouseUp}
            title="Hold to speak (or press Spacebar)"
            aria-label="Voice input"
        >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
                <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
                <line x1="12" y1="19" x2="12" y2="23"/>
                <line x1="8" y1="23" x2="16" y2="23"/>
            </svg>
            {#if isRecording}
                <span class="pulse"></span>
            {/if}
        </button>

        {#if isRecording || isParsing || parsedTokens.length > 0}
            <div class="voice-overlay">
                <div class="overlay-header">
                    {#if isRecording}
                        <span class="recording-indicator">Recording...</span>
                    {:else if isParsing}
                        <span class="parsing-indicator">Transcribing...</span>
                    {:else}
                        <span class="done-indicator">Added!</span>
                    {/if}
                </div>

                {#if transcript}
                    <div class="transcript">
                        "{transcript}"
                    </div>
                {/if}

                {#if isParsing}
                    <div class="parsing-spinner"></div>
                {:else if parsedTokens.length > 0}
                    <div class="parsed-tokens">
                        {#each parsedTokens as token}
                            <span class="preview-chip {getTypeClass(token.type)}">
                                {getDisplayName(token.type, token.label)}
                            </span>
                        {/each}
                    </div>
                {:else if transcript && !isRecording}
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

    .voice-btn.recording {
        border-color: #ef4444;
        background: rgba(239, 68, 68, 0.15);
        color: #ef4444;
    }

    .voice-btn svg {
        width: 18px;
        height: 18px;
    }

    .pulse {
        position: absolute;
        inset: -4px;
        border-radius: 12px;
        border: 2px solid #ef4444;
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

    .recording-indicator {
        color: #ef4444;
        font-size: 12px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        display: flex;
        align-items: center;
        gap: 8px;
    }

    .recording-indicator::before {
        content: '';
        width: 8px;
        height: 8px;
        background: #ef4444;
        border-radius: 50%;
        animation: blink 1s infinite;
    }

    @keyframes blink {
        0%, 50% { opacity: 1; }
        51%, 100% { opacity: 0.3; }
    }

    .parsing-indicator {
        color: var(--text-secondary);
        font-size: 12px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.1em;
    }

    .done-indicator {
        color: #22c55e;
        font-size: 12px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.1em;
    }

    .parsing-spinner {
        width: 24px;
        height: 24px;
        border: 2px solid var(--border);
        border-top-color: var(--accent);
        border-radius: 50%;
        animation: spin 0.8s linear infinite;
        margin: 8px auto;
    }

    @keyframes spin {
        to { transform: rotate(360deg); }
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
        content: '';
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
        from { opacity: 0; }
        to { opacity: 1; }
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
