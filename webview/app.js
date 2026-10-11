// HealthBench results dashboard. Reads window.HEALTHBENCH_RUNS from data.js (written by build.py).

const RUNS = window.HEALTHBENCH_RUNS || [];
const PAGE_SIZE = 50;

const state = { run: null, visible: [], shown: 0 };

// ---------- helpers ----------

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

function fmtScore(score) {
  return score == null ? "n/a" : score.toFixed(3);
}

function pct(part, total) {
  return total ? `${Math.round((100 * part) / total)}%` : "n/a";
}

function shortDataset(path) {
  return path ? path.split("/").pop() : "";
}

function outcome(rubric) {
  if (rubric.points >= 0) return rubric.met ? "earned" : "missed";
  return rubric.met ? "penalty" : "avoided";
}

// ---------- runs table ----------

function renderRuns() {
  const tbody = document.querySelector("#runs tbody");
  document.getElementById("empty").hidden = RUNS.length > 0;

  for (const run of RUNS) {
    const bar = el("div", { class: "score-bar" }, el("span"));
    bar.firstChild.style.width = `${run.score * 100}%`;

    const row = el("tr", { "data-id": run.id },
      el("td", {}, el("code", { text: run.id })),
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

function selectRun(id) {
  state.run = RUNS.find((r) => r.id === id);
  document.querySelectorAll("#runs tbody tr").forEach((tr) => {
    tr.classList.toggle("selected", tr.dataset.id === id);
  });
  history.replaceState(null, "", `#${id}`);

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

  const width = 760, height = 220, left = 32, bottom = 24, top = 16;
  const plotW = width - left - 8, plotH = height - bottom - top;
  const max = Math.max(1, ...counts);
  const slot = plotW / counts.length;
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Histogram of per-conversation scores");

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
    const title = document.createElementNS(ns, "title");
    title.textContent = i === 0 ? `score < 0: ${count}` : `${labels[i]}–${(i / 10).toFixed(1)}: ${count}`;
    bar.append(title);
    if (count) add("text", { x: x + (slot - 6) / 2, y: top + plotH - h - 4, "text-anchor": "middle" }, count);
    add("text", { x: x + (slot - 6) / 2, y: height - 6, "text-anchor": "middle" }, labels[i]);
  });

  document.getElementById("histogram").replaceChildren(svg);
}

// ---------- conversations ----------

function matches(conversation, query) {
  if (!query) return true;
  const haystack = [
    ...conversation.prompt.map((m) => m.content),
    conversation.completion,
    ...conversation.rubrics.map((r) => r.explanation),
  ].join("\n").toLowerCase();
  return haystack.includes(query);
}

function renderConversations() {
  const query = document.getElementById("search").value.trim().toLowerCase();
  const sort = document.getElementById("sort").value;
  const list = state.run.conversations.filter((c) => matches(c, query));
  const key = (c) => (c.score == null ? Infinity : c.score);
  if (sort === "asc") list.sort((a, b) => key(a) - key(b));
  if (sort === "desc") list.sort((a, b) => key(b) - key(a));

  state.visible = list;
  state.shown = 0;
  document.getElementById("conversations").replaceChildren();
  document.getElementById("count").textContent = `${list.length} of ${state.run.conversations_count}`;
  showMore();
}

function showMore() {
  const container = document.getElementById("conversations");
  const next = state.visible.slice(state.shown, state.shown + PAGE_SIZE);
  container.append(...next.map(conversationItem));
  state.shown += next.length;
  document.getElementById("more").hidden = state.shown >= state.visible.length;
}

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

function conversationBody(c) {
  const body = el("div", { class: "body" });

  for (const message of c.prompt) {
    body.append(el("div", { class: "message" },
      el("div", { class: "role", text: message.role }),
      el("div", { class: "text", text: message.content }),
    ));
  }
  body.append(el("div", { class: "message completion" },
    el("div", { class: "role", text: "Model response (evaluated)" }),
    el("div", { class: "text", text: c.completion }),
  ));

  const rows = c.rubrics.map((r, i) => {
    const kind = outcome(r);
    return el("tr", {},
      el("td", { class: "num muted", text: i + 1 }),
      el("td", { class: `points num ${r.points >= 0 ? "pos" : "neg"}`, text: r.points > 0 ? `+${r.points}` : r.points }),
      el("td", { class: "outcome" }, el("span", { class: `pill ${kind}`, text: kind })),
      el("td", { text: r.explanation }),
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
          el("th", { text: "Grader explanation" }),
        )),
        el("tbody", {}, ...rows),
      ),
    ),
  );
  return body;
}

// ---------- init ----------

document.getElementById("search").addEventListener("input", renderConversations);
document.getElementById("sort").addEventListener("change", renderConversations);
document.getElementById("more").addEventListener("click", showMore);

renderRuns();
const initial = RUNS.find((r) => `#${r.id}` === location.hash) || RUNS[RUNS.length - 1];
if (initial) selectRun(initial.id);
