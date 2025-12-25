/**
 * Audio recorder utility for voice input
 * Records PCM16 audio at 24kHz for OpenAI Realtime API
 */

import { writable, get } from 'svelte/store';

/**
 * Check if audio recording is supported
 */
export function isRecordingSupported() {
    return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.AudioContext);
}

/**
 * Create an audio recorder instance with Svelte stores
 * Records raw PCM16 samples at 24kHz for OpenAI Realtime API
 *
 * @returns {Object} Recorder controls and state
 */
export function createAudioRecorder() {
    const isRecording = writable(false);
    const error = writable(null);

    let mediaStream = null;
    let audioContext = null;
    let processor = null;
    let source = null;
    let audioChunks = [];
    let resolveRecording = null;

    const SAMPLE_RATE = 24000; // OpenAI Realtime API expects 24kHz

    /**
     * Request microphone permission and set up audio context
     */
    async function init() {
        try {
            mediaStream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    echoCancellation: true,
                    noiseSuppression: true,
                    sampleRate: SAMPLE_RATE,
                }
            });
            error.set(null);
            return true;
        } catch (e) {
            console.error('Microphone access error:', e);

            const errorMessages = {
                'NotAllowedError': 'Microphone access denied. Please allow microphone permissions.',
                'NotFoundError': 'No microphone found.',
                'NotReadableError': 'Microphone is in use by another application.',
                'OverconstrainedError': 'Microphone constraints not satisfied.',
            };

            error.set(errorMessages[e.name] || `Microphone error: ${e.message}`);
            return false;
        }
    }

    /**
     * Convert Float32Array samples to Int16Array (PCM16)
     */
    function floatTo16BitPCM(float32Array) {
        const int16Array = new Int16Array(float32Array.length);
        for (let i = 0; i < float32Array.length; i++) {
            const s = Math.max(-1, Math.min(1, float32Array[i]));
            int16Array[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        return int16Array;
    }

    /**
     * Resample audio to target sample rate
     */
    function resample(audioData, fromRate, toRate) {
        if (fromRate === toRate) {
            return audioData;
        }
        const ratio = fromRate / toRate;
        const newLength = Math.round(audioData.length / ratio);
        const result = new Float32Array(newLength);
        for (let i = 0; i < newLength; i++) {
            const srcIndex = i * ratio;
            const srcIndexFloor = Math.floor(srcIndex);
            const srcIndexCeil = Math.min(srcIndexFloor + 1, audioData.length - 1);
            const t = srcIndex - srcIndexFloor;
            result[i] = audioData[srcIndexFloor] * (1 - t) + audioData[srcIndexCeil] * t;
        }
        return result;
    }

    /**
     * Start recording audio
     * Returns a promise that resolves with the audio blob when stop() is called
     */
    async function start() {
        if (get(isRecording)) {
            return null;
        }

        // Initialize if needed
        if (!mediaStream) {
            const ok = await init();
            if (!ok) return null;
        }

        audioChunks = [];

        try {
            // Create audio context
            audioContext = new (window.AudioContext || window.webkitAudioContext)({
                sampleRate: SAMPLE_RATE,
            });

            // Create source from microphone
            source = audioContext.createMediaStreamSource(mediaStream);

            // Create script processor for capturing raw samples
            // Using 4096 buffer size for good balance of latency and performance
            processor = audioContext.createScriptProcessor(4096, 1, 1);

            processor.onaudioprocess = (event) => {
                if (get(isRecording)) {
                    const inputData = event.inputBuffer.getChannelData(0);
                    // Resample if needed and convert to PCM16
                    const resampled = resample(inputData, audioContext.sampleRate, SAMPLE_RATE);
                    const pcm16 = floatTo16BitPCM(resampled);
                    audioChunks.push(new Uint8Array(pcm16.buffer));
                }
            };

            // Connect the audio graph
            source.connect(processor);
            processor.connect(audioContext.destination);

            // Create promise for recording result
            const recordingPromise = new Promise((resolve) => {
                resolveRecording = resolve;
            });

            isRecording.set(true);
            error.set(null);

            return recordingPromise;

        } catch (e) {
            console.error('Failed to start recording:', e);
            error.set('Failed to start recording. Please try again.');
            return null;
        }
    }

    /**
     * Stop recording and return audio blob
     */
    function stop() {
        if (!get(isRecording)) return;

        isRecording.set(false);

        // Disconnect audio nodes
        if (processor) {
            processor.disconnect();
            processor = null;
        }
        if (source) {
            source.disconnect();
            source = null;
        }
        if (audioContext) {
            audioContext.close();
            audioContext = null;
        }

        // Combine all chunks into a single blob
        if (audioChunks.length > 0 && resolveRecording) {
            const totalLength = audioChunks.reduce((acc, chunk) => acc + chunk.length, 0);
            const combined = new Uint8Array(totalLength);
            let offset = 0;
            for (const chunk of audioChunks) {
                combined.set(chunk, offset);
                offset += chunk.length;
            }

            // Create blob with PCM data
            const blob = new Blob([combined], { type: 'audio/pcm' });
            resolveRecording(blob);
            resolveRecording = null;
        } else if (resolveRecording) {
            resolveRecording(null);
            resolveRecording = null;
        }

        audioChunks = [];
    }

    /**
     * Cancel recording (discard data)
     */
    function cancel() {
        audioChunks = [];
        if (resolveRecording) {
            resolveRecording(null);
            resolveRecording = null;
        }
        stop();
    }

    /**
     * Clean up resources
     */
    function destroy() {
        cancel();
        if (mediaStream) {
            mediaStream.getTracks().forEach(track => track.stop());
            mediaStream = null;
        }
    }

    return {
        // State
        isRecording,
        error,

        // Actions
        start,
        stop,
        cancel,
        destroy,
    };
}
