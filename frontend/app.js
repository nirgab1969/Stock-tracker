const API = "/api";

function banner(msg, kind = "info", timeout = 4000) {
  const el = document.getElementById("statusBanner");
  el.textContent = msg;
  el.className = `banner ${kind}`;
  el.classList.remove("hidden");
  if (timeout) setTimeout(() => el.classList.add("hidden"), timeout);
}

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = atob(base64);
  return Uint8Array.from([...rawData].map((c) => c.charCodeAt(0)));
}

// ---------------- Service worker + push ----------------

let swRegistration = null;

async function initServiceWorker() {
  if (!("serviceWorker" in navigator)) {
    document.getElementById("pushStatus").textContent = "הדפדפן הזה לא תומך בהתראות (נסה Chrome/Edge/Safari עדכני)";
    return;
  }
  swRegistration = await navigator.serviceWorker.register("/service-worker.js");
  await refreshPushStatus();
}

async function refreshPushStatus() {
  const statusEl = document.getElementById("pushStatus");
  const btn = document.getElementById("btnNotify");
  if (!swRegistration || !("PushManager" in window)) {
    statusEl.textContent = "לא נתמך בדפדפן זה";
    return;
  }
  const sub = await swRegistration.pushManager.getSubscription();
  if (sub && Notification.permission === "granted") {
    statusEl.textContent = "פעילות ✅";
    btn.classList.add("active");
  } else {
    statusEl.textContent = "לא פעילות - לחץ על הפעמון להפעלה";
    btn.classList.remove("active");
  }
}

async function togglePush() {
  if (!swRegistration) return banner("שירות ה-Service Worker עדיין לא מוכן, נסה שוב בעוד רגע", "error");

  const existing = await swRegistration.pushManager.getSubscription();
  if (existing) {
    await existing.unsubscribe();
    await fetch(`${API}/push/unsubscribe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ endpoint: existing.endpoint }),
    });
    banner("התראות כובו", "info");
    return refreshPushStatus();
  }

  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    banner("לא ניתנה הרשאה להתראות בדפדפן", "error");
    return;
  }

  const { publicKey } = await (await fetch(`${API}/push/vapid-public-key`)).json();
  const subscription = await swRegistration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(publicKey),
  });

  const subJson = subscription.toJSON();
  await fetch(`${API}/push/subscribe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(subJson),
  });

  banner("התראות הופעלו בהצלחה!", "success");
  await refreshPushStatus();
}

async function sendTestPush() {
  const res = await (await fetch(`${API}/push/test`, { method: "POST" })).json();
  if (res.total_subscriptions === 0) {
    banner("אין עדיין מכשירים רשומים להתראות - הפעל התראות דרך הפעמון קודם", "error");
  } else {
    banner(`נשלחה התראת בדיקה ל-${res.sent} מכשיר/ים`, "success");
  }
}

// ---------------- Watchlist + dashboard ----------------

function scoreClass(score) {
  if (score === null || score === undefined) return "score-none";
  if (score >= 72) return "score-high";
  if (score >= 55) return "score-mid";
  return "score-low";
}

function buildBadges(scan) {
  const badges = [];
  if (scan.error) {
    badges.push(`<span class="badge badge-error">שגיאת נתונים</span>`);
    return badges.join("");
  }
  const signals = scan.signals || {};
  if (signals.entry_signal) badges.push(`<span class="badge badge-entry">🟢 איתות כניסה</span>`);
  if (signals.exit_signal) {
    const cls = signals.exit_type === "take_profit" ? "badge-exit-profit" : "badge-exit-loss";
    const label = signals.exit_type === "take_profit" ? "🟡 שווה לשקול מימוש" : "🔴 איתות יציאה/סטופ";
    badges.push(`<span class="badge ${cls}">${label}</span>`);
  }
  return badges.join("");
}

