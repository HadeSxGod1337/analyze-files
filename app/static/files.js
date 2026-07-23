const selectPageBtn = document.getElementById("select-page");
const selectAllBtn = document.getElementById("select-all");
const clearBtn = document.getElementById("clear-selection");
const calculateBtn = document.getElementById("calculate-btn");
const selectionCountEl = document.getElementById("selection-count");
const resultsEl = document.getElementById("results");
const totalTableEl = document.getElementById("total-table");
const perFileTableEl = document.getElementById("per-file-table");
const recalculateEl = document.getElementById("recalculate");
const anomaliesEl = document.getElementById("anomalies");
const anomaliesTableEl = document.getElementById("anomalies-table");
const errorsEl = document.getElementById("errors");
const errorsListEl = document.getElementById("errors-list");

let selectAllMode = false;

function checkboxes() {
  return Array.from(document.querySelectorAll(".file-checkbox"));
}

function updateSelectionCount() {
  if (selectAllMode) {
    selectionCountEl.textContent = "выбрано: все файлы";
    return;
  }
  const count = checkboxes().filter((cb) => cb.checked).length;
  selectionCountEl.textContent = count ? `выбрано: ${count}` : "";
}

selectPageBtn.addEventListener("click", () => {
  selectAllMode = false;
  checkboxes().forEach((cb) => (cb.checked = true));
  updateSelectionCount();
});

selectAllBtn.addEventListener("click", () => {
  selectAllMode = true;
  checkboxes().forEach((cb) => (cb.checked = true));
  updateSelectionCount();
});

clearBtn.addEventListener("click", () => {
  selectAllMode = false;
  checkboxes().forEach((cb) => (cb.checked = false));
  updateSelectionCount();
});

checkboxes().forEach((cb) =>
  cb.addEventListener("change", () => {
    selectAllMode = false;
    updateSelectionCount();
  }),
);

function renderCountsTable(table, counts) {
  const digits = Object.keys(counts).sort();
  table.innerHTML =
    `<tr>${digits.map((d) => `<th>${d}</th>`).join("")}</tr>` +
    `<tr>${digits.map((d) => `<td>${counts[d]}</td>`).join("")}</tr>`;
}

function renderPerFileTable(table, perFile) {
  const names = Object.keys(perFile);
  if (names.length === 0) {
    table.innerHTML = "";
    return;
  }
  const digits = Object.keys(perFile[names[0]]).sort();
  const header = `<tr><th>Файл</th>${digits.map((d) => `<th>${d}</th>`).join("")}</tr>`;
  const rows = names
    .map(
      (name) =>
        `<tr><td>${name}</td>${digits.map((d) => `<td>${perFile[name][d]}</td>`).join("")}</tr>`,
    )
    .join("");
  table.innerHTML = header + rows;
}

function renderAnomalies(anomalies) {
  anomaliesEl.hidden = anomalies.length === 0;
  if (anomalies.length === 0) return;
  const header = "<tr><th>Файл</th><th>Не-цифры</th><th>Длина</th></tr>";
  const rows = anomalies
    .map((a) => `<tr><td>${a.name}</td><td>${a.non_digit}</td><td>${a.length}</td></tr>`)
    .join("");
  anomaliesTableEl.innerHTML = header + rows;
}

function renderErrors(errors) {
  errorsEl.hidden = errors.length === 0;
  errorsListEl.innerHTML = errors.map((name) => `<li>${name}</li>`).join("");
}

calculateBtn.addEventListener("click", async () => {
  const body = selectAllMode
    ? { all: true }
    : { names: checkboxes().filter((cb) => cb.checked).map((cb) => cb.value) };
  body.recalculate = recalculateEl.checked;

  const response = await fetch("/api/files/calculate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json();

  renderCountsTable(totalTableEl, data.total);
  renderPerFileTable(perFileTableEl, data.per_file);
  renderAnomalies(data.anomalies);
  renderErrors(data.errors);
  resultsEl.hidden = false;
});

const nskFormatter = new Intl.DateTimeFormat("ru-RU", {
  timeZone: "Asia/Novosibirsk",
  dateStyle: "medium",
  timeStyle: "medium",
});

document.querySelectorAll(".downloaded-at").forEach((cell) => {
  if (cell.dataset.utc) {
    cell.textContent = nskFormatter.format(new Date(cell.dataset.utc));
  }
});

updateSelectionCount();
