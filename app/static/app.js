/* CEHub RMV front-end — single-page controller.
 *
 * Backend contract (see cehub-rmv-platform app/main.py):
 *   GET  /cases                 → { cases: [...] }
 *   GET  /modules               → { modules: [...] }
 *   POST /submissions (multipart: candidate_id, file)
 *                               → { submission_id, ... }
 *   POST /sessions              → { session_id, product_type, content_id,
 *                                   opening_message, first_prompt, phase }
 *   POST /sessions/{id}/respond → { done, next_prompt, phase, prompt_id, is_followup }
 *   GET  /sessions/{id}/result  → { status: "scoring" | "scored", result }
 *
 * The front-end is served same-origin by the FastAPI app, but will honor a
 * window.RMV_API_BASE override if the Thinkific embed needs to point elsewhere.
 *
 * URL query string (from Thinkific iframe):
 *   candidate_id=<id>          — participant ID (required; prompts if missing)
 *   product=assigned_case|case_based|mastery_module
 *                              — skip the launcher and go straight to a product
 *   case_id=<id>               — assigned_case deep link to a specific case
 *   module_id=<id>             — mastery_module deep link to a specific module
 *   attempt=<n>                — mastery_module attempt number (default 1)
 */
(function () {
  "use strict";

  const API_BASE = (window.RMV_API_BASE || "").replace(/\/+$/, "");

  // ---- URL params -------------------------------------------------------

  const params = new URLSearchParams(window.location.search);
  const urlCandidateId = params.get("candidate_id") || params.get("participant_id") || "";
  const urlProduct = params.get("product") || "";
  const urlCaseId = params.get("case_id") || "";
  const urlModuleId = params.get("module_id") || "";
  const urlAttempt = parseInt(params.get("attempt") || "1", 10) || 1;

  // ---- State ------------------------------------------------------------

  const state = {
    candidateId: urlCandidateId,
    productType: null,
    sessionId: null,
    turns: [],
    currentPrompt: null,
    currentPhase: null,
    pollHandle: null,
  };

  // ---- API helpers ------------------------------------------------------

  async function api(path, options = {}) {
    const res = await fetch(API_BASE + path, {
      headers: options.body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" },
      ...options,
    });

    // Read the body exactly once. Calling res.json() and then falling back
    // to res.text() would throw "body stream already read" because .json()
    // consumes the stream even when parsing fails.
    const raw = await res.text();

    if (!res.ok) {
      let detail = raw;
      try {
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === "object") {
          if (typeof parsed.detail === "string") {
            detail = parsed.detail;
          } else if (Array.isArray(parsed.detail)) {
            // FastAPI validation errors come back as an array
            detail = parsed.detail
              .map((e) => `${(e.loc || []).join(".")}: ${e.msg}`)
              .join("; ");
          } else {
            detail = JSON.stringify(parsed);
          }
        }
      } catch (_) {
        // Body wasn't JSON — keep the raw text as the detail.
      }
      throw new Error(`HTTP ${res.status}: ${detail || res.statusText}`);
    }

    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch (e) {
      throw new Error(`Invalid JSON response from ${path}: ${e.message}`);
    }
  }

  const apiListCases = () => api("/cases");
  const apiListModules = () => api("/modules");
  const apiUploadSubmission = (candidateId, file) => {
    const fd = new FormData();
    fd.append("candidate_id", candidateId);
    fd.append("file", file);
    return api("/submissions", { method: "POST", body: fd });
  };
  const apiStartSession = (body) =>
    api("/sessions", { method: "POST", body: JSON.stringify(body) });
  const apiRespond = (sessionId, response) =>
    api(`/sessions/${sessionId}/respond`, {
      method: "POST",
      body: JSON.stringify({ response }),
    });
  const apiGetResult = (sessionId) => api(`/sessions/${sessionId}/result`);

  // ---- DOM helpers ------------------------------------------------------

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const views = {
    identify: $("#view-identify"),
    launcher: $("#view-launcher"),
    acPicker: $("#view-ac-picker"),
    cbUpload: $("#view-cb-upload"),
    mmPicker: $("#view-mm-picker"),
    session: $("#view-session"),
    result: $("#view-result"),
    error: $("#view-error"),
  };

  function showView(name) {
    for (const key in views) views[key].classList.add("hidden");
    if (views[name]) views[name].classList.remove("hidden");
  }

  function setChips() {
    const candChip = $("#candidate-chip");
    if (state.candidateId) {
      candChip.textContent = `Participant: ${state.candidateId}`;
      candChip.classList.remove("hidden");
    } else {
      candChip.classList.add("hidden");
    }
    const prodChip = $("#product-chip");
    if (state.productType) {
      prodChip.textContent = productLabel(state.productType);
      prodChip.classList.remove("hidden");
    } else {
      prodChip.classList.add("hidden");
    }
  }

  function productLabel(pt) {
    switch (pt) {
      case "assigned_case": return "Assigned Case RMV";
      case "case_based": return "Case-Based RMV";
      case "mastery_module": return "Mastery Module RMV";
      default: return pt;
    }
  }

  function phaseLabel(phaseId) {
    if (!phaseId) return "";
    return phaseId
      .split("_")
      .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
      .join(" ");
  }

  function showError(err, retryFn) {
    $("#error-message").textContent = err && err.message ? err.message : String(err);
    const retryBtn = $("#error-retry");
    retryBtn.onclick = retryFn || (() => goHome());
    retryBtn.style.display = retryFn ? "" : "none";
    showView("error");
    console.error(err);
  }

  // ---- Launcher ---------------------------------------------------------

  function goHome() {
    stopResultPolling();
    state.productType = null;
    state.sessionId = null;
    state.turns = [];
    state.currentPrompt = null;
    state.currentPhase = null;
    setChips();
    showView("launcher");
  }

  function bindLauncher() {
    $$("#view-launcher .tile").forEach((btn) => {
      btn.addEventListener("click", () => {
        const pt = btn.dataset.product;
        state.productType = pt;
        setChips();
        if (pt === "assigned_case") openAssignedCasePicker();
        else if (pt === "case_based") openCaseBasedUpload();
        else if (pt === "mastery_module") openMasteryModulePicker();
      });
    });

    $$('[data-action="back-to-launcher"]').forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        goHome();
      });
    });

    $("#error-home").addEventListener("click", goHome);
    $("#restart-btn").addEventListener("click", goHome);
  }

  // ---- Assigned Case flow ----------------------------------------------

  async function openAssignedCasePicker() {
    showView("acPicker");
    const list = $("#ac-case-list");
    list.innerHTML = '<div class="status">Loading cases…</div>';
    try {
      const data = await apiListCases();
      list.innerHTML = "";
      (data.cases || []).forEach((c) => {
        const row = document.createElement("button");
        row.type = "button";
        row.className = "list-item";
        row.innerHTML = `
          <div>
            <div class="list-item-title">${escapeHtml(c.title || c.case_id)}</div>
            <div class="list-item-meta">${escapeHtml([c.species, c.difficulty].filter(Boolean).join(" · "))}</div>
          </div>
          <div class="list-item-right">${escapeHtml(c.estimated_duration_minutes ? c.estimated_duration_minutes + " min" : "")}</div>
        `;
        row.addEventListener("click", () => startSession({ case_id: c.case_id }));
        list.appendChild(row);
      });
      if (!list.children.length) {
        list.innerHTML = '<div class="status">No active cases are available.</div>';
      }
    } catch (err) {
      showError(err, openAssignedCasePicker);
    }
  }

  $("#ac-random")?.addEventListener("click", () => startSession({}));

  // ---- Case-Based flow --------------------------------------------------

  function openCaseBasedUpload() {
    showView("cbUpload");
    $("#cb-upload-status").classList.add("hidden");
    $("#cb-form").reset();
  }

  $("#cb-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fileInput = $("#cb-file");
    const file = fileInput.files && fileInput.files[0];
    if (!file) return;
    const statusEl = $("#cb-upload-status");
    statusEl.classList.remove("hidden");
    statusEl.textContent = `Uploading ${file.name}…`;
    try {
      const up = await apiUploadSubmission(state.candidateId, file);
      statusEl.textContent = `Uploaded (${up.char_count.toLocaleString()} characters). Generating prompts…`;
      await startSession({ submission_id: up.submission_id });
    } catch (err) {
      showError(err, openCaseBasedUpload);
    }
  });

  // ---- Mastery Module flow ----------------------------------------------

  async function openMasteryModulePicker() {
    showView("mmPicker");
    const list = $("#mm-module-list");
    list.innerHTML = '<div class="status">Loading modules…</div>';
    try {
      const data = await apiListModules();
      list.innerHTML = "";
      (data.modules || []).forEach((m) => {
        const row = document.createElement("button");
        row.type = "button";
        row.className = "list-item";
        row.innerHTML = `
          <div>
            <div class="list-item-title">${escapeHtml(m.module_title || m.title || m.module_id)}</div>
            <div class="list-item-meta">${escapeHtml([m.discipline, m.module_id].filter(Boolean).join(" · "))}</div>
          </div>
          <div class="list-item-right">${escapeHtml(m.estimated_duration_minutes ? m.estimated_duration_minutes + " min" : "")}</div>
        `;
        row.addEventListener("click", () =>
          startSession({ module_id: m.module_id, attempt_number: urlAttempt })
        );
        list.appendChild(row);
      });
      if (!list.children.length) {
        list.innerHTML = '<div class="status">No active modules are available.</div>';
      }
    } catch (err) {
      showError(err, openMasteryModulePicker);
    }
  }

  // ---- Session / turn loop ----------------------------------------------

  async function startSession(extra) {
    if (!state.candidateId) {
      showView("identify");
      return;
    }
    showView("session");
    state.turns = [];
    renderTranscript();
    $("#opening-message").textContent = "Starting session…";
    $("#phase-indicator").textContent = "";
    $("#response-input").value = "";
    $("#submit-response").disabled = true;
    $("#submit-response").textContent = "Submit response";
    // Make sure the respond form is visible. It may have been hidden by a
    // previous completed session (done=true path) in the same iframe load.
    $("#respond-form").classList.remove("hidden");

    const body = {
      product_type: state.productType,
      participant_id: state.candidateId,
      attempt_number: extra.attempt_number || 1,
      ...extra,
    };

    try {
      const sess = await apiStartSession(body);
      state.sessionId = sess.session_id;
      state.currentPrompt = sess.first_prompt;
      state.currentPhase = sess.phase;
      $("#opening-message").textContent = sess.opening_message;
      $("#session-title").textContent = productLabel(sess.product_type);
      $("#phase-indicator").textContent = phaseLabel(sess.phase);
      addTurn("examiner", sess.first_prompt, false);
      $("#submit-response").disabled = false;
      $("#response-input").focus();
    } catch (err) {
      showError(err, () => startSession(extra));
    }
  }

  $("#respond-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const textarea = $("#response-input");
    const value = textarea.value.trim();
    if (!value || !state.sessionId) return;

    const submitBtn = $("#submit-response");
    submitBtn.disabled = true;
    submitBtn.textContent = "Submitting…";

    addTurn("candidate", value, false);
    textarea.value = "";

    try {
      const result = await apiRespond(state.sessionId, value);

      if (result.done) {
        addTurn("examiner", result.next_prompt || "Thank you. Your assessment is complete.", false);
        submitBtn.textContent = "Submit response";
        $("#respond-form").classList.add("hidden");
        openResultView();
        return;
      }

      state.currentPrompt = result.next_prompt;
      state.currentPhase = result.phase;
      $("#phase-indicator").textContent = phaseLabel(result.phase);
      addTurn("examiner", result.next_prompt || "", !!result.is_followup);

      submitBtn.disabled = false;
      submitBtn.textContent = "Submit response";
      $("#response-input").focus();
    } catch (err) {
      // Put the user's text back so they can retry.
      textarea.value = value;
      state.turns.pop(); // remove optimistic candidate turn
      renderTranscript();
      submitBtn.disabled = false;
      submitBtn.textContent = "Submit response";
      showError(err, () => {
        showView("session");
        renderTranscript();
      });
    }
  });

  function addTurn(role, text, isFollowup) {
    state.turns.push({ role, text, isFollowup });
    renderTranscript();
  }

  function renderTranscript() {
    const wrap = $("#transcript");
    wrap.innerHTML = "";
    state.turns.forEach((t) => {
      const row = document.createElement("div");
      row.className = `turn turn-${t.role}${t.isFollowup ? " is-followup" : ""}`;
      const label = document.createElement("div");
      label.className = "turn-label";
      label.textContent =
        t.role === "examiner"
          ? t.isFollowup ? "Examiner (follow-up)" : "Examiner"
          : "You";
      const body = document.createElement("div");
      body.className = "turn-text";
      body.textContent = t.text;
      row.appendChild(label);
      row.appendChild(body);
      wrap.appendChild(row);
    });
    // Auto-scroll the last turn into view inside the iframe.
    const last = wrap.lastElementChild;
    if (last && last.scrollIntoView) {
      last.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }

  // ---- Result view ------------------------------------------------------

  function openResultView() {
    showView("result");
    $("#result-status").textContent =
      "Your responses have been submitted. Scoring is in progress — this typically takes under a minute.";
    $("#result-body").classList.add("hidden");
    pollResult(0);
  }

  function stopResultPolling() {
    if (state.pollHandle) {
      clearTimeout(state.pollHandle);
      state.pollHandle = null;
    }
  }

  async function pollResult(attempt) {
    if (!state.sessionId) return;
    try {
      const data = await apiGetResult(state.sessionId);
      if (data.status === "scored" && data.result) {
        renderResult(data.result);
        return;
      }
    } catch (err) {
      // A transient error during scoring shouldn't kill the view;
      // keep polling up to a reasonable ceiling.
      console.warn("result poll error:", err);
    }
    if (attempt > 40) {
      $("#result-status").textContent =
        "Scoring is taking longer than expected. You may close this window — your result will be available through the standard reporting process.";
      return;
    }
    state.pollHandle = setTimeout(() => pollResult(attempt + 1), 3000);
  }

  // Outcomes vary per product but collapse cleanly to three visual states:
  //   pass / verified / mastered           → green
  //   borderline / borderline_review        → yellow
  //   fail / not_verified / not_yet_mastered → red
  const POSITIVE_OUTCOMES = new Set(["pass", "verified", "mastered"]);
  const NEGATIVE_OUTCOMES = new Set(["fail", "not_verified", "not_yet_mastered"]);

  function outcomeToneClass(outcome) {
    if (!outcome) return "";
    if (POSITIVE_OUTCOMES.has(outcome)) return "outcome-tone-positive";
    if (NEGATIVE_OUTCOMES.has(outcome)) return "outcome-tone-negative";
    return "outcome-tone-neutral";
  }

  function humanize(str) {
    return String(str || "").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }

  function renderResult(result) {
    $("#result-status").textContent = "Scoring complete.";
    const body = $("#result-body");
    body.classList.remove("hidden");
    body.innerHTML = "";

    // Outcome chip
    const outcome = result.outcome || "";
    if (outcome) {
      const chip = document.createElement("div");
      chip.className = `result-outcome ${outcomeToneClass(outcome)}`;
      chip.textContent = humanize(outcome).toUpperCase();
      body.appendChild(chip);
    }

    // Total score
    if (result.total_score !== undefined && result.total_score !== null) {
      const h = document.createElement("h3");
      h.className = "result-total";
      h.textContent = `Total score: ${result.total_score} / 30`;
      body.appendChild(h);
    }

    // Confidence
    if (result.confidence) {
      const conf = document.createElement("div");
      conf.className = "muted small";
      conf.textContent = `Scoring confidence: ${humanize(result.confidence)}`;
      body.appendChild(conf);
    }

    // Domain scores (object: domain_name → int)
    const domains = result.domain_scores;
    if (domains && typeof domains === "object" && !Array.isArray(domains)) {
      const entries = Object.entries(domains);
      if (entries.length) {
        const grid = document.createElement("div");
        grid.className = "domain-grid";
        entries.forEach(([key, score]) => {
          const card = document.createElement("div");
          card.className = "domain-card";

          const name = document.createElement("div");
          name.className = "domain-name";
          name.textContent = humanize(key);

          const s = document.createElement("div");
          s.className = "domain-score";
          s.textContent = `${score} / 5`;

          card.appendChild(name);
          card.appendChild(s);
          grid.appendChild(card);
        });
        body.appendChild(grid);
      }
    }

    // Strengths / gaps / flags — all string arrays
    appendStringList(body, "Strengths", result.strengths);
    appendStringList(body, "Gaps", result.gaps);
    appendStringList(body, "Flags", result.safety_flags || result.flags);

    // Phase summaries (assigned_case only; array of {phase_id, summary})
    if (Array.isArray(result.phase_summaries) && result.phase_summaries.length) {
      const h = document.createElement("h3");
      h.className = "section-title";
      h.textContent = "Phase summaries";
      body.appendChild(h);
      result.phase_summaries.forEach((ps) => {
        const wrap = document.createElement("div");
        wrap.className = "phase-summary";
        const label = document.createElement("div");
        label.className = "domain-name";
        label.textContent = humanize(ps.phase_id);
        const text = document.createElement("div");
        text.className = "phase-summary-text";
        text.textContent = ps.summary || "";
        wrap.appendChild(label);
        wrap.appendChild(text);
        body.appendChild(wrap);
      });
    }

    // Review recommendation
    if (result.review_recommendation) {
      const rec = document.createElement("p");
      rec.className = "muted small review-rec";
      rec.textContent = `Review recommendation: ${humanize(result.review_recommendation)}`;
      body.appendChild(rec);
    }
  }

  function appendStringList(parent, title, items) {
    if (!Array.isArray(items) || !items.length) return;
    const h = document.createElement("h3");
    h.className = "section-title";
    h.textContent = title;
    parent.appendChild(h);
    const ul = document.createElement("ul");
    ul.className = "bullet-list";
    items.forEach((it) => {
      const li = document.createElement("li");
      li.textContent = String(it);
      ul.appendChild(li);
    });
    parent.appendChild(ul);
  }

  // ---- Identify form ---------------------------------------------------

  $("#identify-form")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const val = $("#candidate-input").value.trim();
    if (!val) return;
    state.candidateId = val;
    setChips();
    routeFromUrlOrLauncher();
  });

  // ---- Utility ---------------------------------------------------------

  function escapeHtml(str) {
    if (str === null || str === undefined) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // ---- Routing ---------------------------------------------------------

  function routeFromUrlOrLauncher() {
    if (!state.candidateId) {
      showView("identify");
      return;
    }

    if (urlProduct && ["assigned_case", "case_based", "mastery_module"].includes(urlProduct)) {
      state.productType = urlProduct;
      setChips();

      if (urlProduct === "assigned_case") {
        if (urlCaseId) {
          startSession({ case_id: urlCaseId });
        } else {
          openAssignedCasePicker();
        }
        return;
      }

      if (urlProduct === "mastery_module") {
        if (urlModuleId) {
          startSession({ module_id: urlModuleId, attempt_number: urlAttempt });
        } else {
          openMasteryModulePicker();
        }
        return;
      }

      if (urlProduct === "case_based") {
        openCaseBasedUpload();
        return;
      }
    }

    showView("launcher");
  }

  // ---- Boot ------------------------------------------------------------

  document.addEventListener("DOMContentLoaded", () => {
    bindLauncher();
    setChips();
    routeFromUrlOrLauncher();
  });
})();
