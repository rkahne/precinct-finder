/* ============================================================
   Precinct Leader Finder — Frontend Logic
   ============================================================ */

const COLORS = {
  full:    "#28a745",
  partial: "#ffc107",
  empty:   "#dc3545",
};

let map, precinctLayer, highlightLayer, userMarker;
let geojsonData    = null;
let leaderThreshold = 3;
let lastSearchData  = null; // stored for form pre-fill

// ---------------------------------------------------------------------------
// Initialization
// ---------------------------------------------------------------------------

async function init() {
  map = L.map("map", { zoomControl: true }).setView([38.2527, -85.7585], 11);

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19,
  }).addTo(map);

  // Fetch app config
  try {
    const cfg = await fetch("/api/config").then((r) => r.json());
    leaderThreshold = cfg.leader_threshold || leaderThreshold;
  } catch (_) {}

  // Load precinct GeoJSON, then overlay live leader counts from DB
  try {
    const resp = await fetch("/data/precincts.geojson");
    geojsonData = await resp.json();
    await _mergeLiveLeaderCounts();
    renderPrecincts();
    addMapLegend();
  } catch (err) {
    console.error("Failed to load precinct data:", err);
  }

  loadSummaryStats();
  wireModal();

  const btn   = document.getElementById("search-btn");
  const input = document.getElementById("address-input");
  btn.addEventListener("click", doSearch);
  input.addEventListener("keypress", (e) => { if (e.key === "Enter") doSearch(); });
}

// ---------------------------------------------------------------------------
// Merge live leader counts from the database into the loaded GeoJSON
// Falls back silently to GeoJSON values if the API is unavailable.
// ---------------------------------------------------------------------------

