"use strict";

// Highlight the upcoming measure this many seconds before its exact
// timestamp. The audio "timeupdate" event only fires a few times a second,
// so without a lead the visible highlight lags perceptibly behind the audio.
const HIGHLIGHT_LEAD_SECONDS = 0.1;

const state = {
  pieces: [],
  currentPieceId: null,
  currentPiece: null,
  score: null,
  alignment: null,
  measureBoxes: new Map(),
  measureFirstStart: new Map(),
  measureOccurrences: new Map(),
  sortedMeasures: [],
  currentMeasureNumber: null,
  mode: "play",
  rangeAnchor: null,
  range: null,
  followPlayback: true,
  selectionEndTime: null,
};

const el = {
  tabs: document.getElementById("piece-tabs"),
  scorePages: document.getElementById("score-pages"),
  modePlay: document.getElementById("mode-play"),
  modeSelect: document.getElementById("mode-select"),
  follow: document.getElementById("follow-playback"),
  resetBtn: document.getElementById("reset-btn"),
  audio: document.getElementById("audio"),
  nowPlayingTitle: document.getElementById("now-playing-title"),
  nowPlayingMeasure: document.getElementById("now-playing-measure"),
  rangeFrom: document.getElementById("range-from"),
  rangeTo: document.getElementById("range-to"),
  rangeClear: document.getElementById("range-clear"),
  playSelection: document.getElementById("play-selection"),
  rangeHint: document.getElementById("range-hint"),
  question: document.getElementById("question"),
  exampleQuestions: document.getElementById("example-questions"),
  askBtn: document.getElementById("ask-btn"),
  askStatus: document.getElementById("ask-status"),
  qaHistory: document.getElementById("qa-history"),
};

// play() returns a promise that rejects with AbortError when playback is
// interrupted on purpose (pause, a new source on tab switch); only report
// other failures.
function playAudio() {
  const started = el.audio.play();
  if (started && started.catch) {
    started.catch((err) => {
      if (err.name !== "AbortError") console.error(err);
    });
  }
}

async function fetchJSON(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = j.error || msg;
    } catch (_) {
      /* ignore */
    }
    throw new Error(msg);
  }
  return res.json();
}

function formatRangeList(ranges) {
  return ranges
    .map((r) => (r[0] === r[1] ? String(r[0]) : `${r[0]}-${r[1]}`))
    .join(", ");
}

// -- piece selection ---------------------------------------------------

async function loadPieces() {
  const data = await fetchJSON("/api/pieces");
  state.pieces = data.pieces;
  renderPieceTabs();
  const preferred = state.pieces.find((p) => p.sync_available) || state.pieces[0];
  await selectPiece(preferred.id);
}

function renderPieceTabs() {
  el.tabs.innerHTML = "";
  for (const p of state.pieces) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className =
      "piece-tab" +
      (p.id === state.currentPieceId ? " active" : "") +
      (p.pending_reason ? " pending" : "");
    const dot = document.createElement("span");
    dot.className = "dot";
    btn.appendChild(dot);
    btn.appendChild(document.createTextNode(p.title));
    btn.title = p.pending_reason || "Score/audio sync ready";
    btn.addEventListener("click", () => selectPiece(p.id));
    el.tabs.appendChild(btn);
  }
}

async function selectPiece(pieceId) {
  state.currentPieceId = pieceId;
  state.currentPiece = state.pieces.find((p) => p.id === pieceId);
  state.range = null;
  state.rangeAnchor = null;
  state.currentMeasureNumber = null;
  state.selectionEndTime = null;
  closeOccurrenceMenu();

  renderPieceTabs();

  el.nowPlayingTitle.textContent = state.currentPiece.title;
  el.nowPlayingMeasure.textContent = "";
  el.audio.pause();
  el.audio.src = `/media/audio/${pieceId}.mp3`;

  el.modePlay.disabled = !state.currentPiece.sync_available;
  el.modeSelect.disabled = !state.currentPiece.measures_known;
  setMode(state.currentPiece.sync_available ? "play" : "select");

  const [score, alignment] = await Promise.all([
    fetchJSON(`/api/pieces/${pieceId}/score`).catch(() => null),
    fetchJSON(`/api/pieces/${pieceId}/alignment`),
  ]);
  state.score = score;
  state.alignment = alignment;
  state.sortedMeasures = (alignment.measures || [])
    .slice()
    .sort((a, b) => a.start_seconds - b.start_seconds);
  state.measureFirstStart = new Map();
  state.measureOccurrences = new Map();
  for (const m of state.sortedMeasures) {
    if (!state.measureFirstStart.has(m.measure_number)) {
      state.measureFirstStart.set(m.measure_number, m.start_seconds);
    }
    if (!state.measureOccurrences.has(m.measure_number)) {
      state.measureOccurrences.set(m.measure_number, []);
    }
    state.measureOccurrences.get(m.measure_number).push(m);
  }

  renderScorePages();
  renderExampleQuestions();
  el.rangeFrom.max = state.currentPiece.max_measure_number || "";
  el.rangeTo.max = state.currentPiece.max_measure_number || "";
  updateRangeInputsFromState();
}

