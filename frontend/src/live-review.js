const state = {
  data: null,
  selectedDrivers: new Set(),
  search: "",
  lapFrom: null,
  lapTo: null,
  onlyRadioLaps: false,
  showUnmapped: true,
};

const $ = (id) => document.getElementById(id);
const els = {};

document.addEventListener("DOMContentLoaded", boot);
if (document.readyState !== "loading") boot();

let booted = false;
async function boot() {
  if (booted) return;
  booted = true;
  Object.assign(els, {
    meta: $("meta"),
    stats: $("stats"),
    driverCount: $("driverCount"),
    driverList: $("driverList"),
    allDrivers: $("allDrivers"),
    clearDrivers: $("clearDrivers"),
    search: $("search"),
    lapFrom: $("lapFrom"),
    lapTo: $("lapTo"),
    onlyRadioLaps: $("onlyRadioLaps"),
    showUnmapped: $("showUnmapped"),
    timeline: $("timeline"),
  });

  state.data = await fetch("data/live-review/timeline.json").then((res) => {
    if (!res.ok) throw new Error(`timeline.json ${res.status}`);
    return res.json();
  });
  state.selectedDrivers = new Set(state.data.drivers.map((driver) => driver.tla));
  state.lapFrom = state.data.stats.lap_min;
  state.lapTo = state.data.stats.lap_max;
  els.lapFrom.value = state.lapFrom ?? "";
  els.lapTo.value = state.lapTo ?? "";

  bind();
  render();
}

function bind() {
  els.allDrivers.addEventListener("click", () => {
    state.selectedDrivers = new Set(state.data.drivers.map((driver) => driver.tla));
    render();
  });
  els.clearDrivers.addEventListener("click", () => {
    state.selectedDrivers.clear();
    render();
  });
  els.search.addEventListener("input", () => {
    state.search = els.search.value.trim().toLowerCase();
    renderTimeline();
  });
  els.lapFrom.addEventListener("input", () => {
    state.lapFrom = numberOrNull(els.lapFrom.value);
    renderTimeline();
  });
  els.lapTo.addEventListener("input", () => {
    state.lapTo = numberOrNull(els.lapTo.value);
    renderTimeline();
  });
  els.onlyRadioLaps.addEventListener("change", () => {
    state.onlyRadioLaps = els.onlyRadioLaps.checked;
    renderTimeline();
  });
  els.showUnmapped.addEventListener("change", () => {
    state.showUnmapped = els.showUnmapped.checked;
    renderTimeline();
  });
}

function render() {
  const d = state.data;
  els.meta.textContent = `${d.title} · ${d.session} · ${d.quality_note}`;
  els.stats.replaceChildren(
    stat("Laps", `${d.stats.lap_min}-${d.stats.lap_max}`),
    stat("Rows", d.stats.lap_row_count),
    stat("Mapped TR", d.stats.mapped_radio_count),
    stat("Unmapped", d.stats.unmapped_radio_count),
  );
  renderDrivers();
  renderTimeline();
}

function renderDrivers() {
  els.driverCount.textContent = `${state.selectedDrivers.size}/${state.data.drivers.length}`;
  els.driverList.replaceChildren();
  for (const driver of state.data.drivers) {
    const on = state.selectedDrivers.has(driver.tla);
    const button = document.createElement("button");
    button.type = "button";
    button.className = `driver ${on ? "on" : "off"}`;
    button.style.setProperty("--driver", driver.color);
    button.innerHTML = `<span class="avatar"></span><span class="tla"></span><span class="count"></span>`;
    button.querySelector(".avatar").textContent = driver.tla;
    button.querySelector(".tla").textContent = driver.tla;
    button.querySelector(".count").textContent = radioCountFor(driver.tla);
    button.addEventListener("click", () => {
      if (state.selectedDrivers.has(driver.tla)) state.selectedDrivers.delete(driver.tla);
      else state.selectedDrivers.add(driver.tla);
      render();
    });
    els.driverList.appendChild(button);
  }
}

function renderTimeline() {
  const laps = visibleLaps();
  els.timeline.replaceChildren();
  if (!laps.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No timeline rows";
    els.timeline.appendChild(empty);
    return;
  }

  for (const lap of laps) {
    const rows = timingRowsForLap(lap);
    const radios = radioRowsForLap(lap);
    if (state.onlyRadioLaps && !radios.length) continue;

    const section = document.createElement("article");
    section.className = `lapBlock ${radios.length ? "hasRadio" : ""}`;
    section.append(lapHeader(lap, rows, radios), lapBody(rows, radios));
    els.timeline.appendChild(section);
  }

  if (state.showUnmapped) {
    const rows = unmappedRadios();
    if (rows.length) els.timeline.appendChild(unmappedBlock(rows));
  }
}

function lapHeader(lap, rows, radios) {
  const head = document.createElement("header");
  head.className = "lapHead";
  const leader = rows[0];
  head.innerHTML = `
    <div>
      <span class="lapTitle">Lap ${lap}</span>
      <span class="sub">${leader?.race_clock ?? ""}</span>
    </div>
    <div class="lapBadges">
      <span>${rows.length} cars</span>
      <span class="${radios.length ? "hot" : ""}">${radios.length} TR</span>
    </div>
  `;
  return head;
}

