import { mount } from 'svelte'
import './app.css'
import App from './App.svelte'
import posthog, { initPosthog } from './lib/client/posthog'

// Initialize PostHog in the browser
initPosthog()

// Capture initial pageview
posthog.capture('$pageview')

// Capture pageleave when the user unloads the page
window.addEventListener('beforeunload', () => {
  posthog.capture('$pageleave')
})

const app = mount(App, {
  target: document.getElementById('app'),
})

export default app
