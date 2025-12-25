/**
 * WebRTC client for OpenAI Realtime API
 * Provides low-latency voice input with direct browser <-> OpenAI connection
 */

import { writable, get } from 'svelte/store';

/**
 * Create a WebRTC voice client for OpenAI Realtime API
 * @param {Function} onToolCall - Callback when tool call is received
 * @returns {Object} Voice client controls and state
 */
export function createRealtimeVoice(onToolCall) {
    const isConnected = writable(false);
    const isListening = writable(false);
    const error = writable(null);
    const transcript = writable('');

    let peerConnection = null;
    let dataChannel = null;
    let mediaStream = null;
    let sessionConfig = null;

    /**
     * Start a voice session - connects to OpenAI via WebRTC
     */
    async function connect() {
        if (get(isConnected)) return;

        try {
            error.set(null);

            // Fetch session config first
            const configRes = await fetch('/voice-session-config');
            if (!configRes.ok) {
                throw new Error('Failed to fetch session config');
            }
            sessionConfig = await configRes.json();

            // Create peer connection
            peerConnection = new RTCPeerConnection();

            // Set up data channel for events
            dataChannel = peerConnection.createDataChannel('oai-events');

            dataChannel.onopen = () => {
                console.log('Data channel open, sending session config');
                isConnected.set(true);
                // Send session.update to configure tools
                if (sessionConfig) {
                    dataChannel.send(JSON.stringify(sessionConfig));
                }
            };

            dataChannel.onclose = () => {
                console.log('Data channel closed');
                isConnected.set(false);
            };

            dataChannel.onmessage = (e) => {
                handleServerEvent(JSON.parse(e.data));
            };

            // Get microphone access
            mediaStream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    echoCancellation: true,
                    noiseSuppression: true,
                }
            });

            // Add audio track
            const audioTrack = mediaStream.getTracks()[0];
            peerConnection.addTrack(audioTrack, mediaStream);

            // Create and send offer
            const offer = await peerConnection.createOffer();
            await peerConnection.setLocalDescription(offer);

            // Send to our server which forwards to OpenAI
            const response = await fetch('/realtime-session', {
                method: 'POST',
                body: offer.sdp,
                headers: {
                    'Content-Type': 'application/sdp',
                },
            });

            if (!response.ok) {
                const text = await response.text();
                throw new Error(`Session creation failed: ${text}`);
            }

            // Set remote description from OpenAI's answer
            const answerSdp = await response.text();
            await peerConnection.setRemoteDescription({
                type: 'answer',
                sdp: answerSdp,
            });

            isListening.set(true);

        } catch (e) {
            console.error('WebRTC connection error:', e);
            error.set(e.message || 'Failed to connect');
            disconnect();
        }
    }

    /**
     * Handle events from OpenAI via data channel
     */
    function handleServerEvent(event) {
        const type = event.type || '';

        // Transcript updates
        if (type === 'conversation.item.input_audio_transcription.completed') {
            transcript.set(event.transcript || '');
        }

        // Function call completed
        if (type === 'response.function_call_arguments.done') {
            try {
                const args = JSON.parse(event.arguments || '{}');
                if (event.name === 'add_search_filters' && onToolCall) {
                    onToolCall(args);
                }
            } catch (e) {
                console.error('Failed to parse tool call:', e);
            }
        }

        // Handle errors
        if (type === 'error') {
            console.error('OpenAI error:', event.error);
            error.set(event.error?.message || 'OpenAI error');
        }
    }

    /**
     * Send an event to OpenAI via data channel
     */
    function sendEvent(event) {
        if (dataChannel && dataChannel.readyState === 'open') {
            dataChannel.send(JSON.stringify(event));
        }
    }

    /**
     * Trigger a response (useful after VAD detects speech end)
     */
    function triggerResponse() {
        sendEvent({ type: 'response.create' });
    }

    /**
     * Disconnect and clean up
     */
    function disconnect() {
        isListening.set(false);
        isConnected.set(false);

        if (mediaStream) {
            mediaStream.getTracks().forEach(track => track.stop());
            mediaStream = null;
        }

        if (dataChannel) {
            dataChannel.close();
            dataChannel = null;
        }

        if (peerConnection) {
            peerConnection.close();
            peerConnection = null;
        }
    }

    return {
        // State
        isConnected,
        isListening,
        error,
        transcript,

        // Actions
        connect,
        disconnect,
        sendEvent,
        triggerResponse,
    };
}
