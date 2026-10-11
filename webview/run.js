// HealthBench dashboard: the New run view.
//
// Lets the user start an evaluation run from the page and follow its progress:
//   - fills the dataset list from GET /api/datasets (files in healthbench/)
//   - sends the form to POST /api/runs to start a run on the local server
//   - polls GET /api/runs/current once a second and draws the progress bars,
//     elapsed and remaining time, running score and recent errors
//   - POST /api/runs/cancel stops the run after the conversation in progress
//   - when the run is finished and the data rebuilt, "View results" reloads the
//     page on the new run
//
// The form values (base URL, API key, models) live only in the form fields:
// they are not stored in localStorage, cookies or the URL, and the form has
// autocomplete off. They are sent to the local server once, when a run starts.
// If the page is reloaded mid-run, the progress picks up again from the server.
//
// Uses el(), fmtScore() and currentView() from app.js, which loads first.

const POLL_MS = 1000;
const FINISHED = ["completed", "cancelled", "failed"];
const runState = { timer: null, datasetsLoaded: false };

const $ = (id) => document.getElementById(id);

function fmtDuration(seconds) {
  if (seconds == null || !isFinite(seconds)) return "–";
  seconds = Math.max(0, Math.round(seconds));
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = seconds % 60;
  return h ? `${h}h ${m}m` : m ? `${m}m ${s}s` : `${s}s`;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok || body.ok === false) throw new Error(body.error || `HTTP ${response.status}`);
  return body;
}

function setFormStatus(text, isError = false) {
  const status = $("run-form-status");
  status.textContent = text;
  status.className = isError ? "error" : "muted";
}

// ---------- datasets ----------

async function loadDatasets() {
  const select = document.querySelector("#run-form [name=dataset]");
  const hint = $("dataset-hint");
  try {
    const { datasets } = await api("/api/datasets");
    runState.datasetsLoaded = true;
    if (!datasets.length) {
      select.replaceChildren(el("option", { value: "", text: "No dataset files found" }));
      hint.replaceChildren(
        "No .jsonl files in ", el("code", { text: "healthbench/" }), ". Download HealthBench with ",
        el("code", { text: "hf download openai/healthbench --repo-type dataset --local-dir ./healthbench" }),
        " (see the README), then reopen this tab.",
      );
      hint.classList.add("error");
      return;
    }
    hint.classList.remove("error");
    hint.replaceChildren("Files in ", el("code", { text: "healthbench/" }), ".");
    select.replaceChildren(...datasets.map((d) =>
      el("option", { value: d.name, text: `${d.name} (${d.conversations} conversations)` })));
  } catch (error) {
    select.replaceChildren(el("option", { value: "", text: "Couldn't load datasets" }));
    hint.textContent = `Couldn't load the dataset list: ${error.message}`;
    hint.classList.add("error");
  }
}

// ---------- starting and cancelling ----------