function renderCard(item) {
  const tpl = document.getElementById("cardTemplate");
  const node = tpl.content.cloneNode(true);
  const scan = item.scan || {};

  node.querySelector(".stock-symbol").textContent = item.symbol;
  node.querySelector(".stock-name").textContent = scan.display_name || item.display_name || "";

  const scoreEl = node.querySelector(".score-badge");
  const score = scan.composite_score;
  if (score === null || score === undefined) {
    scoreEl.textContent = scan.error ? "שגיאה" : "מחשב...";
  } else {
    scoreEl.textContent = Math.round(score);
  }
  scoreEl.className = `score-badge ${scoreClass(score)}`;

  node.querySelector(".signal-badges").innerHTML = buildBadges(scan);

  const sub = node.querySelector(".sub-scores");
  const t = scan.technical_score, f = scan.fundamental_score, s = scan.sentiment_score;
  sub.innerHTML = `
    <span>טכני: <b>${t !== undefined && t !== null ? Math.round(t) : "—"}</b></span>
    <span>פונדמנטלי: <b>${f !== undefined && f !== null ? Math.round(f) : "—"}</b></span>
    <span>סנטימנט: <b>${s !== undefined && s !== null ? Math.round(s) : "—"}</b></span>
  `;

  const reasonsEl = node.querySelector(".reasons");
  const reasons = [
    ...((scan.signals && scan.signals.entry_reasons) || []),
    ...((scan.signals && scan.signals.exit_reasons) || []),
    ...((scan.technical_reasons || []).slice(0, 2)),
  ].slice(0, 4);
  reasonsEl.innerHTML = reasons.map((r) => `<li>${r}</li>`).join("") ||
    (scan.error ? `<li>${scan.error}</li>` : "<li>אין עדיין נתונים - בצע סריקה</li>");

  const posEl = node.querySelector(".position-info");
  if (item.in_position) {
    posEl.classList.remove("hidden");
    const pnl = scan.signals ? scan.signals.pnl_pct : null;
    const pnlHtml = pnl !== null && pnl !== undefined
      ? `<span class="${pnl >= 0 ? "pnl-pos" : "pnl-neg"}">${pnl >= 0 ? "+" : ""}${pnl}%</span>`
      : "—";
    posEl.innerHTML = `מוחזק | כניסה: ${item.entry_price ?? "—"} | תשואה: ${pnlHtml}`;
  }

  node.querySelector(".updated-at").textContent = scan._updated_at
    ? `עודכן: ${new Date(scan._updated_at + "Z").toLocaleString("he-IL")}`
    : "עדיין לא נסרק";

  node.querySelector(".btn-remove").addEventListener("click", async () => {
    if (!confirm(`להסיר את ${item.symbol} מהמעקב?`)) return;
    await fetch(`${API}/watchlist/${item.symbol}`, { method: "DELETE" });
    loadDashboard();
  });

  node.querySelector(".btn-edit-position").addEventListener("click", async () => {
    const inPos = confirm("האם אתה מחזיק בפועל את המניה הזו כרגע? (אישור=כן, ביטול=לא)");
    let entryPrice = null, shares = null;
    if (inPos) {
      entryPrice = parseFloat(prompt("מחיר כניסה?", item.entry_price ?? "") || "");
      shares = parseFloat(prompt("כמות מניות?", item.shares ?? "") || "");
    }
    await fetch(`${API}/watchlist/${item.symbol}/position`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ in_position: inPos, entry_price: entryPrice || null, shares: shares || null }),
    });
    loadDashboard();
  });

  return node;
}

async function loadDashboard() {
  const res = await (await fetch(`${API}/dashboard`)).json();
  const grid = document.getElementById("watchlistGrid");
  grid.innerHTML = "";
  document.getElementById("emptyState").classList.toggle("hidden", res.items.length > 0);
  res.items.forEach((item) => grid.appendChild(renderCard(item)));

  const lastScan = res.last_scan;
  document.getElementById("lastScanInfo").textContent = lastScan && lastScan.finished_at
    ? `סריקה אחרונה: ${new Date(lastScan.finished_at + "Z").toLocaleString("he-IL")}`
    : "עדיין לא בוצעה סריקה";
}

async function loadAlerts() {
  const alerts = await (await fetch(`${API}/alerts?limit=30`)).json();
  const list = document.getElementById("alertsList");
  list.innerHTML = alerts.length
    ? alerts.map((a) => `
        <li>
          <strong>${a.symbol}</strong> — ${a.alert_type} — ${a.message}
          <span class="alert-time">${new Date(a.sent_at + "Z").toLocaleString("he-IL")}</span>
        </li>`).join("")
    : `<li class="muted">אין עדיין התראות</li>`;
}

// ---------------- Discoveries (broad-market scan) ----------------

