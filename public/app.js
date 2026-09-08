const $ = (id) => document.getElementById(id);

function money(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  const sign = n < 0 ? "-" : "+";
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(1)}K`;
  return `${sign}$${abs.toFixed(0)}`;
}

function price(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(2) : "—";
}

function setStatus(ok, stale, text) {
  const dot = $("statusDot");
  dot.className = `dot ${ok && !stale ? "ok" : stale ? "warn" : "bad"}`;
  $("statusText").textContent = text;
}

function placeLine(id, value, min, max) {
  const el = $(id);
  const n = Number(value);
  if (!Number.isFinite(n) || max <= min) {
    el.style.display = "none";
    return;
  }
  const pct = ((max - n) / (max - min)) * 100;
  el.style.display = "flex";
  el.style.top = `${Math.min(100, Math.max(0, pct))}%`;
  el.querySelector("b").textContent = price(n);
}

function renderMap(data) {
  const values = [
    data.callWall?.mnqLevel,
    data.putWall?.mnqLevel,
    data.gammaFlip?.mnqLevel,
    data.mnqPrice,
  ].map(Number).filter(Number.isFinite);

  if (values.length < 2) return;
  const rawMin = Math.min(...values);
  const rawMax = Math.max(...values);
  const pad = Math.max(25, (rawMax - rawMin) * 0.18);
  const min = rawMin - pad;
  const max = rawMax + pad;

  $("mapRange").textContent = `${price(max)} → ${price(min)}`;
  placeLine("callLine", data.callWall?.mnqLevel, min, max);
  placeLine("priceLine", data.mnqPrice, min, max);
  placeLine("flipLine", data.gammaFlip?.mnqLevel, min, max);
  placeLine("putLine", data.putWall?.mnqLevel, min, max);
}

function render(payload) {
  const { data, stale, ageMs, lastError } = payload;
  if (!data) {
    setStatus(false, true, lastError || "Waiting for GEX data");
    return;
  }

  setStatus(true, stale, stale ? "STALE DATA" : "LIVE");

  $("regime").textContent = data.regime;
  $("regime").dataset.regime = data.regime;
  $("netGex").textContent = `Net GEX ${money(data.netGex)} / 1%`;
  $("mnqPrice").textContent = price(data.mnqPrice);
  $("basis").textContent = Number.isFinite(data.basis) ? `NDX→MNQ basis ${data.basis >= 0 ? "+" : ""}${data.basis.toFixed(2)}` : "Direct/normalized mapping";
  $("source").textContent = data.sourceUnderlying;
  $("freshness").textContent = ageMs == null ? "—" : `${Math.round(ageMs / 1000)}s since refresh`;

  $("callLevel").textContent = price(data.callWall?.mnqLevel);
  $("callGex").textContent = `${money(data.callWall?.gex)} GEX / 1% · source strike ${price(data.callWall?.strike)}`;
  $("callStrength").textContent = data.callWall?.strength || "—";

  $("putLevel").textContent = price(data.putWall?.mnqLevel);
  $("putGex").textContent = `${money(data.putWall?.gex)} GEX / 1% · source strike ${price(data.putWall?.strike)}`;
  $("putStrength").textContent = data.putWall?.strength || "—";

  $("flipLevel").textContent = price(data.gammaFlip?.mnqLevel);
  $("timestamp").textContent = `Source snapshot ${new Date(data.timestamp).toLocaleString()}`;

  renderMap(data);
}

async function refresh() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    const payload = await response.json();
    render(payload);
  } catch (error) {
    setStatus(false, false, "Connection error");
    console.error(error);
  }
}

refresh();
setInterval(refresh, 2000);