// -- score rendering -----------------------------------------------------

function renderScorePages() {
  el.scorePages.innerHTML = "";
  state.measureBoxes = new Map();

  if (!state.score) {
    const p = document.createElement("p");
    p.className = "hint";
    p.textContent = "Sheet-music page geometry is not available yet for this piece.";
    el.scorePages.appendChild(p);
    return;
  }

  for (const page of state.score.pages) {
    const pageEl = document.createElement("div");
    pageEl.className = "score-page";

    const img = document.createElement("img");
    img.src = page.image_url;
    img.alt = page.page_id;
    img.loading = "lazy";
    pageEl.appendChild(img);

    const overlay = document.createElement("div");
    overlay.className = "measure-overlay";
    for (const row of page.rows) {
      for (const m of row.measures) {
        const occurrences = state.measureOccurrences.get(m.measure_number) || [];
        const repeats = occurrences.length > 1;
        const box = document.createElement("div");
        box.className =
          "measure-box" +
          (state.currentPiece.sync_available ? "" : " no-sync") +
          (repeats ? " has-repeats" : "");
        box.dataset.measureNumber = String(m.measure_number);
        const b = m.bbox_normalized;
        box.style.left = b.x * 100 + "%";
        box.style.top = b.y * 100 + "%";
        box.style.width = b.width * 100 + "%";
        box.style.height = b.height * 100 + "%";
        box.title = repeats
          ? `Measure ${m.measure_number} (performed ${occurrences.length}×)`
          : `Measure ${m.measure_number}`;
        overlay.appendChild(box);
        state.measureBoxes.set(m.measure_number, box);
      }
    }
    pageEl.appendChild(overlay);
    el.scorePages.appendChild(pageEl);
  }

  updateCurrentHighlight();
  updateRangeHighlight();
}

el.scorePages.addEventListener("click", (e) => {
  const box = e.target.closest(".measure-box");
  if (!box) return;
  const measureNumber = parseInt(box.dataset.measureNumber, 10);
  if (state.mode === "play") {
    if (!state.currentPiece.sync_available) return;
    const occurrences = state.measureOccurrences.get(measureNumber) || [];
    if (occurrences.length > 1) {
      openOccurrenceMenu(box, measureNumber, occurrences);
    } else {
      closeOccurrenceMenu();
      seekToMeasure(measureNumber);
    }
  } else {
    closeOccurrenceMenu();
    handleRangeClick(measureNumber);
  }
});

el.scorePages.addEventListener("scroll", closeOccurrenceMenu);
window.addEventListener("resize", closeOccurrenceMenu);

// -- repeated-measure picker -----------------------------------------------
//
// A measure number can be performed more than once (a written repeat, e.g.
// Die Forelle replays several measures for its second verse). Clicking such
// a measure in "play" mode opens a small menu to pick which pass/occurrence
// to start playback from, instead of silently always jumping to the first.

let occurrenceMenuEl = null;
let occurrenceMenuOutsideHandler = null;

function closeOccurrenceMenu() {
  if (occurrenceMenuEl) {
    occurrenceMenuEl.remove();
    occurrenceMenuEl = null;
  }
  if (occurrenceMenuOutsideHandler) {
    document.removeEventListener("click", occurrenceMenuOutsideHandler);
    document.removeEventListener("keydown", occurrenceMenuOutsideHandler);
    occurrenceMenuOutsideHandler = null;
  }
}

