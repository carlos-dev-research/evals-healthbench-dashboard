// HealthBench dashboard: shared helpers, view switching and the Results view.
// (The New run view lives in run.js, which uses the helpers defined here.)
//
// Reads the runs that build.py wrote to data.js (window.HEALTHBENCH_RUNS and
// window.HEALTHBENCH_ERRORS, loaded by a <script> tag before this file) and renders:
//   - the runs table, with each run's status and skipped folders listed underneath
//   - the selected run's summary tiles and score histogram (hand-drawn SVG)
//   - the searchable, sortable conversation list; each conversation shows the
//     messages, the model response and every rubric criterion with its points,
//     outcome and the grader's explanation
//   - the Refresh button, which asks serve.py to rebuild data.js and reloads the page
//
// Views: the URL hash picks the view. "#new-run" shows the New run view; anything
// else shows Results, with "#test-..." naming the selected run.
//
// Plain JavaScript with no build step. Conversation messages and model responses
// are rendered as Markdown with marked and sanitized with DOMPurify (both in vendor/)
// before being inserted, so model output can't inject scripts. Everything else is
// inserted with textContent.

const RUNS = window.HEALTHBENCH_RUNS || [];
const ERRORS = window.HEALTHBENCH_ERRORS || [];
const PAGE_SIZE = 50; // conversations rendered per "Show more" click

// run: selected run; visible: conversations after search/sort; shown: how many are rendered
const state = { run: null, visible: [], shown: 0 };

// ---------- helpers ----------

// Create a DOM element: el("td", { class: "num", text: "5" }, child1, child2).
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) {
    if (child != null) node.append(child);
  }
  return node;
}

// Render Markdown into a new element: GitHub-flavored Markdown (tables, lists, code)
// with single line breaks kept, as chat output expects. The HTML is sanitized before
// insertion; images and forms are dropped so model output can't load remote content.
// Falls back to plain text if the vendor libraries didn't load.
const MARKDOWN_READY = Boolean(window.marked && window.DOMPurify);
if (MARKDOWN_READY) {
  marked.setOptions({ gfm: true, breaks: true });
  // Open links in a new tab, without giving the linked page access to this one.
  DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    if (node.tagName === "A") {
      node.setAttribute("target", "_blank");
      node.setAttribute("rel", "noopener noreferrer");
    }
  });
}

function markdown(source, className) {
  const node = el("div", { class: className });
  if (!MARKDOWN_READY) {
    node.textContent = source;
    node.classList.add("plain");
    return node;
  }
  node.innerHTML = DOMPurify.sanitize(marked.parse(source || ""), {
    FORBID_TAGS: ["img", "style", "form", "input", "button", "iframe"],
  });
  return node;
}

function fmtScore(score) {
  return score == null ? "n/a" : score.toFixed(3);
}

function pct(part, total) {
  return total ? `${Math.round((100 * part) / total)}%` : "n/a";
}

function shortDataset(path) {
  return path ? path.split("/").pop() : "";
}

// Classify a graded criterion for the colored pill in the rubric table:
// positive criteria are "earned" or "missed", negative ones "penalty" (met) or "avoided".
function outcome(rubric) {
  if (rubric.points >= 0) return rubric.met ? "earned" : "missed";
  return rubric.met ? "penalty" : "avoided";
}

// ---------- runs table ----------

function renderRuns() {
  const tbody = document.querySelector("#runs tbody");
  document.getElementById("empty").hidden = RUNS.length > 0;

  const skipped = document.getElementById("skipped");
  skipped.hidden = ERRORS.length === 0;
  skipped.replaceChildren(
    el("strong", { text: `${ERRORS.length} folder(s) skipped:` }),
    el("ul", {}, ...ERRORS.map((e) => el("li", {}, el("code", { text: e.id }), ` ${e.error}`))),
  );

  for (const run of RUNS) {
    const bar = el("div", { class: "score-bar" }, el("span"));
    bar.firstChild.style.width = `${run.score * 100}%`;

    // Runs made by this repo record their status; flag anything that didn't complete.
    const status = run.status && run.status !== "completed"
      ? el("span", { class: `pill status-${run.status}`, text: run.status }) : null;
    const row = el("tr", { "data-id": run.id },
      el("td", {}, el("code", { text: run.id }), status ? " " : null, status),
      el("td", { text: run.model || "" }),
      el("td", { text: run.grader || "" }),
      el("td", { text: `${run.model_reasoning || "-"} / ${run.grader_reasoning || "-"}` }),
      el("td", { text: shortDataset(run.dataset) }),
      el("td", { class: "num", text: run.conversations_count }),
      el("td", {}, el("div", { class: "score-cell" }, bar, el("strong", { text: fmtScore(run.score) }))),
    );
    row.addEventListener("click", () => selectRun(run.id));
    tbody.append(row);
  }
}

