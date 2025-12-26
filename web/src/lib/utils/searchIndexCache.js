import { get } from "svelte/store";
import { fetchSearchIndex } from "../api.js";
import { getDisplayName, assetsLoaded } from "../stores/assets.js";

let cachedIndex = null;
let inflight = null;

function waitForAssets() {
  return new Promise((resolve) => {
    if (get(assetsLoaded)) {
      resolve();
      return;
    }
    const unsubscribe = assetsLoaded.subscribe((loaded) => {
      if (loaded) {
        unsubscribe();
        resolve();
      }
    });
  });
}

export async function getSearchIndex() {
  if (cachedIndex) return cachedIndex;
  if (inflight) return inflight;

  inflight = (async () => {
    // Fetch search index and wait for assets in parallel
    const [index] = await Promise.all([fetchSearchIndex(), waitForAssets()]);
    // Enrich item labels with proper display names from Data Dragon
    for (const entry of index) {
      if (entry.type === "item") {
        entry.label = getDisplayName("item", entry.label);
      }
    }
    cachedIndex = index;
    return index;
  })().finally(() => {
    inflight = null;
  });

  return inflight;
}

