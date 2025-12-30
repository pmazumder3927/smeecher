import http from "node:http";

const API_URL = process.env.SMEECHER_API_URL || "http://localhost:8000/stats";
const TIMEOUT_MS = Number.parseInt(process.env.SMEECHER_API_WAIT_MS || "60000", 10);
const START = Date.now();

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function pingOnce() {
  return new Promise((resolve) => {
    const req = http.get(API_URL, (res) => {
      res.resume();
      resolve(res.statusCode >= 200 && res.statusCode < 500);
    });
    req.on("error", () => resolve(false));
    req.setTimeout(1000, () => {
      req.destroy();
      resolve(false);
    });
  });
}

while (true) {
  const ok = await pingOnce();
  if (ok) process.exit(0);

  const elapsed = Date.now() - START;
  if (elapsed > TIMEOUT_MS) {
    console.error(`[waitForApi] Timed out after ${Math.round(elapsed / 1000)}s waiting for ${API_URL}`);
    process.exit(1);
  }

  await sleep(200);
}