function renderDiscoveryCard(item) {
  const tpl = document.getElementById("discoveryCardTemplate");
  const node = tpl.content.cloneNode(true);
  const scan = item.scan || {};

  node.querySelector(".stock-symbol").textContent = item.symbol;
  node.querySelector(".stock-name").textContent = item.display_name || "";

  const scoreEl = node.querySelector(".score-badge");
  scoreEl.textContent = Math.round(item.composite_score);
  scoreEl.className = `score-badge ${scoreClass(item.composite_score)}`;

  const reasonsEl = node.querySelector(".reasons");
  const reasons = (scan.technical_reasons || []).slice(0, 4);
  reasonsEl.innerHTML = reasons.map((r) => `<li>${r}</li>`).join("") || "<li>ניקוד גבוה בניתוח המשוקלל</li>";

  node.querySelector(".updated-at").textContent = item.discovered_at
    ? `התגלה: ${new Date(item.discovered_at + "Z").toLocaleString("he-IL")}`
    : "";

  node.querySelector(".btn-promote").addEventListener("click", async () => {
    await fetch(`${API}/discoveries/${item.symbol}/promote`, { method: "POST" });
    banner(`${item.symbol} נוסף לרשימת המעקב שלך`, "success");
    loadDiscoveries();
    loadDashboard();
  });

  node.querySelector(".btn-dismiss").addEventListener("click", async () => {
    await fetch(`${API}/discoveries/${item.symbol}/dismiss`, { method: "POST" });
    loadDiscoveries();
  });

  return node;
}

async function loadDiscoveries() {
  const res = await (await fetch(`${API}/discoveries`)).json();
  const grid = document.getElementById("discoveriesGrid");
  grid.innerHTML = "";
  document.getElementById("discoveriesEmptyState").classList.toggle("hidden", res.items.length > 0);
  res.items.forEach((item) => grid.appendChild(renderDiscoveryCard(item)));
}

document.getElementById("btnDiscoveryScan").addEventListener("click", async () => {
  banner("סורק את כל השוק (S&P 500 + ת\"א 125)... זה יכול לקחת כמה דקות.", "info", 10000);
  try {
    const res = await (await fetch(`${API}/discovery-scan`, { method: "POST" })).json();
    banner(`הסריקה הסתיימה - נמצאו ${res.discovered.length} מניות חדשות עם פוטנציאל`, "success");
  } catch (err) {
    banner("הסריקה נכשלה, נסה שוב", "error");
  }
  loadDiscoveries();
});

// ---------------- Add form ----------------

document.getElementById("fInPosition").addEventListener("change", (e) => {
  document.getElementById("positionFields").style.display = e.target.checked ? "flex" : "none";
});

document.getElementById("fSymbol").addEventListener("input", (e) => {
  const marketSel = document.getElementById("fMarket");
  marketSel.value = e.target.value.toUpperCase().endsWith(".TA") ? "TASE" : "US";
});

document.getElementById("addForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const symbol = document.getElementById("fSymbol").value.trim().toUpperCase();
  if (!symbol) return;
  const inPosition = document.getElementById("fInPosition").checked;

  await fetch(`${API}/watchlist`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      symbol,
      display_name: document.getElementById("fName").value.trim() || symbol,
      market: document.getElementById("fMarket").value,
      in_position: inPosition,
      entry_price: inPosition ? parseFloat(document.getElementById("fEntryPrice").value) || null : null,
      shares: inPosition ? parseFloat(document.getElementById("fShares").value) || null : null,
    }),
  });

  e.target.reset();
  document.getElementById("positionFields").style.display = "none";
  banner(`${symbol} נוסף למעקב. הרץ סריקה כדי לקבל ניתוח.`, "success");
  loadDashboard();
});

document.getElementById("btnScanNow").addEventListener("click", async () => {
  banner("סורק את רשימת המעקב... זה יכול לקחת כמה עשרות שניות.", "info", 8000);
  try {
    await fetch(`${API}/scan`, { method: "POST" });
    banner("הסריקה הסתיימה", "success");
  } catch (err) {
    banner("הסריקה נכשלה, נסה שוב", "error");
  }
  loadDashboard();
  loadAlerts();
});

document.getElementById("btnNotify").addEventListener("click", togglePush);
document.getElementById("btnTestPush").addEventListener("click", sendTestPush);
document.getElementById("btnRefreshAlerts").addEventListener("click", loadAlerts);

// ---------------- Init ----------------

initServiceWorker();
loadDashboard();
loadAlerts();
loadDiscoveries();
setInterval(loadDashboard, 60000);
setInterval(loadDiscoveries, 120000);
