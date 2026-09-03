/**
 * 화면 로직.
 *
 * 여기에는 `fetch` 가 없다. 실행 방식(브라우저 / 서버)은 backend.js 가 정하고,
 * 화면은 open / preview / convert / jsonSchema 네 가지만 부른다.
 */

import { pickBackend } from "./backend.js";

const $ = (id) => document.getElementById(id);
const LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("");

/** 타입 배지가 도는 순서. 원래 추론된 타입에 따라 후보가 달라진다. */
const CYCLES = {
  date: [{ type: "date" }, { type: "date", format: "%Y/%m/%d" }, { type: "text" }],
  code: [{ type: "code" }, { type: "integer" }, { type: "text" }],
  integer: [{ type: "integer" }, { type: "code" }, { type: "text" }],
  decimal: [{ type: "decimal" }, { type: "money" }, { type: "text" }],
  money: [{ type: "money" }, { type: "money", format: "string" }, { type: "text" }],
  text: [{ type: "text" }, { type: "code" }, { type: "money" }, { type: "date" }],
  boolean: [{ type: "boolean" }, { type: "text" }],
};

const TYPE_LABEL = {
  text: "문자열",
  code: "코드(문자열)",
  integer: "정수",
  decimal: "소수",
  money: "금액",
  date: "날짜",
  boolean: "참/거짓",
};

function typeLabel(field) {
  if (field.type === "date") {
    if (!field.format) return "날짜 ISO";
    return "날짜 " + field.format.replace(/%Y/g, "Y").replace(/%m/g, "M").replace(/%d/g, "D");
  }
  if (field.type === "money" && field.format === "string") return "금액 원문";
  return TYPE_LABEL[field.type] || field.type;
}

const state = {
  backend: null,
  fileId: null,
  origin: "",
  format: "",
  sheets: [],
  sheet: null,
  profile: null,
  payload: null,
  baseTypes: {},
  sel: { row: null, col: null },
  showJson: false,
};

const esc = (s) =>
  String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

/* ------------------------------------------------------------------ 상태 표시 */

function setBusy(on, text) {
  $("busy").hidden = !on;
  if (text !== undefined) $("busy").textContent = text;
  for (const id of ["btnConvert", "btnSchema", "btnOpen", "btnSample"]) {
    $(id).disabled = on || (id !== "btnOpen" && id !== "btnSample" && !state.fileId);
  }
}

function note(message, kind = "안내") {
  const el = document.createElement("div");
  el.className = "note" + (kind === "오류" ? " err" : "");
  el.innerHTML = `<span class="tag">${esc(kind)}</span><span>${esc(message)}</span>`;
  $("notes").prepend(el);
}

/* ------------------------------------------------------------------ 불러오기 */

async function loadWith(action) {
  setBusy(true, "여는 중…");
  $("notes").innerHTML = "";
  try {
    const data = await action();
    state.fileId = data.fileId;
    state.origin = data.origin;
    state.format = data.format;
    state.sheets = data.sheets;
    state.sheet = data.sheets.length ? data.sheets[0].name : null;
    state.profile = data.profile;
    state.baseTypes = {};
    for (const f of data.profile.fields) state.baseTypes[f.key] = f.type;
    state.sel = { row: null, col: null };
    applyPayload(data.preview);
    renderChrome();
  } catch (err) {
    note(err.message, "오류");
  } finally {
    setBusy(false, "계산 중…");
  }
}

async function refresh() {
  if (!state.fileId) return;
  setBusy(true, "계산 중…");
  try {
    applyPayload(
      await state.backend.preview({
        fileId: state.fileId,
        profile: state.profile,
        sheet: state.sheet,
      }),
    );
  } catch (err) {
    note(err.message, "오류");
  } finally {
    setBusy(false);
  }
}

/* ------------------------------------------------------------------ 렌더 */

