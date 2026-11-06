const platformCaps = { x: 30, linkedin: 20, telegram: 100 };
const platformLabels = { x: "X Bookmarks", linkedin: "LinkedIn Saved Posts", telegram: "Telegram Saved Messages" };
let selected = "x";
let activeJob = null;
let pollTimer = null;

const $ = (selector) => document.querySelector(selector);
const countInput = $("#count");
const riskRow = $("#risk-row");
const riskInput = $("#risk");
const runButton = $("#run");

function clampCount() {
  const cap = platformCaps[selected];
  const value = Math.max(1, Math.min(cap, Number.parseInt(countInput.value || "10", 10)));
  countInput.max = String(cap);
  countInput.value = String(value);
  $("#count-help").textContent = `${platformLabels[selected]} allows 1–${cap} items per manual run.`;
}

function selectPlatform(platform) {
  selected = platform;
  document.querySelectorAll(".platform[data-platform]").forEach((button) => {
    const active = button.dataset.platform === platform;
    button.classList.toggle("active", active);
    button.setAttribute("aria-checked", String(active));
  });
  riskRow.hidden = platform === "telegram";
  riskInput.checked = false;
  $("#browser-note").textContent = platform === "linkedin"
    ? "Uses the LinkedIn tab already signed in to your normal Chrome profile."
    : platform === "x"
      ? "Uses the private X credential file already configured on this Mac."
      : "Uses the existing local Telegram session.";
  clampCount();
}

document.querySelectorAll(".platform[data-platform]").forEach((button) => {
  button.addEventListener("click", () => selectPlatform(button.dataset.platform));
});
$("#minus").addEventListener("click", () => { countInput.value = String(Number(countInput.value) - 1); clampCount(); });
$("#plus").addEventListener("click", () => { countInput.value = String(Number(countInput.value) + 1); clampCount(); });
countInput.addEventListener("change", clampCount);

function setActivity(job) {
  activeJob = job;
  $("#job-title").textContent = `${platformLabels[job.platform]} · ${job.count} max`;
  const status = $("#status");
  status.textContent = job.status.replace("_", " ").toUpperCase();
  status.className = `status ${job.status}`;
  $("#phase").textContent = job.error || job.phase;
  $("#percent").textContent = `${job.progress}%`;
  $("#progress").style.width = `${job.progress}%`;
  const terminal = $("#terminal");
  terminal.innerHTML = (job.logs.length ? job.logs : [job.phase]).map((line, index) => {
    const minute = new Date(Date.now() - (job.logs.length - index - 1) * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    return `<div><time>${minute}</time><span>${escapeHtml(line)}</span></div>`;
  }).join("");
  terminal.scrollTop = terminal.scrollHeight;
  $("#extension-hint").hidden = job.platform !== "linkedin" || !["waiting_browser", "running"].includes(job.status);
  if (job.status === "complete" && job.result) {
    $("#results").hidden = false;
    for (const key of ["created", "updated", "skipped", "pending"]) {
      $(`#${key}`).textContent = job.result[key] ?? 0;
    }
    runButton.disabled = false;
    runButton.querySelector("span").textContent = "Run another sync";
    clearInterval(pollTimer);
  } else if (job.status === "failed") {
    runButton.disabled = false;
    runButton.querySelector("span").textContent = "Try again";
    clearInterval(pollTimer);
  }
}

function escapeHtml(value) {
  const element = document.createElement("span");
  element.textContent = value;
  return element.innerHTML;
}

async function pollJob(id) {
  try {
    const response = await fetch(`/api/jobs/${id}`, { cache: "no-store" });
    if (!response.ok) throw new Error("Could not read job status");
    setActivity(await response.json());
  } catch (error) {
    $("#phase").textContent = error.message;
  }
}

runButton.addEventListener("click", async () => {
  clampCount();
  if (selected !== "telegram" && !riskInput.checked) {
    riskRow.animate([{ transform: "translateX(-4px)" }, { transform: "translateX(4px)" }, { transform: "translateX(0)" }], { duration: 260 });
    return;
  }
  runButton.disabled = true;
  runButton.querySelector("span").textContent = "Starting…";
  $("#results").hidden = true;
  let socialTab = null;
  if (selected === "linkedin") socialTab = window.open("about:blank", "socials-linkedin-capture");
  try {
    const response = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        platform: selected,
        count: Number(countInput.value),
        acknowledge_risk: selected === "telegram" || riskInput.checked,
      }),
    });
    const job = await response.json();
    if (!response.ok) throw new Error(job.error || "Could not start job");
    setActivity(job);
    if (socialTab && job.target_url) socialTab.location.href = job.target_url;
    clearInterval(pollTimer);
    pollTimer = setInterval(() => pollJob(job.id), 800);
    runButton.querySelector("span").textContent = "Sync in progress";
  } catch (error) {
    if (socialTab) socialTab.close();
    runButton.disabled = false;
    runButton.querySelector("span").textContent = "Try again";
    setActivity({
      platform: selected,
      count: Number(countInput.value),
      status: "failed",
      progress: 0,
      phase: "Failed",
      error: error.message,
      logs: [error.message],
    });
  }
});

selectPlatform("x");
fetch("/api/status").catch(() => {
  $("#phase").textContent = "Local bridge is not reachable. Reopen the AiFa Social app.";
});