// Show one run's details. The run id is stored in the URL hash (#test-...) so a reload keeps it.
function selectRun(id, updateHash = true) {
  state.run = RUNS.find((r) => r.id === id);
  document.querySelectorAll("#runs tbody tr").forEach((tr) => {
    tr.classList.toggle("selected", tr.dataset.id === id);
  });
  if (updateHash) history.replaceState(null, "", `#${id}`);

  document.getElementById("run").hidden = false;
  document.getElementById("run-title").textContent = id;
  document.getElementById("search").value = "";
  document.getElementById("sort").value = "index";
  renderStats();
  renderHistogram();
  renderConversations();
}

// ---------- run summary ----------

function renderStats() {
  const run = state.run;
  const stats = [
    [fmtScore(run.score), "Run score"],
    [run.conversations_count, "Conversations"],
    [run.criteria_count, "Rubric criteria graded"],
    [pct(run.positive_met, run.positive_count), `Positive criteria met (${run.positive_met}/${run.positive_count})`],
    [pct(run.negative_met, run.negative_count), `Penalties triggered (${run.negative_met}/${run.negative_count})`],
  ];
  const container = document.getElementById("stats");
  container.replaceChildren(...stats.map(([value, label]) =>
    el("div", { class: "stat" }, el("div", { class: "value", text: value }), el("div", { class: "label", text: label })),
  ));
}

function renderHistogram() {
  // Bins: below 0, then ten bins of 0.1 from 0 to 1.
  const labels = ["<0", ...Array.from({ length: 10 }, (_, i) => (i / 10).toFixed(1))];
  const counts = new Array(labels.length).fill(0);
  for (const c of state.run.conversations) {
    if (c.score == null) continue;
    const bin = c.score < 0 ? 0 : 1 + Math.min(9, Math.floor(c.score * 10));
    counts[bin] += 1;
  }

  // Draw in a fixed 760x220 coordinate space; the viewBox scales it to the container width.
  const width = 760, height = 220, left = 32, bottom = 24, top = 16;
  const plotW = width - left - 8, plotH = height - bottom - top;
  const max = Math.max(1, ...counts);
  const slot = plotW / counts.length;
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Histogram of per-conversation scores");

  // SVG elements need createElementNS, so they get their own small helper.
  const add = (tag, attrs, text) => {
    const node = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    if (text != null) node.textContent = text;
    svg.append(node);
    return node;
  };

  add("line", { x1: left, x2: width - 8, y1: top + plotH, y2: top + plotH });
  counts.forEach((count, i) => {
    const h = (count / max) * plotH;
    const x = left + i * slot + 3;
    const bar = add("rect", { class: "bar", x, y: top + plotH - h, width: slot - 6, height: h, rx: 2 });
    // <title> gives the bar a hover tooltip, e.g. "0.4–0.5: 23".
    const title = document.createElementNS(ns, "title");
    title.textContent = i === 0 ? `score < 0: ${count}` : `${labels[i]}–${(i / 10).toFixed(1)}: ${count}`;
    bar.append(title);
    if (count) add("text", { x: x + (slot - 6) / 2, y: top + plotH - h - 4, "text-anchor": "middle" }, count);
    add("text", { x: x + (slot - 6) / 2, y: height - 6, "text-anchor": "middle" }, labels[i]);
  });

  document.getElementById("histogram").replaceChildren(svg);
}

// ---------- conversations ----------

// Case-insensitive search across the conversation, the model response and the grader explanations.
function matches(conversation, query) {
  if (!query) return true;
  const haystack = [
    ...conversation.prompt.map((m) => m.content),
    conversation.completion,
    ...conversation.rubrics.map((r) => `${r.criterion || ""}\n${r.explanation}`),
  ].join("\n").toLowerCase();
  return haystack.includes(query);
}

// Re-filter and re-sort the conversation list, then render the first page.
function renderConversations() {
  const query = document.getElementById("search").value.trim().toLowerCase();
  const sort = document.getElementById("sort").value;
  const list = state.run.conversations.filter((c) => matches(c, query));
  const key = (c) => (c.score == null ? Infinity : c.score); // unscored conversations sort last
  if (sort === "asc") list.sort((a, b) => key(a) - key(b));
  if (sort === "desc") list.sort((a, b) => key(b) - key(a));

  state.visible = list;
  state.shown = 0;
  document.getElementById("conversations").replaceChildren();
  document.getElementById("count").textContent = `${list.length} of ${state.run.conversations_count}`;
  showMore();
}

// Append the next PAGE_SIZE conversations, so long runs don't render everything at once.
function showMore() {
  const container = document.getElementById("conversations");
  const next = state.visible.slice(state.shown, state.shown + PAGE_SIZE);
  container.append(...next.map(conversationItem));
  state.shown += next.length;
  document.getElementById("more").hidden = state.shown >= state.visible.length;
}