function applyPayload(payload) {
  state.payload = payload;
  renderNotes();
  renderSrc();
  renderOut();
  renderJson();
  renderIssues();
  $("samplingLabel").innerHTML = `샘플 <b>${esc(payload.sampling.label)}</b>`;
  $("srcSub").textContent =
    payload.sheet +
    (payload.source.headerRow ? ` · 헤더 ${payload.source.headerRow}행` : " · 헤더 미검출");
  $("outSub").textContent = `${payload.output.columns.length}개 필드`;
  $("workspace").classList.toggle("plain", !$("optHighlight").checked);
  applySelection();
  updateStatus();
}

function renderChrome() {
  $("fmtIn").textContent = (state.format || "").toUpperCase() || "—";
  $("fileName").innerHTML = `<b>${esc(state.origin)}</b>`;

  const picker = $("sheetPicker");
  if (state.sheets.length > 1) {
    picker.innerHTML = state.sheets
      .map(
        (s) =>
          `<option value="${esc(s.name)}"${s.name === state.sheet ? " selected" : ""}>` +
          `${esc(s.name)} (${s.totalRows}행)</option>`,
      )
      .join("");
    picker.hidden = false;
  } else {
    picker.hidden = true;
  }
  $("btnConvert").disabled = false;
  $("btnSchema").disabled = false;

  const big = state.sheets.some((s) => s.totalRows > 30000);
  if (big && state.backend.kind === "browser") {
    note(
      "3만 행이 넘는 장부입니다 — 브라우저에서는 느릴 수 있습니다. " +
        "자주 다루신다면 사내 서버 설치나 명령줄 도구가 낫습니다.",
    );
  }
}

function renderNotes() {
  $("notes").innerHTML = (state.payload.notes || [])
    .map((n) => `<div class="note"><span class="tag">안내</span><span>${esc(n)}</span></div>`)
    .join("");
}

function renderSrc() {
  const { header, rows } = state.payload.source;
  if (!rows.length) {
    $("gridSrc").innerHTML = emptyState("표시할 행이 없습니다");
    return;
  }

  let h = '<thead><tr><th class="rownum"><div class="colletter">&nbsp;</div></th>';
  header.forEach((name, i) => {
    h +=
      `<th><div class="colletter">${LETTERS[i] || i + 1}</div><div class="colhead">` +
      `<span class="colname">${esc(name)}</span>` +
      `<span class="badges"><span class="typebtn static">원본</span></span></div></th>`;
  });
  h += "</tr></thead><tbody>";

  for (const row of rows) {
    h += `<tr class="${row.kind === "subtotal" ? "r-subtotal" : ""}" data-row="${row.sourceRow}">`;
    h += `<td class="rownum">${row.sourceRow}</td>`;
    header.forEach((_, i) => {
      const v = row.cells[i] == null ? "" : row.cells[i];
      h +=
        `<td class="cell txt" tabindex="0" data-r="${row.sourceRow}" data-src-col="${i}">` +
        `${esc(v)}</td>`;
    });
    h += "</tr>";
  }
  $("gridSrc").innerHTML = h + "</tbody>";
}

function renderOut() {
  const { columns, rows } = state.payload.output;
  if (!columns.length) {
    $("gridOut").innerHTML = emptyState("필드가 없습니다");
    return;
  }

  let h = '<thead><tr><th class="rownum"><div class="colletter">&nbsp;</div></th>';
  columns.forEach((col, i) => {
    const cycle = CYCLES[state.baseTypes[col.key]] || [];
    const fixed = cycle.length < 2;
    h +=
      `<th><div class="colletter">${i + 1}</div><div class="colhead">` +
      `<span class="colname mono">${esc(col.key)}</span><span class="badges">` +
      `<button class="typebtn${fixed ? " static" : ""}" type="button" data-typecol="${i}">` +
      `${esc(typeLabel(col))}</button>` +
      (col.mapped ? "" : '<span class="typebtn static unmapped">원본 열 없음</span>') +
      "</span></div></th>";
  });
  h += "</tr></thead><tbody>";

  let n = 0;
  for (const row of rows) {
    if (!row.included) {
      h +=
        `<tr class="r-gap" data-row="${row.sourceRow}"><td class="rownum">·</td>` +
        `<td colspan="${columns.length}"><span class="gapnote">` +
        `${esc(row.dropReason || "제외됨")} (원본 ${row.sourceRow}행)</span></td></tr>`;
      continue;
    }
    n++;
    h += `<tr data-row="${row.sourceRow}"><td class="rownum">${n}</td>`;
    row.cells.forEach((cell, i) => {
      const col = columns[i];
      let cls = "cell";
      if (["money", "integer", "decimal"].includes(col.type) && col.format !== "string") {
        cls += " num";
      } else if (col.type === "text") {
        cls += " txt";
      }
      if (cell.display === "") cls += " nul";
      if (cell.diff !== "unchanged") cls += " d-" + cell.diff;
      const text = cell.display === "" ? (cell.value === null ? "null" : "") : esc(cell.display);
      h += `<td class="${cls}" tabindex="0" data-r="${row.sourceRow}" data-c="${i}">${text}</td>`;
    });
    h += "</tr>";
  }
  $("gridOut").innerHTML = h + "</tbody>";
}