async function _mergeLiveLeaderCounts() {
  try {
    const counts = await fetch("/api/leader-counts").then((r) => r.json());
    if (!counts || !geojsonData) return;
    geojsonData.features.forEach((f) => {
      const code = f.properties.precinct;
      if (counts[code] !== undefined) {
        f.properties.unique_leaders = counts[code].unique_leaders;
      }
    });
  } catch (_) {}
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
      const p      = feature.properties;
      const status =
        p.unique_leaders >= leaderThreshold ? "Fully staffed" :
        p.unique_leaders > 0 ? "Needs more leaders" : "No leaders";
      layer.bindTooltip(
        `<strong>Precinct ${p.precinct}</strong> &nbsp;|&nbsp; ` +
        `LD ${p.leg_dist} &nbsp;|&nbsp; ` +
        `<strong>${p.unique_leaders}</strong> leader(s) &mdash; <em>${status}</em>`,
        { sticky: true, opacity: 0.92 }
      );
      layer.on("mouseover", function () {
        this.setStyle({ weight: 2, fillOpacity: 0.75 });
      });
      layer.on("mouseout", function () {
        precinctLayer.resetStyle(this);
      });
      layer.on("click", function () {
        showPrecinctFromMap(feature);
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
    const s  = await fetch("/api/summary").then((r) => r.json());
    const el = document.getElementById("header-stats");
    if (el && s.total_precincts) {
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

    const { lat, lon, matched_address, address_input } = data;

    if (!geojsonData) {
      showError("Precinct data not yet loaded. Please wait a moment and try again.");
      return;
    }

    const pt    = turf.point([lon, lat]);
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

    // Log the search (fire-and-forget)
    fetch("/api/track-search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        address_input:   address_input || address,
        matched_address: matched_address,
        precinct_code:   found.properties.precinct,
        leg_dist:        found.properties.leg_dist,
        lat,
        lon,
      }),
    }).catch(() => {});

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

// Called when the user clicks a precinct on the map directly.
function showPrecinctFromMap(feature) {
  clearResults();
  clearError();
  _renderResultPanel(feature, null, null, null);

  // Highlight the clicked precinct
  if (highlightLayer) map.removeLayer(highlightLayer);
  highlightLayer = L.geoJSON(feature, {
    style: {
      fillColor: leaderColor(feature.properties.unique_leaders),
      weight: 3, color: "#003087", fillOpacity: 0.65, dashArray: "6 4",
    },
  }).addTo(map);

  // Scroll the panel into view on mobile
  document.getElementById("panel").scrollTop = 0;
}

// Called after a successful address search.
function showResults(feature, matchedAddress, lat, lon) {
  _renderResultPanel(feature, matchedAddress, lat, lon);

  if (userMarker) map.removeLayer(userMarker);
  userMarker = L.circleMarker([lat, lon], {
    radius: 9, fillColor: "#fff", color: "#003087", weight: 3, fillOpacity: 1,
  })
    .addTo(map)
    .bindPopup(`<strong>Your address</strong><br>${matchedAddress}`)
    .openPopup();

  if (highlightLayer) map.removeLayer(highlightLayer);
  highlightLayer = L.geoJSON(feature, {
    style: {
      fillColor: leaderColor(feature.properties.unique_leaders),
      weight: 3, color: "#003087", fillOpacity: 0.65, dashArray: "6 4",
    },
  }).addTo(map);

  map.fitBounds(highlightLayer.getBounds(), { padding: [50, 50], maxZoom: 15 });
}

// Shared panel rendering for both address search and map click.
// matchedAddress / lat / lon are null when triggered by a map click.
function _renderResultPanel(feature, matchedAddress, lat, lon) {
  const p      = feature.properties;
  const count  = p.unique_leaders;
  const isFull = count >= leaderThreshold;

  const matchedRow = document.getElementById("matched-addr-row");
  if (matchedAddress) {
    document.getElementById("matched-addr").textContent = matchedAddress;
    matchedRow.classList.remove("hidden");
  } else {
    matchedRow.classList.add("hidden");
  }

  document.getElementById("r-precinct").textContent = p.precinct;
  document.getElementById("r-ld").textContent        = p.leg_dist;
  document.getElementById("r-count").textContent     = count;

  const badge      = document.getElementById("r-status-badge");
  const badgeLabel = document.getElementById("r-badge-label");
  badge.className  = "precinct-card-right " +
    (isFull ? "status-full" : count > 0 ? "status-needs" : "status-empty");
  badgeLabel.textContent = isFull ? "Fully Staffed" : "Needs Leaders";

  // Store for form pre-fill regardless of status
  lastSearchData = {
    precinct_code:   p.precinct,
    leg_dist:        p.leg_dist,
    matched_address: matchedAddress || null,
  };

  if (isFull) {
    document.getElementById("full-precinct").textContent = p.precinct;
    document.getElementById("full-count").textContent    = count;
    show("full-box");
    hide("cta-box");
  } else {
    document.getElementById("cta-precinct").textContent = p.precinct;
    document.getElementById("cta-count").textContent    = count;
    show("cta-box");
    hide("full-box");
  }

  show("result-box");
}

// ---------------------------------------------------------------------------
// Interest form modal
// ---------------------------------------------------------------------------

function wireModal() {
  document.getElementById("cta-open-form").addEventListener("click", openModal);
  document.getElementById("full-open-form").addEventListener("click", openModal);
  document.getElementById("modal-close-btn").addEventListener("click", closeModal);
  document.getElementById("modal-cancel-btn").addEventListener("click", closeModal);
  document.getElementById("interest-modal").addEventListener("click", (e) => {
    if (e.target === e.currentTarget) closeModal();
  });
  document.getElementById("interest-form").addEventListener("submit", handleFormSubmit);
}

function openModal() {
  const data = lastSearchData || {};

  // Reset form state first
  document.getElementById("interest-form").reset();

  // Pre-fill hidden fields and precinct display
  document.getElementById("form-precinct").value         = data.precinct_code || "";
  document.getElementById("form-leg-dist").value         = data.leg_dist      || "";
  document.getElementById("form-precinct-display").value = data.precinct_code || "";

  // Parse matched address → "123 MAIN ST, LOUISVILLE, KY, 40205"
  const parts        = (data.matched_address || "").split(",").map((s) => s.trim());
  const streetPart   = parts[0] || "";
  const cityPart     = parts[1] || "Louisville";
  // parts[2] is "KY 40205" or just "KY"; split on space to separate state and zip
  const stateZip     = (parts[2] || "KY").trim().split(/\s+/);
  const statePart    = stateZip[0] || "KY";
  const zipPart      = stateZip[1] || (parts[3] || "");

  document.getElementById("form-street-address").value = streetPart;
  document.getElementById("form-city").value           = cityPart;
  document.getElementById("form-state").value          = statePart;
  document.getElementById("form-zip").value            = zipPart;

  hide("modal-success");
  hide("modal-error");
  document.getElementById("interest-form").classList.remove("hidden");
  document.getElementById("form-submit-btn").disabled    = false;
  document.getElementById("form-submit-btn").textContent = "Submit";

  show("interest-modal");
  document.getElementById("form-legal-first-name").focus();

  // Show scroll hint, hide once user scrolls near bottom
  const box  = document.querySelector(".modal-box");
  const hint = document.getElementById("modal-scroll-hint");
  hint.style.opacity = "1";
  const onScroll = () => {
    const nearBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 40;
    hint.style.opacity = nearBottom ? "0" : "1";
  };
  box.scrollTop = 0;
  box.removeEventListener("scroll", onScroll); // clear any previous listener
  box.addEventListener("scroll", onScroll);
}

function closeModal() {
  hide("interest-modal");
}

async function handleFormSubmit(e) {
  e.preventDefault();
  hide("modal-error");

  const legal_first_name     = document.getElementById("form-legal-first-name").value.trim();
  const preferred_first_name = document.getElementById("form-preferred-first-name").value.trim();
  const legal_middle_name    = document.getElementById("form-legal-middle-name").value.trim();
  const legal_last_name      = document.getElementById("form-legal-last-name").value.trim();
  const street_address       = document.getElementById("form-street-address").value.trim();
  const city                 = document.getElementById("form-city").value.trim();
  const state                = document.getElementById("form-state").value.trim();
  const zip_code             = document.getElementById("form-zip").value.trim();
  const email                = document.getElementById("form-email").value.trim();
  const phone                = document.getElementById("form-phone").value.trim();
  const birthdate            = document.getElementById("form-birthdate").value;
  const precinct_code        = document.getElementById("form-precinct").value;
  const leg_dist             = document.getElementById("form-leg-dist").value;
  const democratEl           = document.querySelector('input[name="is_democrat"]:checked');
  const is_democrat          = democratEl ? democratEl.value === "yes" : null;

  if (!legal_first_name || !legal_last_name || !email || !phone ||
      !street_address || !city || !state || !zip_code || !birthdate || is_democrat === null) {
    showModalError("Please fill in all required fields.");
    return;
  }

  const submitBtn = document.getElementById("form-submit-btn");
  submitBtn.disabled    = true;
  submitBtn.textContent = "Submitting…";

  try {
    const resp = await fetch("/api/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        legal_first_name, preferred_first_name, legal_middle_name, legal_last_name,
        street_address, city, state, zip_code,
        email, phone, birthdate, is_democrat,
        precinct_code, leg_dist,
      }),
    });
    const data = await resp.json();

    if (!resp.ok) {
      showModalError(data.error || "Submission failed. Please try again.");
      submitBtn.disabled    = false;
      submitBtn.textContent = "Submit";
      return;
    }

    document.getElementById("interest-form").classList.add("hidden");
    show("modal-success");
  } catch (_) {
    showModalError("An unexpected error occurred. Please try again.");
    submitBtn.disabled    = false;
    submitBtn.textContent = "Submit";
  }
}

function showModalError(msg) {
  const el = document.getElementById("modal-error");
  el.textContent = msg;
  show("modal-error");
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

function clearError() { hide("error-box"); }

function showError(msg) {
  document.getElementById("error-msg").textContent = msg;
  show("error-box");
}

function show(id)          { document.getElementById(id).classList.remove("hidden"); }
function hide(id)          { document.getElementById(id).classList.add("hidden"); }
function toggle(id, on)    { on ? show(id) : hide(id); }

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", init);