async function startRun(event) {
  event.preventDefault();
  const form = event.target;
  const data = Object.fromEntries(new FormData(form).entries());
  if (!data.dataset) {
    setFormStatus("Choose a dataset file first.", true);
    return;
  }
  $("start-run").disabled = true;
  setFormStatus("Starting…");
  try {
    await api("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    setFormStatus("");
    pollProgress();
  } catch (error) {
    setFormStatus(`Couldn't start the run: ${error.message}`, true);
    $("start-run").disabled = false;
  }
}

async function cancelRun() {
  $("cancel-run").disabled = true;
  try {
    await api("/api/runs/cancel", { method: "POST" });
    $("run-criterion").textContent = "Cancelling after the conversation in progress…";
  } catch (error) {
    $("cancel-run").disabled = false;
    setFormStatus(`Couldn't cancel: ${error.message}`, true);
  }
}

// ---------- progress ----------

async function pollProgress() {
  clearTimeout(runState.timer);
  let progress;
  try {
    progress = await api("/api/runs/current");
  } catch (error) {
    $("run-criterion").textContent = `Lost contact with the server (${error.message}). Retrying…`;
    runState.timer = setTimeout(pollProgress, POLL_MS * 3);
    return;
  }
  renderProgress(progress);
  // Keep polling until the run is over and its results are rebuilt.
  const settled = progress.state === "idle" || (FINISHED.includes(progress.state) && progress.results_ready);
  if (!settled) runState.timer = setTimeout(pollProgress, POLL_MS);
}

function renderProgress(p) {
  const running = p.state === "running" || p.state === "starting";
  $("start-run").disabled = running;
  if (p.state === "idle") {
    $("run-progress").hidden = true;
    return;
  }
  $("run-progress").hidden = false;

  const stateLabel = $("run-state");
  stateLabel.textContent = p.state;
  stateLabel.className = `pill status-${p.state}`;

  const where = p.folder
    ? el("span", {}, running ? "Saving to " : "Saved to ", el("code", { text: `results/${p.folder}/` }))
    : running ? "Preparing…" : "Nothing was saved: no conversation could be graded.";
  $("run-folder").replaceChildren(
    where,
    p.model_test ? ` · ${p.model_test} graded by ${p.model_grader} · ${p.dataset}` : "",
  );

  // Main bar: conversations finished (graded or failed), plus the share of the current one already graded.
  const total = p.total || 0;
  const processed = (p.done || 0) + (p.failed || 0);
  const partial = running && p.criterion_total ? p.criterion_done / p.criterion_total : 0;
  const fraction = total ? Math.min(1, (processed + partial) / total) : 0;
  const [mainBar, criterionBar] = document.querySelectorAll("#run-progress .progress-bar");
  mainBar.querySelector("span").style.width = `${fraction * 100}%`;
  mainBar.setAttribute("aria-valuenow", Math.round(fraction * 100));
  $("run-counts").replaceChildren(
    el("strong", { text: `${processed} of ${total}` }), " conversations",
    p.failed ? el("span", { class: "error", text: ` · ${p.failed} failed` }) : "",
    el("span", { class: "muted", text: ` · ${Math.round(fraction * 100)}%` }),
  );

  // Second bar: criteria graded in the conversation in progress.
  const criterionFraction = p.criterion_total ? p.criterion_done / p.criterion_total : 0;
  criterionBar.hidden = !running;
  criterionBar.querySelector("span").style.width = `${criterionFraction * 100}%`;
  if (running) {
    $("run-criterion").textContent = p.criterion_total
      ? `Conversation ${processed + 1}: graded ${p.criterion_done} of ${p.criterion_total} criteria`
      : `Conversation ${Math.min(processed + 1, total)}: generating the model response…`;
  } else {
    $("run-criterion").textContent = p.results_ready ? "" : "Saving and rebuilding the results…";
  }

  // Time: remaining is estimated from the average time per conversation so far.
  const now = p.finished_at || Date.now() / 1000;
  const elapsed = p.started_at ? now - p.started_at : null;
  const remaining = running && processed + partial > 0
    ? (elapsed / (processed + partial)) * (total - processed - partial) : null;
  const tiles = [
    [fmtDuration(elapsed), "Elapsed"],
    [running ? fmtDuration(remaining) : "–", "Estimated time left"],
    [fmtScore(p.score), running ? "Score so far" : "Final score"],
    [String(p.done || 0), "Conversations graded"],
  ];
  $("run-stats").replaceChildren(...tiles.map(([value, label]) =>
    el("div", { class: "stat" }, el("div", { class: "value", text: value }), el("div", { class: "label", text: label }))));

  const errors = p.errors || [];
  $("run-errors").hidden = errors.length === 0;
  $("run-errors").replaceChildren(
    el("strong", { text: p.state === "failed" ? "The run stopped:" : "Recent errors (those conversations were skipped):" }),
    el("ul", {}, ...errors.map((e) => el("li", { text: e }))),
  );

  $("cancel-run").hidden = !running;
  if (running) $("cancel-run").disabled = false;

  // Finished: offer to open the new run once data.js has been rebuilt.
  const viewLink = $("view-run");
  viewLink.hidden = !(FINISHED.includes(p.state) && p.results_ready && p.done > 0);
  viewLink.href = `#${p.folder}`;
}

// ---------- init ----------

function initRunView() {
  if (location.protocol === "file:") {
    const notice = $("run-unavailable");
    notice.hidden = false;
    notice.textContent = "Starting runs needs the local server. Run python serve.py and open http://localhost:8000.";
    $("start-run").disabled = true;
    return;
  }
  // Load the dataset list when the view is opened (again after a failed load).
  const onView = () => { if (currentView() === "new-run" && !runState.datasetsLoaded) loadDatasets(); };
  window.addEventListener("hashchange", onView);
  onView();
  pollProgress(); // resumes the display if a run is already going
}

$("run-form").addEventListener("submit", startRun);
$("cancel-run").addEventListener("click", cancelRun);
// data.js only changes on reload, so jump to the new run with a full reload.
$("view-run").addEventListener("click", (event) => {
  event.preventDefault();
  location.hash = $("view-run").getAttribute("href");
  location.reload();
});
initRunView();