function renderJson() {
  const { columns, rows } = state.payload.output;
  const body = rows
    .filter((r) => r.included)
    .map((row) => {
      const lines = row.cells.map((cell, i) => {
        const v = cell.value;
        let cls, text;
        if (v === null) [cls, text] = ["p", "null"];
        else if (typeof v === "number" || typeof v === "boolean") [cls, text] = ["n", String(v)];
        else [cls, text] = ["s", JSON.stringify(String(v))];
        return (
          `    <span class="k">${JSON.stringify(columns[i].key)}</span>` +
          `<span class="p">:</span> <span class="${cls}">${esc(text)}</span>`
        );
      });
      return (
        `  <span class="obj" data-row="${row.sourceRow}">{\n` +
        lines.join('<span class="p">,</span>\n') +
        "\n  }</span>"
      );
    });
  $("jsonOut").innerHTML =
    '<span class="p">[</span>\n' + body.join('<span class="p">,</span>\n') + '\n<span class="p">]</span>';
}

function renderIssues() {
  const issues = state.payload.issues || [];
  $("issuesBox").hidden = issues.length === 0;
  $("issueCount").textContent = issues.length ? `· ${issues.length}건 (표본 내)` : "";
  $("issueList").innerHTML = issues
    .map(
      (i) =>
        `<li><span class="sev ${i.severity}">${esc(i.severity)}</span>` +
        `<span class="row">${i.sourceRow}행</span><span>${esc(i.message)}</span></li>`,
    )
    .join("");
}

const emptyState = (text) =>
  `<tbody><tr><td class="empty-state">${esc(text)}</td></tr></tbody>`;

/* ------------------------------------------------------------ 선택 / 상태바 */

function sourceColumnOf(outputIndex) {
  const col = state.payload.output.columns[outputIndex];
  return state.payload.source.header.indexOf(col.source);
}

function applySelection() {
  for (const el of document.querySelectorAll("tr.r-sel")) el.classList.remove("r-sel");
  for (const el of document.querySelectorAll(".cell.c-sel")) el.classList.remove("c-sel");
  for (const el of document.querySelectorAll("#jsonOut .obj.on")) el.classList.remove("on");
  if (state.sel.row === null) return;

  for (const el of document.querySelectorAll(`tr[data-row="${state.sel.row}"]`)) {
    el.classList.add("r-sel");
  }
  const obj = document.querySelector(`#jsonOut .obj[data-row="${state.sel.row}"]`);
  if (obj) obj.classList.add("on");
  if (state.sel.col === null) return;

  const out = document.querySelector(
    `#gridOut .cell[data-r="${state.sel.row}"][data-c="${state.sel.col}"]`,
  );
  if (out) out.classList.add("c-sel");

  const srcCol = sourceColumnOf(state.sel.col);
  if (srcCol >= 0) {
    const src = document.querySelector(
      `#gridSrc .cell[data-r="${state.sel.row}"][data-src-col="${srcCol}"]`,
    );
    if (src) src.classList.add("c-sel");
  }
}

