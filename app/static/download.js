const startBtn = document.getElementById("start-btn");
const startedAtEl = document.getElementById("started-at");
const stateEl = document.getElementById("state");
const inCatalogEl = document.getElementById("in-catalog");
const namesSeenEl = document.getElementById("names-seen");
const downloadedEl = document.getElementById("downloaded");
const failedEl = document.getElementById("failed");
const lastBatchAtEl = document.getElementById("last-batch-at");
const elapsedEl = document.getElementById("elapsed");
const finishedAtEl = document.getElementById("finished-at");
const blockedNoteEl = document.getElementById("blocked-note");
const errorNoteEl = document.getElementById("error-note");

const nskFormatter = new Intl.DateTimeFormat("ru-RU", {
  timeZone: "Asia/Novosibirsk",
  dateStyle: "medium",
  timeStyle: "medium",
});

function formatTime(utc) {
  return utc ? nskFormatter.format(new Date(utc)) : "-";
}

function formatDuration(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const hh = String(Math.floor(s / 3600)).padStart(2, "0");
  const mm = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

let lastStatus = null;

function renderStatus(status) {
  lastStatus = status;
  stateEl.textContent = status.state;
  inCatalogEl.textContent = status.in_catalog;
  namesSeenEl.textContent = status.names_seen;
  downloadedEl.textContent = `${status.downloaded} из ${status.names_seen}`;
  failedEl.textContent = status.failed.length;
  failedEl.title = status.failed.join("\n");
  startedAtEl.textContent = formatTime(status.started_at);
  lastBatchAtEl.textContent = formatTime(status.last_batch_at);
  finishedAtEl.textContent = formatTime(status.finished_at);

  blockedNoteEl.hidden = !status.blocked_until;
  if (status.blocked_until) {
    blockedNoteEl.textContent = `Достигнут лимит запросов, ждём до ${formatTime(status.blocked_until)}`;
  }

  errorNoteEl.hidden = !status.error;
  if (status.error) {
    errorNoteEl.textContent = status.error;
  }

  startBtn.disabled = status.state === "running";
  renderElapsed();
}

function renderElapsed() {
  if (!lastStatus || !lastStatus.started_at) {
    elapsedEl.textContent = "-";
    return;
  }
  const start = new Date(lastStatus.started_at).getTime();
  const end = lastStatus.finished_at ? new Date(lastStatus.finished_at).getTime() : Date.now();
  elapsedEl.textContent = formatDuration((end - start) / 1000);
}

async function fetchStatus() {
  const response = await fetch("/api/download/status");
  const status = await response.json();
  renderStatus(status);
  return status;
}

let pollTimer = null;
let tickTimer = null;

function startPolling() {
  if (!tickTimer) tickTimer = setInterval(renderElapsed, 1000);
  if (pollTimer) return;
  pollTimer = setInterval(async () => {
    const status = await fetchStatus();
    if (status.state !== "running") {
      clearInterval(pollTimer);
      pollTimer = null;
      clearInterval(tickTimer);
      tickTimer = null;
      renderElapsed();
    }
  }, 1000);
}

startBtn.addEventListener("click", async () => {
  const response = await fetch("/api/download/start", { method: "POST" });
  renderStatus(await response.json());
  startPolling();
});

fetchStatus().then((status) => {
  if (status.state === "running") startPolling();
});
