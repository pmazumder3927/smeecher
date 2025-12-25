import { fetchSearchIndex } from "../api.js";

let cachedIndex = null;
let inflight = null;

export async function getSearchIndex() {
  if (cachedIndex) return cachedIndex;
  if (inflight) return inflight;

  inflight = (async () => {
    const index = await fetchSearchIndex();
    cachedIndex = index;
    return index;
  })().finally(() => {
    inflight = null;
  });

  return inflight;
}