function updateStatus() {
  const { sel, payload } = state;
  if (sel.row === null || sel.col === null) {
    $("stCell").textContent = "—";
    $("stBefore").textContent = "셀을 클릭하세요";
    $("stBefore").className = "st-val muted";
    $("stAfter").textContent = "—";
    $("stRules").innerHTML = '<span class="rule-pill">—</span>';
    return;
  }

  const row = payload.output.rows.find((r) => r.sourceRow === sel.row);
  if (!row) return;
  const cell = row.cells[sel.col];
  const col = payload.output.columns[sel.col];
  const srcCol = sourceColumnOf(sel.col);
  const srcRow = payload.source.rows.find((r) => r.sourceRow === sel.row);
  const raw = srcRow && srcCol >= 0 ? srcRow.cells[srcCol] : "";

  $("stCell").textContent =
    (srcCol >= 0 ? (LETTERS[srcCol] || srcCol + 1) + sel.row : sel.row + "행") + " → " + col.key;
  const empty = raw === "" || raw == null;
  $("stBefore").textContent = empty ? "(빈 셀)" : raw;
  $("stBefore").className = "st-val" + (empty ? " muted" : "");
  $("stAfter").textContent =
    cell.value === null
      ? "null"
      : typeof cell.value === "string"
        ? JSON.stringify(cell.value)
        : String(cell.value);

  let pills = cell.rules
    .map(
      (r, i) =>
        `<span class="rule-pill${cell.issues.length && i === cell.rules.length - 1 ? " warn" : ""}">` +
        `${esc(r)}</span>`,
    )
    .join('<span class="st-arrow">›</span>');
  pills += cell.issues.map((i) => `<span class="rule-pill warn">${esc(i.code)}</span>`).join("");
  $("stRules").innerHTML = pills || '<span class="rule-pill">변경 없음</span>';
}

/* ------------------------------------------------------------------ 조작 */

document.addEventListener("click", (e) => {
  const badge = e.target.closest("[data-typecol]");
  if (badge && !badge.classList.contains("static")) {
    cycleType(Number(badge.getAttribute("data-typecol")));
    return;
  }

  const cell = e.target.closest(".cell[data-r]");
  if (cell) {
    state.sel.row = Number(cell.getAttribute("data-r"));
    if (cell.hasAttribute("data-c")) {
      state.sel.col = Number(cell.getAttribute("data-c"));
    } else {
      // 원본 쪽을 클릭했으면 같은 원본 열을 쓰는 출력 필드를 찾는다.
      const name = state.payload.source.header[Number(cell.getAttribute("data-src-col"))];
      const idx = state.payload.output.columns.findIndex((c) => c.source === name);
      state.sel.col = idx >= 0 ? idx : null;
    }
    applySelection();
    updateStatus();
    return;
  }

  const obj = e.target.closest("#jsonOut .obj");
  if (obj) {
    state.sel.row = Number(obj.getAttribute("data-row"));
    applySelection();
  }
});

document.addEventListener("keydown", (e) => {
  if (e.key !== "Enter" && e.key !== " ") return;
  const el = document.activeElement;
  if (el?.classList?.contains("cell")) {
    e.preventDefault();
    el.click();
  }
});

function cycleType(index) {
  const col = state.payload.output.columns[index];
  const cycle = CYCLES[state.baseTypes[col.key]] || [];
  if (cycle.length < 2) return;

  const at = cycle.findIndex(
    (o) => o.type === col.type && (o.format || null) === (col.format || null),
  );
  const next = cycle[(at + 1) % cycle.length];
  const field = state.profile.fields.find((f) => f.key === col.key);
  if (!field) return;

  field.type = next.type;
  field.format = next.format || null;
  refresh();
}

/* ------------------------------------------------------------ 화면 컨트롤 */

$("optHighlight").addEventListener("change", function () {
  $("workspace").classList.toggle("plain", !this.checked);
});

