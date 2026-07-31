import {
  requireOfficialTeamIdentity,
} from "./official-team-colours.js?v=20260726-official-v1";

const state = {
  data: null,
  indexes: null,
  selectedDrivers: new Set(),
  search: "",
  lapFrom: null,
  lapTo: null,
  onlyRadioLaps: false,
  showUnmapped: true,
};

const $ = (id) => document.getElementById(id);
const els = {};
let booted = false;

document.addEventListener("DOMContentLoaded", boot);
if (document.readyState !== "loading") boot();

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

  const payload = await fetch("data/live-review/timeline.json").then((res) => {
    if (!res.ok) throw new Error(`timeline.json ${res.status}`);
    return res.json();
  });
  state.data = attachOfficialTeamIdentities(payload);
  state.selectedDrivers = new Set(state.data.drivers.map((driver) => driver.tla));
  state.lapFrom = state.data.stats.lap_min;
  state.lapTo = state.data.stats.lap_max;
  els.lapFrom.value = state.lapFrom ?? "";
  els.lapTo.value = state.lapTo ?? "";
  state.indexes = buildIndexes(state.data);

  bind();
  render();
}

function attachOfficialTeamIdentities(data) {
  const year = Number(data?.year);
  if (!Number.isInteger(year)) {
    throw new Error("Live Review 缺少颜色解析所需的赛季");
  }
  const drivers = (data.drivers || []).map((driver) => {
    const identity = requireOfficialTeamIdentity(year, driver.team);
    if (driver.team_key && driver.team_key !== identity.key) {
      throw new Error(
        `Live Review 车队身份冲突：${driver.tla} · ${driver.team_key} != ${identity.key}`,
      );
    }
    return {
      ...driver,
      team: identity.name,
      team_key: identity.key,
      team_colour: identity.colour,
    };
  });
  return { ...data, drivers };
}

function buildIndexes(data) {
  const lapRowsByLap = new Map();
  for (const row of data.lap_rows) {
    const bucket = getBucket(lapRowsByLap, row.race_lap);
    bucket.push(row);
  }
  for (const rows of lapRowsByLap.values()) {
    rows.sort((a, b) => a.position - b.position);
  }

  const radios = data.radios.map((row) => ({
    ...row,
    search_blob: [row.driver, row.driver_tla, row.message, row.timestamp_text]
      .join(" ")
      .toLowerCase(),
  }));
  const radioRowsByLap = new Map();
  const radioCountByDriver = new Map();
  const unmappedRadioRows = [];
  for (const row of radios) {
    if (row.driver_tla) {
      radioCountByDriver.set(row.driver_tla, (radioCountByDriver.get(row.driver_tla) ?? 0) + 1);
    }
    if (row.race_lap == null) {
      unmappedRadioRows.push(row);
    } else {
      getBucket(radioRowsByLap, row.race_lap).push(row);
    }
  }
  const byTime = (a, b) => (a.timestamp_ms ?? 0) - (b.timestamp_ms ?? 0);
  for (const rows of radioRowsByLap.values()) rows.sort(byTime);
  unmappedRadioRows.sort(byTime);

  return {
    lapRowsByLap,
    radioRowsByLap,
    radioCountByDriver,
    unmappedRadioRows,
    driverByTla: new Map(data.drivers.map((driver) => [driver.tla, driver])),
  };
}

function getBucket(map, key) {
  if (!map.has(key)) map.set(key, []);
  return map.get(key);
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
    setOfficialDriverColour(button, driver.tla);
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
  if (!laps.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No timeline rows";
    els.timeline.replaceChildren(empty);
    return;
  }

  const fragment = document.createDocumentFragment();
  let rendered = 0;
  for (const lap of laps) {
    const rows = timingRowsForLap(lap);
    const radios = radioRowsForLap(lap);
    if (state.onlyRadioLaps && !radios.length) continue;

    const section = document.createElement("article");
    section.className = `lapBlock ${radios.length ? "hasRadio" : ""}`;
    section.append(lapHeader(lap, rows, radios), lapBody(rows, radios));
    fragment.appendChild(section);
    rendered += 1;
  }

  if (state.showUnmapped) {
    const rows = unmappedRadios();
    if (rows.length) {
      fragment.appendChild(unmappedBlock(rows));
      rendered += 1;
    }
  }

  if (!rendered) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No timeline rows";
    fragment.appendChild(empty);
  }

  els.timeline.replaceChildren(fragment);
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
    setOfficialDriverColour(tr, row.driver);
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
  setOfficialDriverColour(card, radio.driver_tla);
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
  return (state.indexes.lapRowsByLap.get(lap) ?? [])
    .filter((row) => state.selectedDrivers.has(row.driver));
}

function radioRowsForLap(lap) {
  return (state.indexes.radioRowsByLap.get(lap) ?? [])
    .filter((row) => !row.driver_tla || state.selectedDrivers.has(row.driver_tla))
    .filter(matchesSearch);
}

function unmappedRadios() {
  return state.indexes.unmappedRadioRows
    .filter((row) => !row.driver_tla || state.selectedDrivers.has(row.driver_tla))
    .filter(matchesSearch);
}

function matchesSearch(row) {
  if (!state.search) return true;
  return row.search_blob.includes(state.search);
}

function radioCountFor(tla) {
  return state.indexes.radioCountByDriver.get(tla) ?? 0;
}

function setOfficialDriverColour(element, tla) {
  if (!tla) {
    element.dataset.colorSource = "unmapped";
    return;
  }
  const driver = state.indexes.driverByTla.get(tla);
  if (!driver?.team_colour || !driver?.team_key) {
    throw new Error(`Live Review 官方车队颜色未映射：${tla}`);
  }
  element.style.setProperty("--driver", driver.team_colour);
  element.dataset.teamKey = driver.team_key;
  element.dataset.colorSource = "official";
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