function formatClockTime(seconds) {
  const total = Math.max(0, Math.round(seconds));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function openOccurrenceMenu(box, measureNumber, occurrences) {
  closeOccurrenceMenu();

  const menu = document.createElement("div");
  menu.className = "occurrence-menu";

  const title = document.createElement("div");
  title.className = "occurrence-menu-title";
  title.textContent = `Measure ${measureNumber} is performed ${occurrences.length}× — play from:`;
  menu.appendChild(title);

  occurrences.forEach((occ, i) => {
    const btn = document.createElement("button");
    btn.type = "button";
    const label = occ.repeat_pass != null ? `Pass ${occ.repeat_pass}` : `Occurrence ${i + 1}`;
    btn.textContent = `${label} — ${formatClockTime(occ.start_seconds)}`;
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      state.selectionEndTime = null;
      el.audio.currentTime = occ.start_seconds;
      playAudio();
      closeOccurrenceMenu();
    });
    menu.appendChild(btn);
  });

  document.body.appendChild(menu);
  const rect = box.getBoundingClientRect();
  const menuRect = menu.getBoundingClientRect();
  let left = rect.left + window.scrollX;
  const maxLeft = window.scrollX + document.documentElement.clientWidth - menuRect.width - 8;
  if (left > maxLeft) left = Math.max(8, maxLeft);
  menu.style.left = `${left}px`;
  menu.style.top = `${rect.bottom + window.scrollY + 4}px`;
  occurrenceMenuEl = menu;

  occurrenceMenuOutsideHandler = (e) => {
    if (e.type === "keydown") {
      if (e.key === "Escape") closeOccurrenceMenu();
      return;
    }
    if (occurrenceMenuEl && !occurrenceMenuEl.contains(e.target)) {
      closeOccurrenceMenu();
    }
  };
  // Deferred so the click that opened the menu doesn't immediately close it
  // once it bubbles up to document.
  setTimeout(() => {
    document.addEventListener("click", occurrenceMenuOutsideHandler);
    document.addEventListener("keydown", occurrenceMenuOutsideHandler);
  }, 0);
}

// -- playback sync ---------------------------------------------------------

