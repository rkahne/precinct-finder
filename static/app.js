/* ============================================================
   Precinct Leader Finder — Frontend Logic
   ============================================================ */

const COLORS = {
  full:    "#28a745",
  partial: "#ffc107",
  empty:   "#dc3545",
};

let map, precinctLayer, highlightLayer, userMarker;
let geojsonData = null;
let contactEmail = "YOUR_EMAIL_HERE@example.com";
let leaderThreshold = 3;

// ---------------------------------------------------------------------------
// Initialization
// ---------------------------------------------------------------------------

async function init() {
  map = L.map("map", { zoomControl: true }).setView([38.2527, -85.7585], 11);

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19,
  }).addTo(map);

  // Fetch app config (contact email, threshold)
  try {
    const cfg = await fetch("/api/config").then((r) => r.json());
    contactEmail = cfg.contact_email || contactEmail;
    leaderThreshold = cfg.leader_threshold || leaderThreshold;
  } catch (_) {}

  // Load and render precinct GeoJSON
  try {
    const resp = await fetch("/data/precincts.geojson");
    geojsonData = await resp.json();
    renderPrecincts();
    addMapLegend();
    loadSummaryStats();
  } catch (err) {
    console.error("Failed to load precinct data:", err);
  }

  // Wire up search
  const btn = document.getElementById("search-btn");
  const input = document.getElementById("address-input");
  btn.addEventListener("click", doSearch);
  input.addEventListener("keypress", (e) => { if (e.key === "Enter") doSearch(); });
}

// ---------------------------------------------------------------------------
// Map rendering
// ---------------------------------------------------------------------------

function leaderColor(n) {
  if (n >= leaderThreshold) return COLORS.full;
  if (n >= 1)               return COLORS.partial;
  return                           COLORS.empty;
}

function baseStyle(feature) {
  return {
    fillColor: leaderColor(feature.properties.unique_leaders),
    weight: 0.6,
    opacity: 0.8,
    color: "#444",
    fillOpacity: 0.45,
  };
}

function renderPrecincts() {
  precinctLayer = L.geoJSON(geojsonData, {
    style: baseStyle,
    onEachFeature(feature, layer) {
      const p = feature.properties;
      const status =
        p.unique_leaders >= leaderThreshold ? "Fully staffed" :
        p.unique_leaders > 0 ? "Needs more leaders" : "No leaders";
      layer.bindPopup(
        `<strong>Precinct ${p.precinct}</strong><br>` +
        `LD ${p.leg_dist} &nbsp;|&nbsp; ` +
        `<strong>${p.unique_leaders}</strong> leader(s)<br>` +
        `<em>${status}</em>`
      );
      layer.on("mouseover", function () {
        this.setStyle({ weight: 2, fillOpacity: 0.75 });
        this.openPopup();
      });
      layer.on("mouseout", function () {
        precinctLayer.resetStyle(this);
        this.closePopup();
      });
    },
  }).addTo(map);
}

function addMapLegend() {
  const legend = L.control({ position: "bottomright" });
  legend.onAdd = () => {
    const div = L.DomUtil.create("div", "map-legend");
    div.innerHTML =
      `<h4>Leaders per Precinct</h4>` +
      `<div class="row"><span class="dot" style="background:${COLORS.full}"></span>3 or more</div>` +
      `<div class="row"><span class="dot" style="background:${COLORS.partial}"></span>1–2</div>` +
      `<div class="row"><span class="dot" style="background:${COLORS.empty}"></span>None</div>`;
    return div;
  };
  legend.addTo(map);
}

async function loadSummaryStats() {
  try {
    const s = await fetch("/data/summary.json").then((r) => r.json());
    const el = document.getElementById("header-stats");
    if (el) {
      el.textContent =
        `${s.precincts_with_enough} of ${s.total_precincts} precincts fully staffed`;
    }
  } catch (_) {}
}

// ---------------------------------------------------------------------------
// Address search
// ---------------------------------------------------------------------------