$("segGrid").addEventListener("click", () => setOutputView(false));
$("segJson").addEventListener("click", () => setOutputView(true));
function setOutputView(json) {
  state.showJson = json;
  $("gridOut").hidden = json;
  $("jsonOut").hidden = !json;
  $("segGrid").setAttribute("aria-pressed", String(!json));
  $("segJson").setAttribute("aria-pressed", String(json));
  applySelection();
}

let scrollLock = false;
function linkScroll(a, b) {
  a.addEventListener("scroll", () => {
    if (scrollLock || !$("optSync").checked || state.showJson) return;
    scrollLock = true;
    b.scrollTop = a.scrollTop;
    requestAnimationFrame(() => {
      scrollLock = false;
    });
  });
}
linkScroll($("scrollA"), $("scrollB"));
linkScroll($("scrollB"), $("scrollA"));

$("sheetPicker").addEventListener("change", function () {
  state.sheet = this.value;
  state.sel = { row: null, col: null };
  refresh();
});

/* --------------------------------------------------------- 열기 / 내려받기 */

$("btnOpen").addEventListener("click", () => $("fileInput").click());
$("fileInput").addEventListener("change", function () {
  if (this.files?.[0]) loadWith(() => state.backend.open(this.files[0]));
  this.value = "";
});
$("btnSample").addEventListener("click", () => loadWith(() => state.backend.sample()));

let dragDepth = 0;
addEventListener("dragenter", (e) => {
  e.preventDefault();
  dragDepth++;
  $("dropOverlay").classList.add("on");
});
addEventListener("dragover", (e) => e.preventDefault());
addEventListener("dragleave", () => {
  if (--dragDepth <= 0) {
    dragDepth = 0;
    $("dropOverlay").classList.remove("on");
  }
});
addEventListener("drop", (e) => {
  e.preventDefault();
  dragDepth = 0;
  $("dropOverlay").classList.remove("on");
  if (e.dataTransfer.files?.[0]) {
    const file = e.dataTransfer.files[0];
    loadWith(() => state.backend.open(file));
  }
});

$("btnConvert").addEventListener("click", () => download($("fmtOut").value));
$("fmtOut").addEventListener("change", () => download($("fmtOut").value));

function saveBlob(bytes, filename, type = "application/octet-stream") {
  const url = URL.createObjectURL(new Blob([bytes], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

async function download(format) {
  if (!state.fileId) return;
  setBusy(true, "전체 변환 중…");
  try {
    const { bytes, filename, summary } = await state.backend.convert({
      fileId: state.fileId,
      profile: state.profile,
      sheet: state.sheet,
      format,
    });
    saveBlob(bytes, filename || `converted.${format}`);
    note(
      `${filename} 내려받음 — ${summary.rows.toLocaleString()}행` +
        (summary.issues ? ` · 검수 필요 ${summary.issues}건` : ""),
    );
  } catch (err) {
    note(err.message, "오류");
  } finally {
    setBusy(false);
  }
}

$("btnSchema").addEventListener("click", async () => {
  setBusy(true, "스키마 생성 중…");
  try {
    const schema = await state.backend.jsonSchema({
      fileId: state.fileId,
      profile: state.profile,
      sheet: state.sheet,
    });
    saveBlob(
      new TextEncoder().encode(JSON.stringify(schema, null, 2)),
      "schema.json",
      "application/json",
    );
  } catch (err) {
    note(err.message, "오류");
  } finally {
    setBusy(false);
  }
});

/* ------------------------------------------------------------------ 시작 */

(async function start() {
  setBusy(true, "준비 중…");
  state.backend = await pickBackend({
    onProgress: (text) => {
      if (text) setBusy(true, text);
    },
  });

  $("engineBadge").textContent = state.backend.label;
  $("engineBadge").title =
    state.backend.kind === "browser"
      ? "파일이 이 브라우저 밖으로 나가지 않습니다"
      : "사내 서버에서 처리합니다";
  $("engineBadge").classList.toggle("local", state.backend.kind === "browser");

  // 첫 화면이 빈 상태로 열리지 않도록 예시를 불러온다.
  await loadWith(() => state.backend.sample());
})();
