// t-minus-pi Client-Side Dashboard Controller

const CONFIG = {
  refreshIntervalMs: 10000,
  time24h: false,
};

// 1. Digital Clock Controller
function initClock() {
  const clockTimeEl = document.getElementById("clockTime");
  const clockDateEl = document.getElementById("clockDate");

  function updateClock() {
    const now = new Date();

    // Time
    const hours = now.getHours();
    const minutes = String(now.getMinutes()).padStart(2, "0");
    const seconds = String(now.getSeconds()).padStart(2, "0");

    let timeStr = "";
    if (CONFIG.time24h) {
      timeStr = `${String(hours).padStart(2, "0")}:${minutes}:${seconds}`;
    } else {
      const h12 = hours % 12 || 12;
      const ampm = hours >= 12 ? "PM" : "AM";
      timeStr = `${h12}:${minutes}:${seconds} ${ampm}`;
    }

    if (clockTimeEl) clockTimeEl.textContent = timeStr;

    // Date
    const options = { weekday: "short", month: "short", day: "numeric", year: "numeric" };
    if (clockDateEl) clockDateEl.textContent = now.toLocaleDateString("en-US", options);
  }

  updateClock();
  setInterval(updateClock, 500);
}

// 2. Data Fetcher & Renderer
async function fetchDepartures() {
  const statusDot = document.getElementById("statusDot");
  const statusText = document.getElementById("statusText");
  const lastUpdated = document.getElementById("lastUpdated");

  try {
    const res = await fetch("/api/departures");
    if (!res.ok) throw new Error(`HTTP error ${res.status}`);

    const data = await res.json();
    renderDashboard(data.cards);

    if (statusDot) {
      statusDot.className = "status-dot online";
    }
    if (statusText) {
      statusText.textContent = "Live";
    }
    if (lastUpdated) {
      const now = new Date();
      lastUpdated.textContent = `Last updated: ${now.toLocaleTimeString()}`;
    }
  } catch (err) {
    console.error("Failed to fetch departures:", err);
    if (statusDot) {
      statusDot.className = "status-dot offline";
    }
    if (statusText) {
      statusText.textContent = "Offline";
    }
  }
}

function renderDashboard(cards) {
  const grid = document.getElementById("transitGrid");
  const alertsContainer = document.getElementById("alertsContainer");
  if (!grid) return;

  // Render Alerts
  if (alertsContainer) {
    const allAlerts = [];
    cards.forEach((c) => {
      if (c.alerts && c.alerts.length > 0) {
        c.alerts.forEach((a) => {
          if (!allAlerts.some((existing) => existing.header === a.header)) {
            allAlerts.push(a);
          }
        });
      }
    });

    if (allAlerts.length > 0) {
      alertsContainer.innerHTML = allAlerts
        .map((a) => `<div class="alert-banner">${escapeHtml(a.header)}</div>`)
        .join("");
    } else {
      alertsContainer.innerHTML = "";
    }
  }

  // Render Transit Cards
  if (!cards || cards.length === 0) {
    grid.innerHTML = '<div class="loading-placeholder">No routes configured or active.</div>';
    return;
  }

  grid.innerHTML = cards.map((card) => renderCard(card)).join("");
}

function renderCard(card) {
  const pillClass = card.type === "bus" ? "route-pill bus" : "route-pill";
  const defaultBg = card.type === "commuter_rail" ? "#80276C" : card.type === "bus" ? "#FFC72C" : "#DA291C";

  let directionsHtml = "";
  if (card.directions && card.directions.length > 0) {
    directionsHtml = card.directions
      .map((dir) => {
        let departuresHtml = "";
        if (dir.departures && dir.departures.length > 0) {
          departuresHtml = dir.departures
            .map((dep) => {
              const isArr = dep.countdown === "ARR";
              const isBrd = dep.countdown === "BRD";
              const isScheduledOnly = card.type === "commuter_rail" && dep.is_predicted === false;
              const badgeClass = isArr
                ? "countdown-badge arriving"
                : isBrd
                ? "countdown-badge boarding"
                : "countdown-badge";
              const rowClass = isScheduledOnly ? "departure-row scheduled-only" : "departure-row";

              const busBadge =
                card.type === "bus" && dep.route_name
                  ? `<span class="departure-bus-badge">${escapeHtml(dep.route_name)}</span>`
                  : "";

              const trainNumber = dep.train_number
                ? `<span class="departure-train-no">#${escapeHtml(dep.train_number)}</span>`
                : "";

              const scheduleBadge = isScheduledOnly
                ? '<span class="departure-source-badge">Scheduled</span>'
                : "";

              const serviceDetail = dep.service_detail
                ? `<div class="departure-service-detail">${escapeHtml(dep.service_detail)}</div>`
                : "";
              const busMeta =
                card.type === "bus" && dep.stop_name && dep.direction_label
                  ? `${escapeHtml(dep.stop_name)}: ${escapeHtml(dep.direction_label)}`
                  : "";
              const busMetaHtml = busMeta
                ? `<div class="departure-meta">${busMeta}</div>`
                : "";

              return `
                <div class="${rowClass}">
                  <div class="departure-left">
                    ${busBadge}
                    <div class="departure-main">
                      <div class="departure-headsign">${escapeHtml(dep.headsign)} ${trainNumber} ${scheduleBadge}</div>
                      ${busMetaHtml}
                      ${serviceDetail}
                    </div>
                  </div>
                  <div class="departure-right">
                    <div class="departure-time-clock">${dep.time_formatted}</div>
                    <div class="${badgeClass}">${escapeHtml(dep.countdown)}</div>
                  </div>
                </div>
              `;
            })
            .join("");
        } else {
          departuresHtml = '<div class="no-departures">No upcoming departures</div>';
        }

        return `
          <div class="direction-section">
            <div class="direction-header">${escapeHtml(dir.name)}</div>
            <div class="departures-list">${departuresHtml}</div>
          </div>
        `;
      })
      .join("");
  } else {
    directionsHtml = '<div class="no-departures">No departure data available</div>';
  }

  return `
    <div class="route-card" id="card-${card.id}">
      <div class="card-header">
        <div class="route-badge-title">
          <span class="${pillClass}" style="background-color: ${defaultBg};">${escapeHtml(card.type.toUpperCase().replace("_", " "))}</span>
          <h2 class="card-title">${escapeHtml(card.name)}</h2>
        </div>
      </div>
      <div class="card-body">
        ${directionsHtml}
      </div>
    </div>
  `;
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// 3. Initialization
async function init() {
  initClock();

  // Load public config
  try {
    const res = await fetch("/api/config");
    if (res.ok) {
      const cfg = await res.json();
      if (cfg.refresh_seconds) {
        CONFIG.refreshIntervalMs = cfg.refresh_seconds * 1000;
      }
      if (cfg.clock_format_24h !== undefined) {
        CONFIG.time24h = cfg.clock_format_24h;
      }
    }
  } catch (e) {
    console.warn("Could not fetch remote config, using defaults:", e);
  }

  // Initial fetch and polling loop
  await fetchDepartures();
  async function pollDepartures() {
    await fetchDepartures();
    setTimeout(pollDepartures, CONFIG.refreshIntervalMs);
  }
  setTimeout(pollDepartures, CONFIG.refreshIntervalMs);
}

document.addEventListener("DOMContentLoaded", init);