function findMeasureAtTime(measures, t) {
  let lo = 0,
    hi = measures.length - 1,
    ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (measures[mid].start_seconds <= t) {
      ans = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return ans === -1 ? null : measures[ans];
}

el.audio.addEventListener("timeupdate", () => {
  if (state.selectionEndTime != null && el.audio.currentTime >= state.selectionEndTime) {
    el.audio.pause();
    state.selectionEndTime = null;
  }

  if (!state.currentPiece || !state.currentPiece.sync_available || !state.sortedMeasures.length) {
    return;
  }
  // Look slightly ahead of the raw playback position so the highlight lands
  // on the new measure right as it starts, instead of visibly lagging behind
  // it (timeupdate only fires a few times a second).
  const lookaheadTime = el.audio.currentTime + HIGHLIGHT_LEAD_SECONDS;
  const m = findMeasureAtTime(state.sortedMeasures, lookaheadTime);
  const num = m ? m.measure_number : null;
  if (num !== state.currentMeasureNumber) {
    state.currentMeasureNumber = num;
    updateCurrentHighlight();
    el.nowPlayingMeasure.textContent = num != null ? `Measure ${num}` : "";
    if (state.followPlayback && num != null) {
      const box = state.measureBoxes.get(num);
      if (box) box.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
    }
  }
});

function seekToMeasure(measureNumber) {
  const t = state.measureFirstStart.get(measureNumber);
  if (t == null) return;
  state.selectionEndTime = null;
  el.audio.currentTime = t;
  playAudio();
}

// Find the [start, end) audio window covering an inclusive measure range,
// using the first performed occurrence of the start measure and the nearest
// following occurrence of the end measure (falling back to its last
// occurrence if none follows, e.g. when the range is a single repeated bar).
function getRangeAudioWindow(startMeasure, endMeasure) {
  const startTime = state.measureFirstStart.get(startMeasure);
  if (startTime == null) return null;
  let endTime = null;
  for (const m of state.sortedMeasures) {
    if (m.measure_number === endMeasure && m.start_seconds >= startTime) {
      endTime = m.end_seconds;
      break;
    }
  }
  if (endTime == null) {
    for (let i = state.sortedMeasures.length - 1; i >= 0; i--) {
      if (state.sortedMeasures[i].measure_number === endMeasure) {
        endTime = state.sortedMeasures[i].end_seconds;
        break;
      }
    }
  }
  return endTime != null && endTime > startTime ? { startTime, endTime } : null;
}

function playSelection() {
  if (!state.range || !state.currentPiece || !state.currentPiece.sync_available) return;
  const audioWindow = getRangeAudioWindow(state.range[0], state.range[1]);
  if (!audioWindow) return;
  state.selectionEndTime = audioWindow.endTime;
  el.audio.currentTime = audioWindow.startTime;
  playAudio();
}

function updatePlaySelectionButton() {
  el.playSelection.disabled = !(
    state.range &&
    state.currentPiece &&
    state.currentPiece.sync_available
  );
}

el.playSelection.addEventListener("click", playSelection);

function updateCurrentHighlight() {
  for (const [num, box] of state.measureBoxes) {
    box.classList.toggle("current", num === state.currentMeasureNumber);
  }
}

// -- range selection ---------------------------------------------------

function handleRangeClick(measureNumber) {
  if (state.rangeAnchor === null) {
    state.rangeAnchor = measureNumber;
    state.range = null;
  } else {
    const a = state.rangeAnchor;
    const b = measureNumber;
    state.range = [Math.min(a, b), Math.max(a, b)];
    state.rangeAnchor = null;
    const scopeRange = document.querySelector('input[name="scope"][value="range"]');
    if (scopeRange) scopeRange.checked = true;
  }
  updateRangeInputsFromState();
  updateRangeHighlight();
}

function updateRangeHighlight() {
  for (const [num, box] of state.measureBoxes) {
    box.classList.remove("in-range", "anchor");
    if (state.rangeAnchor === num) {
      box.classList.add("anchor");
    } else if (state.range && num >= state.range[0] && num <= state.range[1]) {
      box.classList.add("in-range");
    }
  }
}

function updateRangeInputsFromState() {
  if (state.range) {
    el.rangeFrom.value = state.range[0];
    el.rangeTo.value = state.range[1];
    el.rangeHint.textContent = `Selected measures ${state.range[0]}–${state.range[1]}.`;
  } else if (state.rangeAnchor != null) {
    el.rangeFrom.value = state.rangeAnchor;
    el.rangeTo.value = "";
    el.rangeHint.textContent = `Anchor at measure ${state.rangeAnchor} — click another measure, or fill in "To".`;
  } else {
    el.rangeFrom.value = "";
    el.rangeTo.value = "";
    el.rangeHint.textContent = state.currentPiece && state.currentPiece.measures_known
      ? 'Click two measures on the score in "select range" mode, or type measure numbers here.'
      : "Measure numbers are not known yet for this piece.";
  }
  updatePlaySelectionButton();
}

function onRangeInputsChanged() {
  const from = parseInt(el.rangeFrom.value, 10);
  const to = parseInt(el.rangeTo.value, 10);
  if (Number.isFinite(from) && Number.isFinite(to) && from > 0 && to > 0) {
    state.range = [Math.min(from, to), Math.max(from, to)];
    state.rangeAnchor = null;
    updateRangeInputsFromState();
    updateRangeHighlight();
    const scopeRange = document.querySelector('input[name="scope"][value="range"]');
    if (scopeRange) scopeRange.checked = true;
  }
}
el.rangeFrom.addEventListener("change", onRangeInputsChanged);
el.rangeTo.addEventListener("change", onRangeInputsChanged);

el.rangeClear.addEventListener("click", () => {
  state.range = null;
  state.rangeAnchor = null;
  updateRangeInputsFromState();
  updateRangeHighlight();
});

// -- mode + follow toggles -----------------------------------------------

function setMode(mode) {
  state.mode = mode;
  el.modePlay.classList.toggle("active", mode === "play");
  el.modeSelect.classList.toggle("active", mode === "select");
  closeOccurrenceMenu();
}

el.modePlay.addEventListener("click", () => {
  if (!el.modePlay.disabled) setMode("play");
});
el.modeSelect.addEventListener("click", () => {
  if (!el.modeSelect.disabled) setMode("select");
});
el.follow.addEventListener("change", () => {
  state.followPlayback = el.follow.checked;
});

function resetSelection() {
  state.range = null;
  state.rangeAnchor = null;
  state.selectionEndTime = null;
  updateRangeInputsFromState();
  updateRangeHighlight();
  setMode(state.currentPiece && state.currentPiece.sync_available ? "play" : "select");

  const scopeWhole = document.querySelector('input[name="scope"][value="whole"]');
  if (scopeWhole) scopeWhole.checked = true;

  el.question.value = "";
  el.askStatus.textContent = "";
}

el.resetBtn.addEventListener("click", resetSelection);

// -- ask the RAG system ---------------------------------------------------

function makeTag(text, cls) {
  const span = document.createElement("span");
  span.className = "tag " + cls;
  span.textContent = text;
  return span;
}

const WEB_COACHING_LABEL = "Web / lesson video (not expert-curated)";
const SCORE_CONTEXT_NOTICE =
  "Answered from the score's lyrics only — no expert annotation covers these measures";

function makeLink(url, text) {
  const link = document.createElement("a");
  link.href = url;
  link.target = "_blank";
  link.rel = "noreferrer";
  link.textContent = text;
  return link;
}

// Parse the start of a locator such as "[12:01-12:20]" or "[1:02:03-1:02:30]"
// into seconds. Returns null when the locator has no leading timestamp.
function locatorStartSeconds(locator) {
  if (typeof locator !== "string") return null;
  const m = locator.match(/^\s*\[?\s*(\d+(?::\d{1,2}){1,2})/);
  if (!m) return null;
  return m[1].split(":").reduce((total, part) => total * 60 + parseInt(part, 10), 0);
}

function isYouTubeUrl(url) {
  try {
    const host = new URL(url).hostname.replace(/^www\.|^m\./, "");
    return host === "youtube.com" || host === "youtu.be" || host === "music.youtube.com";
  } catch (_) {
    return false;
  }
}

// Link to a web-coaching source; YouTube videos jump to the cited timestamp.
function webCoachingSourceUrl(source) {
  if (!source || !source.url) return null;
  const seconds = locatorStartSeconds(source.locator);
  if (source.kind !== "video" || seconds == null || !isYouTubeUrl(source.url)) {
    return source.url;
  }
  try {
    const url = new URL(source.url);
    url.searchParams.set("t", String(seconds));
    return url.toString();
  } catch (_) {
    return source.url;
  }
}

function appendWebCoachingSources(container, sources) {
  container.appendChild(document.createTextNode("Source: "));
  sources.forEach((source, index) => {
    if (index) container.appendChild(document.createTextNode(" · "));
    const kind = source.kind === "video" ? "Lesson video" : "Web page";
    const title = source.title || source.url || source.web_coaching_source_id || "source";
    const href = webCoachingSourceUrl(source);
    container.appendChild(document.createTextNode(`${kind}: `));
    container.appendChild(href ? makeLink(href, title) : document.createTextNode(title));
    const extras = [source.author, source.locator].filter(Boolean);
    if (extras.length) {
      container.appendChild(document.createTextNode(` (${extras.join(", ")})`));
    }
  });
}

function renderScoreContext(scoreContext) {
  const box = document.createElement("div");
  box.className = "lyrics-box";

  const head = document.createElement("div");
  head.className = "lyrics-head";
  const title = document.createElement("strong");
  title.textContent = "Lyrics in the selected measures";
  head.appendChild(title);
  const note = document.createElement("span");
  note.className = "lyrics-note";
  note.textContent = "LLM-assisted transcription";
  head.appendChild(note);
  box.appendChild(head);

  // Line labels can be long ("upper printed verse line (stanza 1 ...)"), so
  // show them once as numbered captions and tag each lyric line by number.
  const labels = scoreContext.line_labels || [];
  const numberLines = labels.length > 1;
  if (labels.length) {
    const legend = document.createElement("div");
    legend.className = "lyrics-legend";
    labels.forEach((label, index) => {
      const caption = document.createElement("div");
      caption.textContent = numberLines ? `${index + 1} = ${label}` : label;
      legend.appendChild(caption);
    });
    box.appendChild(legend);
  }
  const table = document.createElement("div");
  table.className = "lyrics-rows";
  for (const m of scoreContext.measures || []) {
    const row = document.createElement("div");
    row.className = "lyrics-row";
    const bar = document.createElement("span");
    bar.className = "lyrics-bar";
    bar.textContent = `m. ${m.measure}`;
    row.appendChild(bar);

    const lines = document.createElement("div");
    lines.className = "lyrics-lines";
    const lyricLines = m.lyric_lines || [];
    if (m.voice !== "sung" || !lyricLines.some((line) => line)) {
      const rest = document.createElement("span");
      rest.className = "lyrics-empty";
      rest.textContent =
        m.voice === "no_voice_staff" ? "no vocal staff" : m.voice === "rest" ? "rest" : "—";
      lines.appendChild(rest);
    } else {
      lyricLines.forEach((text, index) => {
        if (!text) return;
        const line = document.createElement("div");
        line.className = "lyrics-line";
        if (numberLines) {
          const caption = document.createElement("span");
          caption.className = "lyrics-label";
          caption.textContent = String(index + 1);
          caption.title = labels[index] || "";
          line.appendChild(caption);
        }
        line.appendChild(document.createTextNode(text));
        lines.appendChild(line);
      });
    }
    row.appendChild(lines);
    table.appendChild(row);
  }
  box.appendChild(table);

  if (scoreContext.truncated) {
    const more = document.createElement("div");
    more.className = "lyrics-note";
    more.textContent = "Only the first measures of the selection are shown.";
    box.appendChild(more);
  }
  return box;
}

function renderQAItem(question, result) {
  const item = document.createElement("div");
  item.className = "qa-item";

  const q = document.createElement("div");
  q.className = "qa-question";
  q.textContent = question;
  item.appendChild(q);

  const meta = document.createElement("div");
  meta.className = "qa-meta";
  const usesInternalKnowledge = result.answer_basis === "internal_knowledge";
  const generationUnavailable = result.answer_basis === "generation_unavailable";
  const noCorpusEvidence = result.answer_basis === "no_corpus_evidence";
  const scoreContextOnly = result.answer_basis === "score_context";
  let modeLabel = "Retrieval (extractive)";
  let modeClass = "mode-extractive";
  if (usesInternalKnowledge) {
    modeLabel = "LLM · internal knowledge";
    modeClass = "mode-internal";
  } else if (generationUnavailable) {
    modeLabel = "LLM unavailable";
    modeClass = "mode-unavailable";
  } else if (scoreContextOnly) {
    modeLabel = "Score lyrics only";
    modeClass = "mode-score";
  } else if (noCorpusEvidence) {
    modeLabel = "No corpus match";
    modeClass = "mode-unavailable";
  } else if (result.generation_mode === "llm") {
    modeLabel = "LLM · corpus-grounded";
    modeClass = "mode-llm";
  }
  const modeTag = makeTag(
    modeLabel,
    modeClass
  );
  if (result.generation_fallback_reason) {
    modeTag.title = result.generation_fallback_reason;
  }
  meta.appendChild(modeTag);
  meta.appendChild(makeTag("Soprano QA pipeline", "pipeline"));
  const answerPiece = state.pieces.find((piece) => piece.id === result.piece_id);
  meta.appendChild(
    makeTag(answerPiece ? answerPiece.title : result.piece_id, "answer-piece")
  );
  if (result.scope === "range") {
    meta.appendChild(
      makeTag(`Measures ${result.measure_range[0]}–${result.measure_range[1]}`, "scope-range")
    );
  } else {
    meta.appendChild(makeTag("Whole piece", "scope-whole"));
  }
  if (result.language) {
    meta.appendChild(
      makeTag(result.language === "en" ? "English" : "Korean", "language")
    );
  }
  item.appendChild(meta);

  if (scoreContextOnly) {
    const badge = document.createElement("div");
    badge.className = "qa-caveat qa-score-only";
    badge.textContent = SCORE_CONTEXT_NOTICE + ".";
    item.appendChild(badge);
  }

  const ans = document.createElement("div");
  ans.className = "qa-answer";
  ans.textContent = result.answer;
  item.appendChild(ans);

  if (usesInternalKnowledge) {
    const caveat = document.createElement("div");
    caveat.className = "qa-caveat";
    caveat.textContent =
      "No matching corpus evidence was used. This answer comes from the local LLM's " +
      "internal knowledge, so score-, edition-, and measure-specific details may be inaccurate.";
    item.appendChild(caveat);
  }

  if (result.score_context && (result.score_context.measures || []).length) {
    item.appendChild(renderScoreContext(result.score_context));
  }

  const notices = result.evidence_notices || [];
  const noticeById = new Map(notices.map((notice) => [notice.record_id, notice]));

  const details = document.createElement("details");
  details.className = "qa-evidence";
  const summary = document.createElement("summary");
  summary.textContent = `Evidence (${result.evidence.length})`;
  details.appendChild(summary);
  for (const ev of result.evidence) {
    const evEl = document.createElement("div");
    evEl.className = "evidence-item" + (ev.in_requested_scope ? "" : " out-of-scope");
    const metaLine = document.createElement("div");
    metaLine.className = "evidence-meta";
    const isWebCoaching = ev.evidence_type === "web_coaching";
    if (isWebCoaching) evEl.classList.add("web-coaching");
    const kindLabel =
      ev.kind === "sumijo"
        ? "Professional Soprano"
        : ev.kind === "expert"
        ? "Expert annotation"
        : isWebCoaching
        ? WEB_COACHING_LABEL
        : ev.usage_class === "research_conditional"
        ? "Web reference (conditional rights)"
        : "Web reference";
    const rangeLabel = ev.is_local
      ? ` · measures ${formatRangeList(ev.measure_ranges)}`
      : " · whole piece / general";
    metaLine.textContent =
      `[${ev.id}] · ${kindLabel}${rangeLabel}` +
      (ev.in_requested_scope ? "" : " (background only)");
    evEl.appendChild(metaLine);
    const textEl = document.createElement("div");
    textEl.textContent = ev.text_en || ev.text;
    evEl.appendChild(textEl);
    if (ev.text_en) {
      // English translation of the Korean expert unit; keep the original at hand.
      const original = document.createElement("details");
      original.className = "evidence-original";
      const originalSummary = document.createElement("summary");
      originalSummary.textContent = "English translation · show the Korean original";
      original.appendChild(originalSummary);
      const originalText = document.createElement("div");
      originalText.lang = "ko";
      originalText.textContent = ev.text;
      original.appendChild(originalText);
      evEl.appendChild(original);
    }
    if (ev.sources && ev.sources.length) {
      const sourceLine = document.createElement("div");
      sourceLine.className = "evidence-sources";
      sourceLine.appendChild(document.createTextNode("Sources: "));
      ev.sources.forEach((source, index) => {
        if (index) sourceLine.appendChild(document.createTextNode(" · "));
        const link = document.createElement("a");
        link.href = source.url;
        link.target = "_blank";
        link.rel = "noreferrer";
        link.textContent = source.title || source.web_source_id;
        sourceLine.appendChild(link);
      });
      evEl.appendChild(sourceLine);
    } else if (isWebCoaching) {
      const notice = noticeById.get(ev.id);
      if (notice && notice.sources && notice.sources.length) {
        const sourceLine = document.createElement("div");
        sourceLine.className = "evidence-sources";
        appendWebCoachingSources(sourceLine, notice.sources);
        evEl.appendChild(sourceLine);
      }
    }
    details.appendChild(evEl);
  }
  item.appendChild(details);

  if (notices.length) {
    const noticeDetails = document.createElement("details");
    noticeDetails.className = "qa-evidence qa-notices";
    const noticeSummary = document.createElement("summary");
    noticeSummary.textContent = `Source and rights notices (${notices.length})`;
    noticeDetails.appendChild(noticeSummary);
    for (const notice of notices) {
      const noticeEl = document.createElement("div");
      noticeEl.className = "evidence-item";
      const noticeMeta = document.createElement("div");
      noticeMeta.className = "evidence-meta";
      const noticeIsWebCoaching = (notice.sources || []).some(
        (source) => source.web_coaching_source_id || source.kind === "video" || source.kind === "web_page"
      );
      if (noticeIsWebCoaching) {
        noticeMeta.textContent = `[${notice.record_id}] · ${WEB_COACHING_LABEL}`;
        noticeEl.appendChild(noticeMeta);
        const sourceLine = document.createElement("div");
        sourceLine.className = "rights-condition";
        appendWebCoachingSources(sourceLine, notice.sources);
        noticeEl.appendChild(sourceLine);
        noticeDetails.appendChild(noticeEl);
        continue;
      }
      noticeMeta.textContent =
        `[${notice.record_id}] · ${notice.usage_class || "web evidence"}` +
        (notice.generated_text_license
          ? ` · generated summary: ${notice.generated_text_license}`
          : "");
      noticeEl.appendChild(noticeMeta);

      if (notice.generated_text_attribution) {
        const projectCredit = document.createElement("div");
        projectCredit.className = "rights-condition";
        projectCredit.appendChild(
          document.createTextNode(
            `Generated-text attribution: ${notice.generated_text_attribution}`
          )
        );
        if (notice.generated_text_license_url) {
          projectCredit.appendChild(document.createTextNode(" · "));
          const licenseLink = document.createElement("a");
          licenseLink.href = notice.generated_text_license_url;
          licenseLink.target = "_blank";
          licenseLink.rel = "noreferrer";
          licenseLink.textContent = "license";
          projectCredit.appendChild(licenseLink);
        }
        noticeEl.appendChild(projectCredit);
      }

      for (const inherited of notice.inherited_licenses || []) {
        const condition = document.createElement("div");
        condition.className = "rights-condition";
        condition.appendChild(document.createTextNode(
          `${inherited.attribution || "Source terms apply"}` +
          (inherited.license_id ? ` · ${inherited.license_id}` : "") +
          (inherited.jurisdictions && inherited.jurisdictions.length
            ? ` · ${inherited.jurisdictions.join(", ")}`
            : "")
        ));
        for (const [label, url] of [
          ["license", inherited.license_url],
          ["terms", inherited.terms_url],
        ]) {
          if (!url) continue;
          condition.appendChild(document.createTextNode(" · "));
          const link = document.createElement("a");
          link.href = url;
          link.target = "_blank";
          link.rel = "noreferrer";
          link.textContent = label;
          condition.appendChild(link);
        }
        noticeEl.appendChild(condition);
      }
      noticeDetails.appendChild(noticeEl);
    }
    item.appendChild(noticeDetails);
  }

  el.qaHistory.prepend(item);
}

async function askQuestion() {
  const question = el.question.value.trim();
  if (!question) {
    el.askStatus.textContent = "Type a question first.";
    return;
  }
  const scope = document.querySelector('input[name="scope"]:checked').value;
  let measureRange = null;
  if (scope === "range") {
    if (!state.range) {
      el.askStatus.textContent =
        'Select a measure range first (click two measures in "select range" mode, or use the From/To fields).';
      return;
    }
    measureRange = state.range;
  }

  el.askBtn.disabled = true;
  el.askStatus.textContent = "Thinking…";
  try {
    const result = await fetchJSON("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // "language" is omitted: the server infers it from the question
      // (Hangul -> Korean, otherwise English).
      body: JSON.stringify({
        piece_id: state.currentPieceId,
        question,
        measure_range: measureRange,
        generate: true,
      }),
    });
    el.askStatus.textContent = "";
    renderQAItem(question, result);
    el.question.value = "";
  } catch (err) {
    el.askStatus.textContent = "Something went wrong: " + err.message;
  } finally {
    el.askBtn.disabled = false;
  }
}

el.askBtn.addEventListener("click", askQuestion);

// Example questions (English; Korean questions work the same way). Range
// examples switch the scope to the selected measures when a range exists.
// Example questions follow sample_questions.md in soprano-qa-rag-system: five
// per piece with their measure ranges, in the English wording of the parallel
// English dataset (two without a counterpart there are translated directly).
const EXAMPLE_QUESTIONS = {
  "die-forelle": [
    { text: "What does the accent on the second beat in the piano part express?", range: [2, 27] },
    { text: "Why is this piece difficult to sing even though its notes are not high?", range: null },
    { text: "How should a change in the piece's mood be expressed?", range: [28, 40] },
    { text: "What should a singer do when German pronunciation is difficult?", range: null },
    { text: "What should a singer do to sing this piece well?", range: null },
  ],
  "una-voce-poco-fa": [
    { text: "Why did Rossini use a double-dotted rhythm instead of a standard dotted rhythm?", range: [21, 22] },
    { text: "How can rapid diction be practiced in passages with many short notes?", range: [31, 34] },
    { text: "The interludes in this piece are so long that I don't know what to do during them.", range: null },
    { text: "How should one practice coloratura technique effectively?", range: null },
    { text: "Why does this aria contain so many ornaments?", range: null },
  ],
  "la-capinera": [
    { text: "What musical effect does the contrast between ff and pp express?", range: [117, 124] },
    { text: "How should a trill be sung?", range: [137, 137] },
    { text: "How should the beat and rhythm be maintained in a passage without accompaniment?", range: [135, 137] },
    { text: "How should a slashed ornament be fitted into the rhythm?", range: [22, 22] },
    { text: "How should the lyrics l’erbe ed i fior be placed on the pitches?", range: [80, 80] },
  ],
  "nella-fantasia": [
    { text: "How should a sudden upward leap be sung?", range: [11, 17] },
    { text: "What should a singer do when singing is difficult or breath runs short?", range: [44, 52] },
    { text: "How long should the final note be held?", range: [34, 35] },
    { text: "Can this piece be regarded as classical, and can it be sung with classical vocal production?", range: null },
    { text: "With what dynamic should the opening note begin?", range: [7, 8] },
  ],
  "in-flowery-clouds": [
    { text: "If kkotbaram (\"flower breeze\") is often sung staccato in recordings but has no such marking in the score, what should guide the performance?", range: [11, 12] },
    { text: "Why does the meter change, and how does the mood of the piece change?", range: [39, 49] },
    { text: "How slowly should Larghetto be taken?", range: [74, 79] },
    { text: "If this song is too high, may I adjust or arrange it?", range: null },
    { text: "What should I do if the first note does not come out well when a phrase begins on a high note?", range: [19, 21] },
  ],
};

function applyExampleQuestion(example) {
  el.question.value = example.text;
  if (example.range && state.currentPiece && state.currentPiece.measures_known) {
    state.range = example.range.slice();
    state.rangeAnchor = null;
    updateRangeInputsFromState();
    updateRangeHighlight();
    const firstBox = state.measureBoxes.get(example.range[0]);
    if (firstBox) firstBox.scrollIntoView({ behavior: "smooth", block: "center" });
  }
  const scope = example.range && state.range ? "range" : "whole";
  const radio = document.querySelector(`input[name="scope"][value="${scope}"]`);
  if (radio) radio.checked = true;
  el.question.focus();
}

function renderExampleQuestions() {
  if (!el.exampleQuestions) return;
  el.exampleQuestions.innerHTML = "";
  const examples = EXAMPLE_QUESTIONS[state.currentPieceId] || [];
  for (const example of examples) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "example-chip";
    const [lo, hi] = example.range || [];
    btn.textContent = !example.range
      ? example.text
      : lo === hi ? `${example.text} (measure ${lo})` : `${example.text} (measures ${lo}–${hi})`;
    btn.addEventListener("click", () => applyExampleQuestion(example));
    el.exampleQuestions.appendChild(btn);
  }
}
renderExampleQuestions();
el.question.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) askQuestion();
});

// -- boot ---------------------------------------------------------------

loadPieces().catch((err) => {
  console.error("Failed to load pieces:", err);
});
