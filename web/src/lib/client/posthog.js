import posthog from 'posthog-js';

let initialized = false;

/**
 * Initialize the PostHog client on the browser.
 * Ensures init is only called once.
 */
export function initPosthog() {
  if (typeof window !== 'undefined' && !initialized) {
    const apiKey = import.meta.env.VITE_POSTHOG_KEY;
    const apiHost = import.meta.env.VITE_POSTHOG_HOST || 'https://us.i.posthog.com';

    if (!apiKey) {
      return;
    }

    posthog.init(apiKey, {
      api_host: apiHost,
      capture_pageview: false,
      capture_pageleave: false,
      capture_exceptions: true,
    });
    initialized = true;
  }
}

export default posthog;