function lapBody(rows, radios) {
  const body = document.createElement("div");
  body.className = "lapBody";
  body.appendChild(timingTable(rows));
  body.appendChild(radioPanel(radios));
  return body;
}

function timingTable(rows) {
  const wrap = document.createElement("div");
  wrap.className = "timingWrap";
  const table = document.createElement("table");
  table.className = "timingTable";
  table.innerHTML = `
    <thead>
      <tr>
        <th class="num">P</th>
        <th>Driver</th>
        <th class="num">Last</th>
        <th class="num">S1</th>
        <th class="num">S2</th>
        <th class="num">S3</th>
        <th>Tyre</th>
      </tr>
    </thead>
    <tbody></tbody>
  `;
  const tbody = table.querySelector("tbody");
  for (const row of rows) {
    const tr = document.createElement("tr");
    tr.style.setProperty("--driver", colorOf(row.driver));
    tr.innerHTML = `
      <td class="num">${value(row.position)}</td>
      <td><span class="driverCell"><span class="bar"></span>${escapeHTML(row.driver)}</span></td>
      <td class="num strong">${value(row.last_lap)}</td>
      <td class="num">${value(row.s1)}</td>
      <td class="num">${value(row.s2)}</td>
      <td class="num">${value(row.s3)}</td>
      <td>${tyre(row.tyre_compound, row.tyre_age_laps)}</td>
    `;
    tbody.appendChild(tr);
  }
  wrap.appendChild(table);
  return wrap;
}

function radioPanel(radios) {
  const panel = document.createElement("aside");
  panel.className = "radioPanel";
  if (!radios.length) {
    const empty = document.createElement("div");
    empty.className = "radioEmpty";
    empty.textContent = "No TR";
    panel.appendChild(empty);
    return panel;
  }
  for (const radio of radios) {
    panel.appendChild(radioCard(radio));
  }
  return panel;
}

function radioCard(radio) {
  const card = document.createElement("div");
  card.className = "radioCard";
  card.style.setProperty("--driver", colorOf(radio.driver_tla));
  card.innerHTML = `
    <div class="radioTop">
      <span class="miniAvatar">${escapeHTML(radio.driver_tla ?? "?")}</span>
      <strong>${escapeHTML(radio.driver)}</strong>
      <span>${escapeHTML(radio.timestamp_text)}</span>
    </div>
    <p>${escapeHTML(radio.message)}</p>
  `;
  return card;
}

function unmappedBlock(rows) {
  const block = document.createElement("article");
  block.className = "lapBlock unmapped";
  const shown = rows.slice(0, 180);
  block.innerHTML = `
    <header class="lapHead">
      <div>
        <span class="lapTitle">Unmapped TR</span>
        <span class="sub">${shown.length}/${rows.length}</span>
      </div>
      <div class="lapBadges"><span>${rows.length} messages</span></div>
    </header>
  `;
  const list = document.createElement("div");
  list.className = "unmappedList";
  for (const radio of shown) list.appendChild(radioCard(radio));
  block.appendChild(list);
  return block;
}

function visibleLaps() {
  return state.data.laps.filter((lap) => {
    if (state.lapFrom != null && lap < state.lapFrom) return false;
    if (state.lapTo != null && lap > state.lapTo) return false;
    return true;
  });
}

function timingRowsForLap(lap) {
  return state.data.lap_rows
    .filter((row) => row.race_lap === lap && state.selectedDrivers.has(row.driver))
    .sort((a, b) => a.position - b.position);
}

function radioRowsForLap(lap) {
  return state.data.radios
    .filter((row) => row.race_lap === lap)
    .filter((row) => !row.driver_tla || state.selectedDrivers.has(row.driver_tla))
    .filter(matchesSearch)
    .sort((a, b) => (a.timestamp_ms ?? 0) - (b.timestamp_ms ?? 0));
}

function unmappedRadios() {
  return state.data.unmapped_radios
    .filter((row) => !row.driver_tla || state.selectedDrivers.has(row.driver_tla))
    .filter(matchesSearch)
    .sort((a, b) => (a.timestamp_ms ?? 0) - (b.timestamp_ms ?? 0));
}

function matchesSearch(row) {
  if (!state.search) return true;
  return [row.driver, row.driver_tla, row.message, row.timestamp_text]
    .join(" ")
    .toLowerCase()
    .includes(state.search);
}

function radioCountFor(tla) {
  return state.data.radios.filter((row) => row.driver_tla === tla).length;
}

function colorOf(tla) {
  return state.data.drivers.find((driver) => driver.tla === tla)?.color ?? "#7f8aa0";
}

function stat(label, text) {
  const node = document.createElement("div");
  node.className = "stat";
  node.innerHTML = `<span></span><b></b>`;
  node.querySelector("span").textContent = label;
  node.querySelector("b").textContent = text;
  return node;
}

function tyre(compound, age) {
  const c = String(compound || "?").toUpperCase();
  return `<span class="tyre ${compoundClass(c)}">${escapeHTML(c)}</span><span class="age">${value(age)}</span>`;
}

function compoundClass(c) {
  if (c === "S") return "soft";
  if (c === "M") return "medium";
  if (c === "H") return "hard";
  if (c === "I") return "inter";
  if (c === "W") return "wet";
  return "unknown";
}

function value(v) {
  return v == null || v === "" ? "—" : String(v);
}

function numberOrNull(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function escapeHTML(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
