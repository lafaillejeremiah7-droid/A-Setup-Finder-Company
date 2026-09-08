import "dotenv/config";
import express from "express";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { GenericProvider } from "./providers/genericProvider.js";
import { buildGexState } from "./gexEngine.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const publicDir = path.resolve(__dirname, "../public");

const app = express();
const port = Number(process.env.PORT || 3000);
const pollMs = Math.max(1000, Number(process.env.POLL_MS || 15000));
const maxAgeMs = Math.max(pollMs * 2, Number(process.env.MAX_DATA_AGE_MS || 60000));

const provider = new GenericProvider(process.env);
let state = null;
let lastError = null;
let lastUpdatedAt = null;
let inFlight = false;

async function refresh() {
  if (inFlight) return;
  inFlight = true;
  try {
    const snapshot = await provider.fetchSnapshot();
    state = buildGexState(snapshot);
    lastUpdatedAt = new Date().toISOString();
    lastError = null;
  } catch (error) {
    lastError = error instanceof Error ? error.message : String(error);
    console.error("[gex-refresh]", lastError);
  } finally {
    inFlight = false;
  }
}

function ageMs() {
  if (!lastUpdatedAt) return null;
  return Date.now() - new Date(lastUpdatedAt).getTime();
}

app.use(express.static(publicDir));

app.get("/api/state", (_req, res) => {
  const age = ageMs();
  res.json({
    ok: Boolean(state),
    stale: age === null ? true : age > maxAgeMs,
    ageMs: age,
    lastUpdatedAt,
    lastError,
    data: state,
  });
});

app.get("/api/levels", (_req, res) => {
  if (!state) return res.status(503).json({ ok: false, error: lastError || "No GEX snapshot yet" });
  res.json({
    ok: true,
    timestamp: state.timestamp,
    regime: state.regime,
    netGex: state.netGex,
    gexUnit: state.gexUnit,
    mnqPrice: state.mnqPrice,
    callWall: state.callWall,
    putWall: state.putWall,
    gammaFlip: state.gammaFlip,
  });
});

app.get("/health", (_req, res) => {
  const age = ageMs();
  const healthy = Boolean(state) && age !== null && age <= maxAgeMs;
  res.status(healthy ? 200 : 503).json({
    healthy,
    lastUpdatedAt,
    ageMs: age,
    lastError,
  });
});

app.get("*", (_req, res) => res.sendFile(path.join(publicDir, "index.html")));

await refresh();
setInterval(refresh, pollMs).unref();

app.listen(port, () => {
  console.log(`MNQ GEX dashboard listening on http://localhost:${port}`);
  console.log(`Polling every ${pollMs} ms`);
});
