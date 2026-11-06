(() => {
  "use strict";

  if (window.__socialsObsidianCaptureStarted) return;
  const params = new URLSearchParams(window.location.hash.slice(1));
  const jobId = params.get("socials-obsidian-job");
  const requestedCount = Number.parseInt(params.get("count") || "10", 10);
  if (!/^[a-f0-9]{32}$/.test(jobId || "")) return;

  window.__socialsObsidianCaptureStarted = true;
  const count = Math.max(1, Math.min(20, requestedCount));
  const endpoint = `http://127.0.0.1:8766/api/jobs/${jobId}`;
  const scrollTools = globalThis.SocialSavesLinkedInScroll;
  if (!scrollTools) return;
  const cardSelector = [
    "div[data-chameleon-result-urn]",
    "li.reusable-search__result-container",
    ".entity-result",
  ].join(", ");

  const overlay = document.createElement("div");
  overlay.id = "socials-obsidian-capture-status";
  Object.assign(overlay.style, {
    position: "fixed",
    zIndex: "2147483647",
    right: "22px",
    top: "22px",
    width: "310px",
    padding: "16px 17px",
    border: "1px solid rgba(255,255,255,.16)",
    borderRadius: "14px",
    background: "rgba(17,16,24,.96)",
    boxShadow: "0 20px 50px rgba(0,0,0,.35)",
    color: "#f7f4ff",
    font: "13px/1.45 -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif",
  });
  overlay.innerHTML = `
    <div style="font-size:10px;letter-spacing:.14em;color:#a996ff;font-weight:800">SOCIAL SAVES</div>
    <div id="socials-capture-phase" style="margin-top:7px;font-weight:700">Preparing capture…</div>
    <div style="height:5px;background:rgba(255,255,255,.1);border-radius:99px;margin-top:12px;overflow:hidden">
      <div id="socials-capture-bar" style="width:4%;height:100%;background:linear-gradient(90deg,#744cff,#ae91ff);transition:width .3s"></div>
    </div>`;
  document.documentElement.appendChild(overlay);

  const phaseElement = overlay.querySelector("#socials-capture-phase");
  const barElement = overlay.querySelector("#socials-capture-bar");

  function renderProgress(progress, phase) {
    phaseElement.textContent = phase;
    barElement.style.width = `${Math.max(4, Math.min(100, progress))}%`;
  }

  async function report(progress, phase) {
    renderProgress(progress, phase);
    const response = await fetch(`${endpoint}/progress`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ progress, phase }),
    });
    if (!response.ok) throw new Error("The local Control Center rejected progress");
  }

  function firstText(node, selectors) {
    for (const selector of selectors) {
      const value = node.querySelector(selector)?.innerText?.trim();
      if (value) return value;
    }
    return "";
  }

  function extractCard(node) {
    const author = firstText(node, [
      ".entity-result__title-text span[aria-hidden='true']",
      "a[href*='/in/'] span[aria-hidden='true']",
      "span[dir='ltr'] span[aria-hidden='true']",
    ]);
    const body = firstText(node, [
      "p.entity-result__content-summary",
      ".entity-result__content-summary",
      ".entity-result__summary",
    ]).replace(/(?:…|\.\.\.)see more\s*$/i, "").trim();
    const link = node.querySelector("a[href*='/feed/update/'], a[href*='/posts/']");
    return {
      urn: node.getAttribute("data-chameleon-result-urn") || "",
      author,
      body,
      url: link?.href || "",
    };
  }

  async function waitForCards(timeoutMs = 45000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const cards = document.querySelectorAll(cardSelector);
      if (cards.length) return cards;
      await new Promise((resolve) => setTimeout(resolve, 900));
    }
    throw new Error("No Saved Posts appeared on the LinkedIn page");
  }

  function itemKey(item) {
    return item.urn || item.url || `${item.author}|${item.body}`;
  }

  function collectItems(cards, items, seen) {
    for (const card of Array.from(cards)) {
      const item = extractCard(card);
      const key = itemKey(item);
      if (!key || seen.has(key)) continue;
      seen.add(key);
      items.push(item);
      if (items.length >= count) break;
    }
  }

  async function waitForNewItems(previousTotal, items, seen, timeoutMs = 6500) {
    const deadline = Date.now() + timeoutMs;
    let cards = document.querySelectorAll(cardSelector);
    while (Date.now() < deadline) {
      cards = document.querySelectorAll(cardSelector);
      collectItems(cards, items, seen);
      if (items.length > previousTotal) return cards;
      await new Promise((resolve) => setTimeout(resolve, 700));
    }
    return cards;
  }

  async function followJobToCompletion(timeoutMs = 600000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const response = await fetch(endpoint, { cache: "no-store" });
      if (!response.ok) throw new Error("Could not read local import progress");
      const job = await response.json();
      if (job.status === "complete") {
        const result = job.result || {};
        const details = [
          `${result.created || 0} created`,
          `${result.updated || 0} updated`,
          `${result.skipped || 0} skipped`,
          `${result.pending || 0} pending`,
        ].join(" · ");
        renderProgress(100, `Complete · ${details}`);
        overlay.style.borderColor = "rgba(111,255,177,.55)";
        return;
      }
      if (job.status === "failed") {
        throw new Error(job.error || "Local import failed");
      }
      renderProgress(Math.max(68, job.progress || 0), job.phase || "Importing locally…");
      await new Promise((resolve) => setTimeout(resolve, 800));
    }
    throw new Error("Local import did not finish within ten minutes");
  }

  async function capture() {
    await report(10, "LinkedIn page connected");
    let cards = await waitForCards();
    const items = [];
    const seen = new Set();
    collectItems(cards, items, seen);
    let unchanged = 0;
    for (let scroll = 0; scroll < 3 && items.length < count; scroll += 1) {
      await report(
        18 + scroll * 11,
        `Loading Saved Posts · ${items.length}/${count} · pass ${scroll + 1}/3`,
      );
      const before = items.length;
      scrollTools.advanceResults(cards[cards.length - 1], document, window);
      cards = await waitForNewItems(before, items, seen);
      unchanged = items.length <= before ? unchanged + 1 : 0;
      if (unchanged >= 2) break;
    }

    await report(58, `Captured ${items.length}/${count} Saved Posts; sending locally`);
    const response = await fetch(`${endpoint}/results`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items }),
    });
    if (!response.ok) throw new Error("The local Control Center rejected the capture");
    renderProgress(68, `Sent ${items.length} items. Local enrichment is running…`);
    await followJobToCompletion();
  }

  capture().catch((error) => {
    renderProgress(100, `Capture stopped: ${error.message}`);
    overlay.style.borderColor = "rgba(255,111,125,.55)";
    fetch(`${endpoint}/progress`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ progress: 65, phase: `Capture error: ${error.message}` }),
    }).catch(() => {});
  });
})();