// Collapsed row: number, last user message as a preview, points and score.
function conversationItem(c) {
  const lastUser = [...c.prompt].reverse().find((m) => m.role === "user");
  const details = el("details", { class: "conversation" },
    el("summary", {},
      el("span", { class: "idx", text: `#${c.index + 1}` }),
      el("span", { class: "preview", text: lastUser ? lastUser.content : "" }),
      el("span", { class: "muted", text: `${c.earned}/${c.possible} pts` }),
      el("span", { class: "badge", text: fmtScore(c.score) }),
    ),
  );
  // Build the body only when opened, to keep the page light.
  details.addEventListener("toggle", () => {
    if (details.open && details.children.length === 1) details.append(conversationBody(c));
  });
  return details;
}

// Expanded view: every message, the evaluated response, and the rubric table.
function conversationBody(c) {
  const body = el("div", { class: "body" });

  for (const message of c.prompt) {
    body.append(el("div", { class: "message" },
      el("div", { class: "role", text: message.role }),
      markdown(message.content, "text markdown"),
    ));
  }
  body.append(el("div", { class: "message completion" },
    el("div", { class: "role", text: "Model response (evaluated)" }),
    markdown(c.completion, "text markdown"),
  ));

  const rows = c.rubrics.map((r, i) => {
    const kind = outcome(r);
    // Criterion text and tags (e.g. "axis:accuracy") come from results.jsonl or the dataset;
    // older runs without a local dataset only have the grader's explanation.
    const criterion = r.criterion
      ? el("div", { class: "criterion", text: r.criterion })
      : el("div", { class: "criterion muted", text: "Criterion text not available (dataset not found in healthbench/)" });
    const tags = r.tags && r.tags.length
      ? el("div", { class: "tags" }, ...r.tags.map((t) => el("span", { class: "tag", text: t })))
      : null;
    return el("tr", {},
      el("td", { class: "num muted", text: i + 1 }),
      el("td", { class: `points num ${r.points >= 0 ? "pos" : "neg"}`, text: r.points > 0 ? `+${r.points}` : r.points }),
      el("td", { class: "outcome" }, el("span", { class: `pill ${kind}`, text: kind })),
      el("td", { class: "criterion-cell" }, criterion, tags),
      el("td", { class: "explanation", text: r.explanation }),
    );
  });
  body.append(
    el("div", { class: "role", text: `Rubric evaluation · ${c.earned} of ${c.possible} points` }),
    el("div", { class: "table-wrap" },
      el("table", { class: "rubrics" },
        el("thead", {}, el("tr", {},
          el("th", { class: "num", text: "#" }),
          el("th", { class: "num", text: "Points" }),
          el("th", { text: "Outcome" }),
          el("th", { text: "Rubric criterion" }),
          el("th", { text: "Grader explanation" }),
        )),
        el("tbody", {}, ...rows),
      ),
    ),
  );
  return body;
}

// ---------- refresh ----------

// Ask serve.py (POST /api/refresh) to rebuild data.js from the results/test-* folders, then reload.
async function refresh() {
  const button = document.getElementById("refresh");
  const status = document.getElementById("refresh-status");
  const setStatus = (text, isError = false) => {
    status.textContent = text;
    status.classList.toggle("error", isError);
  };

  // Opened straight from disk: there is no server to rebuild the data.
  if (location.protocol === "file:") {
    setStatus("Refresh needs the local server: run python serve.py, or run python build.py and reload.", true);
    return;
  }

  button.disabled = true;
  setStatus("Rebuilding…");
  try {
    const response = await fetch("/api/refresh", { method: "POST" });
    const result = await response.json().catch(() => ({}));
    if (!response.ok || !result.ok) throw new Error(result.error || `HTTP ${response.status}`);
    // data.js is loaded as a script, so reload the page to pick up the new runs.
    location.reload();
  } catch (error) {
    setStatus(`Refresh failed: ${error.message}. Is serve.py running?`, true);
    button.disabled = false;
  }
}

// ---------- views ----------

function currentView() {
  return location.hash === "#new-run" ? "new-run" : "results";
}

// Show the view named by the URL hash and highlight its tab.
function showView() {
  const view = currentView();
  document.getElementById("view-results").hidden = view !== "results";
  document.getElementById("view-new-run").hidden = view !== "new-run";
  document.querySelectorAll(".tabs a").forEach((a) => {
    if (a.dataset.view === view) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  // Back on Results: put the selected run back in the URL.
  if (view === "results" && state.run && location.hash !== `#${state.run.id}`) {
    history.replaceState(null, "", `#${state.run.id}`);
  }
}

// ---------- init ----------

document.getElementById("refresh").addEventListener("click", refresh);
document.getElementById("search").addEventListener("input", renderConversations);
document.getElementById("sort").addEventListener("change", renderConversations);
document.getElementById("more").addEventListener("click", showMore);
window.addEventListener("hashchange", showView);

renderRuns();
// Open the run named in the URL hash, or else the newest run.
const initial = RUNS.find((r) => `#${r.id}` === location.hash) || RUNS[RUNS.length - 1];
if (initial) selectRun(initial.id, currentView() === "results");
showView();