async function doSearch() {
  const address = document.getElementById("address-input").value.trim();
  if (!address) return;

  setLoading(true);
  clearResults();
  clearError();

  try {
    const resp = await fetch(`/api/geocode?address=${encodeURIComponent(address)}`);
    const data = await resp.json();

    if (!resp.ok) {
      showError(data.error || "Failed to look up address.");
      return;
    }

    const { lat, lon, matched_address } = data;

    if (!geojsonData) {
      showError("Precinct data not yet loaded. Please wait a moment and try again.");
      return;
    }

    // Point-in-polygon using Turf.js
    const pt = turf.point([lon, lat]);
    const found = geojsonData.features.find((f) =>
      turf.booleanPointInPolygon(pt, f)
    );

    if (!found) {
      showError(
        "Your address was geocoded successfully, but it appears to be outside " +
        "Jefferson County's precinct boundaries. Please verify it's a Louisville/Jefferson County, KY address."
      );
      return;
    }

    showResults(found, matched_address, lat, lon);
  } catch (err) {
    showError("An unexpected error occurred. Please try again.");
    console.error(err);
  } finally {
    setLoading(false);
  }
}

// ---------------------------------------------------------------------------
// Results display
// ---------------------------------------------------------------------------

function showResults(feature, matchedAddress, lat, lon) {
  const p = feature.properties;
  const count = p.unique_leaders;
  const isFull = count >= leaderThreshold;

  // Matched address
  document.getElementById("matched-addr").textContent = matchedAddress;

  // Precinct info
  document.getElementById("r-precinct").textContent = p.precinct;
  document.getElementById("r-ld").textContent = p.leg_dist;
  document.getElementById("r-count").textContent = count;

  // Status badge
  const badge = document.getElementById("r-status-badge");
  const badgeLabel = document.getElementById("r-badge-label");
  badge.className = "precinct-card-right " + (isFull ? "status-full" : count > 0 ? "status-needs" : "status-empty");
  badgeLabel.textContent = isFull ? "Fully Staffed" : "Needs Leaders";

  // CTA or full-staffed message
  if (isFull) {
    document.getElementById("full-precinct").textContent = p.precinct;
    document.getElementById("full-count").textContent = count;
    show("full-box");
    hide("cta-box");
  } else {
    document.getElementById("cta-precinct").textContent = p.precinct;
    document.getElementById("cta-count").textContent = count;

    const subject = encodeURIComponent(
      `Interest in Becoming a Precinct Leader — Precinct ${p.precinct}`
    );
    const body = encodeURIComponent(
      `Hello,\n\nI am interested in becoming a precinct leader for ` +
      `Precinct ${p.precinct} (Legislative District ${p.leg_dist}).\n\n` +
      `My name is:\nMy phone/email:\nBest time to reach me:\n\nThank you!`
    );
    document.getElementById("cta-email-link").href =
      `mailto:${contactEmail}?subject=${subject}&body=${body}`;

    show("cta-box");
    hide("full-box");
  }

  show("result-box");

  // ----- Map updates -----

  // User location marker
  if (userMarker) map.removeLayer(userMarker);
  userMarker = L.circleMarker([lat, lon], {
    radius: 9,
    fillColor: "#fff",
    color: "#003087",
    weight: 3,
    fillOpacity: 1,
  })
    .addTo(map)
    .bindPopup(`<strong>Your address</strong><br>${matchedAddress}`)
    .openPopup();

  // Highlight found precinct
  if (highlightLayer) map.removeLayer(highlightLayer);
  highlightLayer = L.geoJSON(feature, {
    style: {
      fillColor: leaderColor(count),
      weight: 3,
      color: "#003087",
      fillOpacity: 0.65,
      dashArray: "6 4",
    },
  }).addTo(map);

  // Zoom to fit the precinct
  map.fitBounds(highlightLayer.getBounds(), { padding: [50, 50], maxZoom: 15 });
}

// ---------------------------------------------------------------------------
// UI helpers
// ---------------------------------------------------------------------------

function setLoading(on) {
  document.getElementById("search-btn").disabled = on;
  toggle("loading", on);
}

function clearResults() {
  hide("result-box");
  hide("cta-box");
  hide("full-box");
}

function clearError() {
  hide("error-box");
}

function showError(msg) {
  document.getElementById("error-msg").textContent = msg;
  show("error-box");
}

function show(id) { document.getElementById(id).classList.remove("hidden"); }
function hide(id) { document.getElementById(id).classList.add("hidden"); }
function toggle(id, on) { on ? show(id) : hide(id); }

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", init);
