// Folio UI — consumes the engine's SSE stream and updates the DOM.
//
// The pipeline emits one ProgressEvent per stage transition; the extract and
// enrich stages additionally emit one per paper / per compound. We update:
//
//   - the pipeline strip at the top (stage states: pending / active / done)
//   - the papers sidebar (one row per paper, status flips as extraction runs)
//   - the briefing pane (rendered once the pipeline finishes)

(() => {
  const els = {
    form: document.getElementById("query-form"),
    input: document.getElementById("query-input"),
    papersInput: document.getElementById("papers-input"),
    runBtn: document.getElementById("run-btn"),
    examples: document.querySelectorAll(".example"),
    papersSort: document.getElementById("papers-sort"),
    papersFilterType: document.getElementById("papers-filter-type"),
    papersSearch: document.getElementById("papers-search"),
    strip: document.getElementById("pipeline-strip"),
    stages: document.querySelectorAll(".stages li"),
    stageLabel: document.getElementById("stage-label"),
    stageInfo: document.getElementById("stage-info"),
    progressTrack: document.getElementById("progress-track"),
    progressBar: document.getElementById("progress-bar"),
    workspace: document.querySelector(".workspace"),
    papersList: document.getElementById("papers-list"),
    papersCount: document.getElementById("papers-count"),
    briefing: document.getElementById("briefing-render"),
    briefingMeta: document.getElementById("briefing-meta"),
    modal: document.getElementById("paper-modal"),
    modalTitle: document.getElementById("paper-modal-title"),
    modalBody: document.getElementById("paper-modal-body"),
    modalClose: document.getElementById("paper-modal-close"),
    modalBackdrop: document.querySelector(".modal-backdrop"),
    createNoteBtn: document.getElementById("create-note-btn"),
    downloadBtn: document.getElementById("download-btn"),
    downloadMenu: document.getElementById("download-menu"),
    downloadDropdown: document.getElementById("download-dropdown"),
    historyList: document.getElementById("history-list"),
    sidebar: document.getElementById("sidebar"),
    sidebarHome: document.getElementById("sidebar-home"),
    sidebarToggle: document.getElementById("sidebar-toggle"),
    sidebarReopen: document.getElementById("sidebar-reopen"),
    sidebarNew: document.getElementById("sidebar-new"),
    sidebarSearch: document.getElementById("sidebar-search"),
    // Auth UI.
    authPanel: document.getElementById("auth-panel"),
    authAnon: document.getElementById("auth-anon"),
    authUser: document.getElementById("auth-user"),
    authUserEmail: document.getElementById("auth-user-email"),
    signinBtn: document.getElementById("signin-btn"),
    signoutBtn: document.getElementById("signout-btn"),
    authModal: document.getElementById("auth-modal"),
    authModalBackdrop: document.getElementById("auth-modal-backdrop"),
    authModalClose: document.getElementById("auth-modal-close"),
    authModalTitle: document.getElementById("auth-modal-title"),
    authForm: document.getElementById("auth-form"),
    authEmail: document.getElementById("auth-email"),
    authPassword: document.getElementById("auth-password"),
    authSubmit: document.getElementById("auth-submit"),
    authToggle: document.getElementById("auth-toggle"),
    authError: document.getElementById("auth-error"),
    folioMe: document.getElementById("folio-me"),
    banner: document.getElementById("banner"),
    bannerText: document.getElementById("banner-text"),
    bannerClear: document.getElementById("banner-clear"),
    notesSection: document.getElementById("notes-section"),
    notesInput: document.getElementById("notes-input"),
    notesStatus: document.getElementById("notes-status"),
  };

  // Notes autosave state: a debounce timer + the briefing_id currently in
  // scope. The notes textarea silently no-ops if there's no briefing_id
  // (i.e. Supabase persistence is disabled).
  let notesTimer = null;
  const NOTES_DEBOUNCE_MS = 800;

  // --- Auth state + helpers ---------------------------------------------
  // Stored in localStorage so the session survives a refresh. Supabase
  // tokens are short-lived (1h); we don't refresh in this build — when a
  // token expires the user will be quietly signed out and re-prompted.
  const AUTH_STORAGE_KEY = "folio.auth.v1";
  let authState = loadAuthState();
  let authMode = "signin"; // "signin" | "signup"

  function loadAuthState() {
    try {
      const raw = localStorage.getItem(AUTH_STORAGE_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (parsed && parsed.access_token && parsed.email) return parsed;
    } catch (_) { /* ignore */ }
    return null;
  }
  function saveAuthState(state) {
    if (state) {
      localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(state));
    } else {
      localStorage.removeItem(AUTH_STORAGE_KEY);
    }
    authState = state;
  }
  function authHeaders() {
    // Returns headers to merge into our backend fetches. Empty when anon.
    if (authState && authState.access_token) {
      return { Authorization: `Bearer ${authState.access_token}` };
    }
    return {};
  }
  // Wrap fetch so every call to our backend auto-includes the auth header
  // without each call-site having to remember.
  function authedFetch(input, init = {}) {
    const headers = { ...(init.headers || {}), ...authHeaders() };
    return fetch(input, { ...init, headers });
  }

  let currentBriefingId = null;
  // Last rendered briefing markdown + the query that produced it. Drives the
  // Download menu, which is also enabled for past briefings.
  let currentMarkdown = "";
  let currentQuery = "";
  // Track when the extract phase began so we can compute an ETA from the
  // per-paper completion rate (which differs from total run elapsed).
  let extractStartedAt = 0;
  let stripHideTimer = null;

  // State scoped to one run.
  let evtSource = null;
  let papers = new Map(); // pmid -> { paperData, extraction, status }
  let runStartedAt = 0;

  function reset() {
    papers = new Map();
    runStartedAt = Date.now();
    extractStartedAt = 0;
    if (stripHideTimer) { clearTimeout(stripHideTimer); stripHideTimer = null; }
    // Hide notes section until a briefing is finished; clear any prior content
    // so a previous query's notes don't leak into a new run.
    if (els.notesSection) els.notesSection.hidden = true;
    if (els.notesInput) els.notesInput.value = "";
    if (els.notesStatus) els.notesStatus.textContent = "";
    if (notesTimer) { clearTimeout(notesTimer); notesTimer = null; }
    // Restore the loading placeholder (replaces any prior briefing markup).
    renderPlaceholder("Starting the search…");
    els.papersList.innerHTML = "";
    els.papersCount.textContent = "";
    // Reset filter inputs so a fresh run isn't unexpectedly empty.
    if (els.papersSort) els.papersSort.value = "relevance";
    if (els.papersFilterType) els.papersFilterType.value = "all";
    if (els.papersSearch) els.papersSearch.value = "";
    els.briefing.innerHTML =
      '<p class="muted placeholder">Pipeline running… papers will fill in as they are extracted.</p>';
    els.briefingMeta.textContent = "";
    els.stageLabel.textContent = "Starting…";
    els.stageInfo.textContent = "";
    els.progressBar.style.width = "0%";
    els.progressTrack.hidden = true;
    els.stages.forEach((s) => s.classList.remove("active", "done", "error"));
    els.strip.hidden = false;
    els.workspace.hidden = false;
  }

  function setStage(name, status /* active | done | error */) {
    els.stages.forEach((s) => {
      if (s.dataset.stage === name) {
        s.classList.remove("active", "done", "error");
        s.classList.add(status);
      }
    });
  }

  function markPreviousDone(upTo) {
    const order = ["plan", "search", "retrieve", "extract", "synthesise", "enrich", "done"];
    const cutoff = order.indexOf(upTo);
    if (cutoff < 0) return;
    els.stages.forEach((s) => {
      const idx = order.indexOf(s.dataset.stage);
      if (idx < cutoff && !s.classList.contains("error")) {
        s.classList.remove("active");
        s.classList.add("done");
      }
    });
  }

  function renderPapersList(papersData) {
    els.papersList.innerHTML = "";
    papersData.forEach((p, idx) => {
      papers.set(p.pmid, {
        paperData: p,
        extraction: null,
        status: "queued",
        searchRank: idx,  // original PubMed-relevance position; survives sort
      });
      const li = document.createElement("li");
      li.className = "paper status-queued";
      li.dataset.pmid = p.pmid;
      li.dataset.searchRank = String(idx);
      li.innerHTML = `
        <div class="paper-statusline">
          <span class="paper-status">${idx + 1}. queued</span>
          <a class="paper-pmid"
             href="https://pubmed.ncbi.nlm.nih.gov/${escapeHtml(p.pmid)}/"
             target="_blank" rel="noopener"
             title="Open in PubMed">PMID ${escapeHtml(p.pmid)}</a>
        </div>
        <div class="paper-title"></div>
        <div class="paper-meta"></div>
      `;
      li.querySelector(".paper-title").textContent = p.title || "(untitled)";
      const meta = [p.journal, p.year].filter(Boolean).join(" · ") || "—";
      li.querySelector(".paper-meta").textContent = meta;
      // Open the modal on click anywhere — except the PMID link, which goes
      // straight to PubMed in a new tab.
      li.addEventListener("click", (e) => {
        if (e.target.closest(".paper-pmid")) return;
        openPaperModal(p.pmid);
      });
      els.papersList.appendChild(li);
    });
    els.papersCount.textContent = `${papersData.length} retrieved`;
  }

  function updatePaper(pmid, status, extraction, indexInOrder) {
    const entry = papers.get(pmid);
    if (!entry) return;
    entry.status = status;
    entry.extraction = extraction;
    const li = els.papersList.querySelector(`li.paper[data-pmid="${pmid}"]`);
    if (!li) return;
    li.classList.remove("status-queued", "status-extracting", "status-done", "status-skipped");
    li.classList.add(`status-${status}`);
    const statusEl = li.querySelector(".paper-status");
    const numberPrefix = li.querySelector(".paper-status").textContent.split(".")[0];
    let label;
    switch (status) {
      case "extracting": label = "extracting…"; break;
      case "done": label = "processed ✓"; break;
      case "skipped": label = "skipped"; break;
      default: label = status;
    }
    statusEl.textContent = `${numberPrefix}. ${label}`;
    if (extraction && status === "done") {
      let extractionEl = li.querySelector(".paper-extraction");
      if (!extractionEl) {
        extractionEl = document.createElement("div");
        extractionEl.className = "paper-extraction";
        li.appendChild(extractionEl);
      }
      const compounds = (extraction.compounds || []).slice(0, 4).join(", ");
      const score = typeof extraction.relevance_score === "number"
        ? extraction.relevance_score.toFixed(2)
        : "—";
      const variant = extraction.variant_or_mutation
        ? ` · <em>${escapeHtml(extraction.variant_or_mutation)}</em>`
        : "";
      extractionEl.innerHTML =
        `<strong>${escapeHtml(compounds || "no compounds found")}</strong>` +
        `<span class="relevance">${score}</span>` +
        variant;
    }
  }

  // Back-compat name used by past-briefing render and extract-done event.
  function sortPapersByRelevance() {
    if (els.papersSort) els.papersSort.value = "relevance";
    applyPaperView();
  }

  function applyPaperView() {
    // Single function for sort + filter. Called on filter/sort change and
    // after the extract phase completes (extractions become available).
    const sortKey = els.papersSort ? els.papersSort.value : "relevance";
    const typeFilter = els.papersFilterType ? els.papersFilterType.value : "all";
    const searchRaw = els.papersSearch ? els.papersSearch.value : "";
    const searchTokens = searchRaw
      .toLowerCase()
      .split(/\s+/)
      .filter(Boolean);

    const items = Array.from(els.papersList.querySelectorAll("li.paper"));

    // --- sort ---
    items.sort((a, b) => sortCompare(a, b, sortKey));

    // --- re-append in new order + renumber visible papers ---
    let visibleIndex = 0;
    items.forEach((item) => {
      els.papersList.appendChild(item);
      const entry = papers.get(item.dataset.pmid);
      const match = entryMatchesFilters(entry, typeFilter, searchTokens);
      item.style.display = match ? "" : "none";
      if (match) {
        visibleIndex += 1;
        const statusEl = item.querySelector(".paper-status");
        if (statusEl) {
          const rest = statusEl.textContent.split(". ").slice(1).join(". ") || "";
          statusEl.textContent = `${visibleIndex}. ${rest}`;
        }
      }
    });

    // Update the count line so the user knows the filter is doing something.
    const total = items.length;
    if (visibleIndex < total) {
      els.papersCount.textContent = `${visibleIndex} of ${total} shown`;
    } else if (total) {
      els.papersCount.textContent = `${total} retrieved`;
    }
  }

  function sortCompare(aLi, bLi, key) {
    const ea = papers.get(aLi.dataset.pmid);
    const eb = papers.get(bLi.dataset.pmid);
    switch (key) {
      case "year-desc":
      case "year-asc": {
        const ya = parseInt((ea && ea.paperData && ea.paperData.year) || "0", 10) || 0;
        const yb = parseInt((eb && eb.paperData && eb.paperData.year) || "0", 10) || 0;
        return key === "year-desc" ? yb - ya : ya - yb;
      }
      case "rank": {
        const ra = (ea && Number.isFinite(ea.searchRank)) ? ea.searchRank :
          parseInt(aLi.dataset.searchRank || "0", 10);
        const rb = (eb && Number.isFinite(eb.searchRank)) ? eb.searchRank :
          parseInt(bLi.dataset.searchRank || "0", 10);
        return ra - rb;
      }
      case "relevance":
      default: {
        const sa = (ea && ea.extraction && typeof ea.extraction.relevance_score === "number")
          ? ea.extraction.relevance_score : -1;
        const sb = (eb && eb.extraction && typeof eb.extraction.relevance_score === "number")
          ? eb.extraction.relevance_score : -1;
        return sb - sa;
      }
    }
  }

  function entryMatchesFilters(entry, typeFilter, searchTokens) {
    if (!entry) return true;

    if (typeFilter && typeFilter !== "all") {
      const st = (entry.extraction && entry.extraction.study_type) || "";
      // Papers without extraction yet stay visible; once extracted, they
      // must match the requested type.
      if (entry.extraction && st.toLowerCase() !== typeFilter) return false;
    }

    if (searchTokens.length === 0) return true;
    const haystack = [
      entry.paperData && entry.paperData.title,
      entry.paperData && entry.paperData.abstract,
      entry.paperData && entry.paperData.journal,
      entry.paperData && entry.paperData.pmid,
      entry.extraction && (entry.extraction.compounds || []).join(" "),
      entry.extraction && entry.extraction.mechanism_of_action,
      entry.extraction && entry.extraction.key_finding,
      entry.extraction && entry.extraction.variant_or_mutation,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return searchTokens.every((t) => haystack.includes(t));
  }

  function openPaperModal(pmid) {
    const entry = papers.get(pmid);
    if (!entry) return;
    const { paperData, extraction } = entry;
    els.modalTitle.textContent = paperData.title || "Paper";
    const fields = [];
    fields.push(field("Authors", (paperData.authors || []).join(", ") || "—"));
    fields.push(field("Journal", [paperData.journal, paperData.year].filter(Boolean).join(" · ") || "—"));
    fields.push(field("PubMed",
      paperData.url ? `<a href="${paperData.url}" target="_blank" rel="noopener">${paperData.url}</a>` : "—"));
    fields.push(field("Abstract", `<div class="abstract">${escapeHtml(paperData.abstract || "(no abstract)")}</div>`));
    if (extraction) {
      const chips = (extraction.compounds || [])
        .map((c) => `<span class="chip">${escapeHtml(c)}</span>`)
        .join(" ");
      fields.push(field("Extracted compounds", chips ? `<div class="compounds">${chips}</div>` : "—"));
      fields.push(field("Mechanism", extraction.mechanism_of_action || "—"));
      fields.push(field("Key finding", extraction.key_finding || "—"));
      if (extraction.variant_or_mutation) fields.push(field("Variant", extraction.variant_or_mutation));
      if (extraction.potency) fields.push(field("Potency", extraction.potency));
      if (extraction.selectivity) fields.push(field("Selectivity", extraction.selectivity));
      if (extraction.resistance_mechanism) fields.push(field("Resistance", extraction.resistance_mechanism));
      if (extraction.study_type) fields.push(field("Study type", extraction.study_type));
      fields.push(field("Relevance to query", relevanceLabel(extraction.relevance_score)));
    }
    els.modalBody.innerHTML = fields.join("");
    els.modal.hidden = false;
  }

  function closeModal() {
    els.modal.hidden = true;
  }

  function field(label, value) {
    return `<div class="field"><div class="field-label">${label}</div><div>${value}</div></div>`;
  }

  function relevanceLabel(score) {
    // Mirrors the rubric in engine/prompts.py _EXTRACT_SYSTEM. Shown as a
    // descriptive label in the modal; the raw decimal stays in the sidebar
    // chip for compactness.
    if (typeof score !== "number" || isNaN(score)) return "—";
    let label;
    if (score >= 0.85) label = "Directly addresses query";
    else if (score >= 0.55) label = "Closely related";
    else if (score >= 0.25) label = "Tangentially related";
    else label = "Off-topic";
    return `${label} <span class="muted">(${score.toFixed(2)})</span>`;
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderPlaceholder(subline) {
    // Restore the loading placeholder in the briefing pane and update the
    // human-readable subline below the headline.
    els.briefing.innerHTML = `
      <div class="briefing-placeholder">
        <div class="placeholder-headline">Preparing your briefing</div>
        <div id="placeholder-subline" class="placeholder-subline"></div>
        <div class="placeholder-skeleton" aria-hidden="true">
          <div class="sk-block sk-h"></div>
          <div class="sk-block sk-line"></div>
          <div class="sk-block sk-line sk-line-3q"></div>
          <div class="sk-block sk-line sk-line-half"></div>
          <div class="sk-block sk-h sk-h-2"></div>
          <div class="sk-block sk-line"></div>
          <div class="sk-block sk-line sk-line-3q"></div>
        </div>
      </div>
    `;
    const sub = document.getElementById("placeholder-subline");
    if (sub) sub.textContent = subline || "";
  }

  function setPlaceholderSubline(text) {
    const sub = document.getElementById("placeholder-subline");
    if (sub) sub.textContent = text || "";
  }

  function setLabel(text) {
    els.stageLabel.textContent = text;
  }

  function setInfo(text) {
    els.stageInfo.textContent = text || "";
  }

  function setProgress(pct) {
    // pct is 0-100; null hides the bar.
    if (pct == null) {
      els.progressTrack.hidden = true;
      els.progressBar.style.width = "0%";
    } else {
      els.progressTrack.hidden = false;
      els.progressBar.style.width = `${Math.max(0, Math.min(100, pct))}%`;
    }
  }

  function handleEvent(payload) {
    const { stage, message, detail } = payload;

    if (stage === "plan") {
      setStage("plan", "active");
      setLabel("Planning search…");
      setInfo(detail && detail.search_terms
        ? detail.search_terms.slice(0, 4).join(" · ")
        : "");
      setProgress(null);
      setPlaceholderSubline("Designing the search strategy…");
      return;
    }
    if (stage === "search") {
      markPreviousDone("search");
      setStage("search", "active");
      setLabel("Searching PubMed…");
      setInfo(detail && typeof detail.pmid_count === "number"
        ? `${detail.pmid_count} hits` : "");
      setProgress(null);
      setPlaceholderSubline("Searching PubMed for relevant papers…");
      return;
    }
    if (stage === "retrieve") {
      markPreviousDone("retrieve");
      setStage("retrieve", "active");
      setLabel("Fetching abstracts…");
      if (detail && Array.isArray(detail.papers)) {
        renderPapersList(detail.papers);
        setInfo(`${detail.papers.length} papers`);
        setPlaceholderSubline(`Reading ${detail.papers.length} abstracts…`);
      } else {
        setInfo("");
        setPlaceholderSubline("Loading abstracts…");
      }
      setProgress(null);
      return;
    }
    if (stage === "extract") {
      markPreviousDone("extract");
      setStage("extract", "active");
      setLabel("Extracting per paper…");
      if (detail && detail.pmid) {
        if (!extractStartedAt) extractStartedAt = Date.now();
        const status = detail.ok ? "done" : "skipped";
        updatePaper(detail.pmid, status, detail.extraction, detail.completed);
        const pct = detail.total ? (detail.completed / detail.total) * 100 : 0;
        setProgress(pct);
        // ETA based on per-paper rate over the extract phase only.
        const elapsedExtract = (Date.now() - extractStartedAt) / 1000;
        const rate = detail.completed > 0 ? elapsedExtract / detail.completed : 0;
        const remaining = Math.max(0, detail.total - detail.completed);
        const eta = Math.max(0, Math.ceil(remaining * rate));
        const etaText = remaining > 0 ? ` · ~${eta}s left` : "";
        setInfo(`${detail.completed}/${detail.total} · ${Math.round(elapsedExtract)}s${etaText}`);
        setPlaceholderSubline(`Extracting compounds and mechanisms · ${detail.completed} of ${detail.total} papers${etaText}`);
      } else if (detail && typeof detail.extraction_count === "number") {
        // The "Extracted N of N papers" summary event.
        setProgress(100);
        setInfo(`${detail.extraction_count} extracted`);
        setPlaceholderSubline(`${detail.extraction_count} papers ready for synthesis`);
        sortPapersByRelevance();
      } else {
        setInfo("");
      }
      return;
    }
    if (stage === "synthesise") {
      markPreviousDone("synthesise");
      setStage("synthesise", "active");
      setLabel("Writing briefing…");
      setProgress(null);
      setInfo("");
      setPlaceholderSubline("Writing the briefing — ranking compounds, surfacing contradictions…");
      return;
    }
    if (stage === "enrich") {
      markPreviousDone("enrich");
      setStage("enrich", "active");
      setLabel("Checking ClinicalTrials.gov…");
      setProgress(null);
      if (detail && detail.compound) {
        setInfo(detail.compound);
        setPlaceholderSubline(`Cross-referencing clinical trial status · ${detail.compound}`);
      } else {
        setPlaceholderSubline("Cross-referencing ClinicalTrials.gov…");
      }
      return;
    }
    if (stage === "done") {
      markPreviousDone("done");
      setStage("done", "done");
      const elapsed = ((Date.now() - runStartedAt) / 1000).toFixed(1);
      setLabel("Briefing ready");
      setProgress(100);
      setInfo(`${elapsed}s total`);
      els.briefingMeta.textContent = `Ready · ${elapsed}s`;
      return;
    }
    if (stage === "render") {
      const md = (detail && detail.markdown) || "";
      renderBriefing(md);
      if (detail && detail.briefing_id) {
        currentBriefingId = detail.briefing_id;
        history.replaceState({}, "", `/?briefing=${detail.briefing_id}`);
      }
      // Notes section is only meaningful once a briefing exists *and* we
      // have a briefing_id to save against (history must be enabled).
      revealNotes("");
      // Briefing is on screen — the pipeline strip is no longer useful.
      // Hide it after a short delay so the user gets to see the "all stages
      // green" final state once.
      if (stripHideTimer) clearTimeout(stripHideTimer);
      stripHideTimer = setTimeout(() => { els.strip.hidden = true; }, 1800);
      finishRun();
      return;
    }
    if (stage === "error") {
      setStage("done", "error");
      setLabel("Pipeline error");
      setInfo(message || "see console");
      setProgress(null);
      finishRun();
      return;
    }
  }

  function renderBriefing(markdown) {
    currentMarkdown = markdown || "";
    els.downloadBtn.disabled = !currentMarkdown;
    if (els.createNoteBtn) els.createNoteBtn.disabled = !currentMarkdown;
    if (!markdown) {
      els.briefing.innerHTML =
        '<p class="muted placeholder">The model returned no briefing markdown.</p>';
      return;
    }
    if (typeof marked === "undefined") {
      // marked.js failed to load (offline?). Fall back to <pre>.
      const pre = document.createElement("pre");
      pre.textContent = markdown;
      els.briefing.innerHTML = "";
      els.briefing.appendChild(pre);
      return;
    }
    marked.setOptions({ breaks: true, gfm: true });
    els.briefing.innerHTML = marked.parse(markdown);
    // Open all rendered links in a new tab so the user does not lose the run.
    els.briefing.querySelectorAll("a").forEach((a) => {
      a.target = "_blank";
      a.rel = "noopener";
    });
    // Upgrade PubMed links to Folio f-pmid styling: mono digits, dim "PMID"
    // prefix, branded blue underline. Done in JS so the underlying Markdown
    // stays clean for the .md download path.
    els.briefing.querySelectorAll('a[href*="pubmed.ncbi.nlm.nih.gov"]').forEach((a) => {
      a.classList.add("f-pmid");
      const m = a.textContent.match(/^\s*PMID\s+(\d+)\s*$/i);
      if (m) {
        a.innerHTML = `<span class="pre">PMID</span>${m[1]}`;
      }
    });
    injectPrintCover();
  }

  function injectPrintCover() {
    // Insert (or refresh) the print-only cover block at the top of
    // .briefing-render. Hidden on screen via CSS, visible in print/PDF.
    let cover = els.briefing.querySelector(".print-cover");
    if (!cover) {
      cover = document.createElement("section");
      cover.className = "print-cover";
      els.briefing.insertBefore(cover, els.briefing.firstChild);
    }
    const dateStr = new Date().toLocaleDateString(undefined, {
      year: "numeric", month: "long", day: "numeric",
    });
    const paperCount = papers.size;
    const extractionCount = Array.from(papers.values()).filter(
      (e) => e.status === "done" || (e.extraction && e.extraction.compounds)
    ).length;
    cover.innerHTML = `
      <img class="cover-logo" src="/static/logo-tagline.png"
           alt="Folio — science summarized, decisions accelerated"
           onerror="this.onerror=null; this.src='/static/logo.png'; this.onerror=()=>this.style.display='none';" />
      <div class="eyebrow">Folio · Drug-discovery research briefing</div>
      <h1 class="cover-title">Research Briefing</h1>
      <div class="cover-subtitle">${escapeHtml(currentQuery || "Untitled query")}</div>
      <div class="cover-meta">
        <span><strong>Generated:</strong> ${dateStr}</span>
        <span><strong>Source:</strong> PubMed + ClinicalTrials.gov</span>
        <span><strong>Papers analysed:</strong> ${paperCount}${
          extractionCount && extractionCount !== paperCount
            ? ` (${extractionCount} extracted)`
            : ""
        }</span>
        <span><strong>Engine:</strong> Folio on Featherless</span>
      </div>
    `;
  }

  function finishRun() {
    if (evtSource) {
      evtSource.close();
      evtSource = null;
    }
    els.runBtn.disabled = false;
    els.runBtn.textContent = "Brief me";
  }

  function startRun(query) {
    if (!query) return;
    if (evtSource) {
      evtSource.close();
    }
    reset();
    // Switch the page out of the centered-hero state into the workspace
    // state. `has-run` collapses the query bar to a compact top-pinned
    // form and reveals the briefing layout.
    document.body.classList.add("has-run");
    currentQuery = query;
    currentMarkdown = "";
    els.downloadBtn.disabled = true;
    if (els.createNoteBtn) els.createNoteBtn.disabled = true;
    els.runBtn.disabled = true;
    els.runBtn.textContent = "Running…";

    // Clamp to [5, 50] on the client to match the server-side clamp.
    let papersCount = parseInt(els.papersInput.value, 10);
    if (!Number.isFinite(papersCount)) papersCount = 25;
    papersCount = Math.max(5, Math.min(50, papersCount));
    els.papersInput.value = String(papersCount);

    let url = `/api/run?query=${encodeURIComponent(query)}&papers=${papersCount}`;
    if (authState && authState.access_token) {
      // EventSource can't set headers; pass the JWT as a query param so the
      // backend can stamp the briefing with the right user_id.
      url += `&token=${encodeURIComponent(authState.access_token)}`;
    }
    evtSource = new EventSource(url);
    evtSource.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        handleEvent(data);
      } catch (err) {
        console.error("Could not parse SSE payload", err, e.data);
      }
    };
    evtSource.onerror = () => {
      // EventSource will auto-reconnect on transient errors. Only act once
      // the stream has terminated; in that case the server has either
      // finished or hard-failed, and we just stop the UI.
      if (evtSource && evtSource.readyState === EventSource.CLOSED) {
        finishRun();
      }
    };
  }

  // --- History drawer + load-past-briefing -----------------------------

  // Sidebar recents cache for client-side filtering by the sidebar search.
  let _historyRows = [];

  async function loadHistory() {
    try {
      const resp = await authedFetch("/api/history");
      const rows = await resp.json();
      _historyRows = Array.isArray(rows) ? rows : [];
      renderHistoryList(_historyRows);
    } catch (err) {
      console.error("Failed to load history", err);
      els.historyList.innerHTML =
        '<p class="muted placeholder">Could not load history.</p>';
    }
  }

  function renderHistoryList(rows) {
    if (!rows || rows.length === 0) {
      els.historyList.innerHTML =
        '<p class="muted placeholder">No past briefings yet. Run a query to start building history.</p>';
      return;
    }
    const items = rows.map((r) => {
      const when = r.created_at ? new Date(r.created_at) : null;
      const ago = when ? relativeTime(when) : "";
      const papers = r.paper_count ? `${r.paper_count}p` : "";
      const elapsed = r.elapsed_seconds
        ? `${r.elapsed_seconds.toFixed(1)}s`
        : "";
      const status = (r.status || "ok").toLowerCase();
      const cls = r.id === currentBriefingId
        ? "history-item f-brief-item is-active"
        : "history-item f-brief-item";
      // Folio f-brief-item structure: a .title with the query, plus a .meta
      // line of mono chips (ago / paper count / runtime / status).
      const metaParts = [];
      if (ago) metaParts.push(`<span>${escapeHtml(ago)}</span>`);
      if (papers) metaParts.push(`<span>${escapeHtml(papers)}</span>`);
      if (elapsed) metaParts.push(`<span>${escapeHtml(elapsed)}</span>`);
      metaParts.push(
        status === "ok"
          ? `<span class="ok">ok</span>`
          : `<span>${escapeHtml(status)}</span>`
      );
      return `
        <button class="${cls}" data-id="${r.id}">
          <div class="title">${escapeHtml(r.query)}</div>
          <div class="meta">${metaParts.join("")}</div>
        </button>
      `;
    });
    els.historyList.innerHTML = items.join("");
    els.historyList.querySelectorAll(".history-item").forEach((b) => {
      b.addEventListener("click", () => {
        loadPastBriefing(b.dataset.id);
      });
    });
  }

  function filterRecents(query) {
    const q = (query || "").toLowerCase().trim();
    if (!q) {
      renderHistoryList(_historyRows);
      return;
    }
    const matched = _historyRows.filter((r) =>
      (r.query || "").toLowerCase().includes(q) ||
      (r.summary || "").toLowerCase().includes(q)
    );
    renderHistoryList(matched);
  }

  function relativeTime(date) {
    const ms = Date.now() - date.getTime();
    const s = Math.round(ms / 1000);
    if (s < 60) return `${s}s ago`;
    const m = Math.round(s / 60);
    if (m < 60) return `${m}m ago`;
    const h = Math.round(m / 60);
    if (h < 24) return `${h}h ago`;
    const d = Math.round(h / 24);
    return `${d}d ago`;
  }

  async function loadPastBriefing(briefingId) {
    try {
      const resp = await authedFetch(`/api/briefing/${briefingId}`);
      if (!resp.ok) {
        throw new Error(`status ${resp.status}`);
      }
      const data = await resp.json();
      // Set the id *before* render so revealNotes() (called inside
      // renderPastBriefing) sees a valid briefing id and actually unhides
      // the textarea with the saved notes content.
      currentBriefingId = briefingId;
      renderPastBriefing(data);
      history.replaceState({}, "", `/?briefing=${briefingId}`);
    } catch (err) {
      console.error("Could not load briefing", err);
      alert("Could not load that briefing.");
    }
  }

  function renderPastBriefing({ briefing, papers }) {
    if (evtSource) {
      evtSource.close();
      evtSource = null;
    }
    if (stripHideTimer) { clearTimeout(stripHideTimer); stripHideTimer = null; }
    // Past briefings are also a "workspace" state — collapse the hero.
    document.body.classList.add("has-run");
    // Reset state to show the past briefing instead of a live run.
    papers = papers || [];
    els.strip.hidden = true;
    els.workspace.hidden = false;
    els.papersList.innerHTML = "";
    els.papersCount.textContent = "";
    els.runBtn.disabled = false;
    els.runBtn.textContent = "Brief me";

    // Banner indicating this is a past run.
    const when = briefing.created_at ? new Date(briefing.created_at) : null;
    const ago = when ? relativeTime(when) : "earlier";
    els.bannerText.textContent = `Viewing past briefing: "${briefing.query}" - created ${ago}`;
    els.banner.hidden = false;

    // Rebuild the papers list (re-using the live-run helpers).
    const paperData = papers.map((p) => ({
      pmid: p.pmid,
      title: p.title,
      abstract: p.abstract,
      authors: Array.isArray(p.authors) ? p.authors : [],
      journal: p.journal,
      year: p.year,
      url: `https://pubmed.ncbi.nlm.nih.gov/${p.pmid}/`,
    }));
    renderPapersList(paperData);
    // Mark every paper as already extracted, surface the saved extraction.
    papers.forEach((p) => {
      if (p.extraction) {
        updatePaper(p.pmid, "done", p.extraction);
      } else {
        updatePaper(p.pmid, "skipped", null);
      }
    });
    // Past briefings should also show the relevance ordering.
    sortPapersByRelevance();

    // Briefing markdown.
    renderBriefing(briefing.briefing_markdown || "");
    const elapsed = briefing.elapsed_seconds
      ? ` · ${briefing.elapsed_seconds.toFixed(1)}s`
      : "";
    els.briefingMeta.textContent = `from history${elapsed}`;

    // Reflect the query back into the input so the user can refine + re-run.
    els.input.value = briefing.query || "";
    currentQuery = briefing.query || "";
    // Surface any saved notes for this past briefing.
    revealNotes(typeof briefing.notes === "string" ? briefing.notes : "");
  }

  function revealNotes(text) {
    // Only show the notes UI when we have a briefing_id (history must be
    // enabled by Supabase). Otherwise hide it -- there's nowhere to save to.
    if (!currentBriefingId || !els.notesSection || !els.notesInput) return;
    els.notesInput.value = text || "";
    els.notesStatus.textContent = text ? "Saved" : "";
    els.notesSection.hidden = false;
  }

  async function saveNotes() {
    if (!currentBriefingId) return;
    const body = JSON.stringify({ notes: els.notesInput.value });
    els.notesStatus.textContent = "Saving…";
    try {
      const resp = await authedFetch(
        `/api/briefing/${currentBriefingId}/notes`,
        { method: "PATCH", headers: { "Content-Type": "application/json" }, body }
      );
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      els.notesStatus.textContent = "Saved";
    } catch (err) {
      console.error("notes save failed", err);
      els.notesStatus.textContent = "Save failed";
    }
  }

  async function savePdf() {
    if (!currentMarkdown) return;
    // Preferred path: server-side render via WeasyPrint. Bypasses the
    // browser's "Margins" override entirely so the PDF looks the same on
    // every machine. Needs `currentBriefingId` (history must be enabled
    // and the briefing must have persisted to Supabase).
    if (currentBriefingId) {
      try {
        const resp = await authedFetch(`/api/briefing/${currentBriefingId}/pdf`);
        if (resp.ok) {
          const blob = await resp.blob();
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          const slug = (currentQuery || "briefing")
            .toLowerCase().replace(/[^a-z0-9]+/g, "-")
            .replace(/^-+|-+$/g, "").slice(0, 60) || "briefing";
          a.download = `folio-${slug}.pdf`;
          document.body.appendChild(a); a.click();
          document.body.removeChild(a);
          URL.revokeObjectURL(url);
          return;
        }
        // Server returned non-2xx — fall through to browser print.
        console.warn("Server PDF unavailable (status", resp.status, "); falling back to browser print.");
      } catch (err) {
        console.warn("Server PDF failed:", err, "falling back to browser print.");
      }
    }
    // Fallback: browser print dialog (works when no briefing_id / Supabase /
    // weasyprint unavailable). The user has to pick "Save as PDF".
    const slug = (currentQuery || "briefing")
      .replace(/\s+/g, " ").trim().slice(0, 80) || "briefing";
    const originalTitle = document.title;
    document.title = `Folio — ${slug}`;
    try {
      window.print();
    } finally {
      setTimeout(() => { document.title = originalTitle; }, 1000);
    }
  }

  function downloadMarkdown() {
    if (!currentMarkdown) return;
    const slug = (currentQuery || "briefing")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 60) || "briefing";
    const blob = new Blob([currentMarkdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `folio-${slug}.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  function clearBanner() {
    els.banner.hidden = true;
    history.replaceState({}, "", "/");
    // Returning to the hero state: hide workspace + strip + notes, drop the
    // body class so the centered query bar comes back, reset state.
    document.body.classList.remove("has-run");
    els.workspace.hidden = true;
    els.strip.hidden = true;
    if (els.notesSection) els.notesSection.hidden = true;
    if (els.notesInput) els.notesInput.value = "";
    currentBriefingId = null;
    currentMarkdown = "";
    currentQuery = "";
  }

  // Auto-load a briefing if the URL is /?briefing=<uuid>
  function loadFromUrlIfAny() {
    const params = new URLSearchParams(window.location.search);
    const bid = params.get("briefing");
    if (bid) loadPastBriefing(bid);
  }

  // Wire up UI events.
  els.form.addEventListener("submit", (e) => {
    e.preventDefault();
    clearBanner();
    startRun(els.input.value.trim());
  });
  els.examples.forEach((btn) => {
    btn.addEventListener("click", () => {
      // Chip labels are shorthand (e.g. "EGFR · NSCLC"); the real query
      // text lives in data-query. Fall back to the visible label for
      // chips that don't set the attribute.
      const q = btn.dataset.query || btn.textContent.trim();
      els.input.value = q;
      clearBanner();
      startRun(q);
    });
  });
  els.modalClose.addEventListener("click", closeModal);
  els.modalBackdrop.addEventListener("click", closeModal);

  // Notes autosave (debounced) + immediate flush on blur for safety.
  if (els.notesInput) {
    els.notesInput.addEventListener("input", () => {
      els.notesStatus.textContent = "Editing…";
      if (notesTimer) clearTimeout(notesTimer);
      notesTimer = setTimeout(saveNotes, NOTES_DEBOUNCE_MS);
    });
    els.notesInput.addEventListener("blur", () => {
      if (notesTimer) { clearTimeout(notesTimer); notesTimer = null; }
      saveNotes();
    });
  }

  // Papers toolbar: re-sort / re-filter on any change. Debounce the search
  // input so typing doesn't fight the DOM updates.
  if (els.papersSort) els.papersSort.addEventListener("change", applyPaperView);
  if (els.papersFilterType) els.papersFilterType.addEventListener("change", applyPaperView);
  let searchTimer = null;
  if (els.papersSearch) {
    els.papersSearch.addEventListener("input", () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(applyPaperView, 120);
    });
  }
  // Download dropdown.
  function openDownloadMenu() {
    els.downloadMenu.hidden = false;
    els.downloadBtn.setAttribute("aria-expanded", "true");
  }
  function closeDownloadMenu() {
    els.downloadMenu.hidden = true;
    els.downloadBtn.setAttribute("aria-expanded", "false");
  }
  // Create-note button: scroll the notes scratchpad into view and focus it.
  // The notes textarea is at the bottom of the briefing pane; if it's hidden
  // (storage disabled / no briefing yet), unhide it first so the focus lands.
  if (els.createNoteBtn) {
    els.createNoteBtn.addEventListener("click", () => {
      if (!els.notesSection || !els.notesInput) return;
      if (els.notesSection.hidden) els.notesSection.hidden = false;
      els.notesSection.scrollIntoView({ behavior: "smooth", block: "start" });
      // Focus after the scroll kicks off so the textarea is keyboard-ready.
      setTimeout(() => els.notesInput.focus(), 150);
    });
  }

  els.downloadBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (els.downloadMenu.hidden) openDownloadMenu();
    else closeDownloadMenu();
  });
  els.downloadMenu.querySelectorAll("button[data-format]").forEach((b) => {
    b.addEventListener("click", () => {
      closeDownloadMenu();
      if (b.dataset.format === "md") downloadMarkdown();
      else if (b.dataset.format === "pdf") savePdf();
    });
  });
  // Click anywhere else closes the menu.
  document.addEventListener("click", (e) => {
    if (els.downloadMenu.hidden) return;
    if (!els.downloadDropdown || els.downloadDropdown.contains(e.target)) return;
    closeDownloadMenu();
  });
  // Sidebar collapse via folio .f-collapse chevron(s). Toggles
  // .is-side-collapsed (or .is-papers-collapsed) on .f-shell and persists
  // the preference in localStorage so reloads keep the user's layout.
  const shell = document.getElementById("f-shell");
  if (shell) {
    document.querySelectorAll(".f-collapse").forEach((btn) => {
      const which = btn.dataset.toggle;       // "side" | "papers"
      const key = `folio:${which}-collapsed`;
      const cls = `is-${which}-collapsed`;
      if (localStorage.getItem(key) === "1") shell.classList.add(cls);
      btn.addEventListener("click", () => {
        shell.classList.toggle(cls);
        localStorage.setItem(
          key, shell.classList.contains(cls) ? "1" : "0"
        );
      });
    });
  }
  // Legacy sidebar-toggle / sidebar-reopen buttons no longer exist in HTML
  // -- removed in favor of .f-collapse. Guarded so JS doesn't error if a
  // cached HTML still has them.
  if (els.sidebarToggle && els.sidebarReopen) {
    els.sidebarToggle.addEventListener("click", () => {
      if (shell) shell.classList.add("is-side-collapsed");
    });
    els.sidebarReopen.addEventListener("click", () => {
      if (shell) shell.classList.remove("is-side-collapsed");
    });
  }
  if (els.sidebarNew) {
    els.sidebarNew.addEventListener("click", () => {
      // "+ New brief" — return to empty hero state and focus the query input.
      clearBanner();
      els.input.value = "";
      els.input.focus();
    });
  }
  if (els.sidebarHome) {
    // Clicking the logo / brand wordmark behaves like a home link: return
    // to the empty hero state where the user can write a new query.
    els.sidebarHome.addEventListener("click", () => {
      clearBanner();
      els.input.value = "";
      els.input.focus();
    });
  }
  if (els.sidebarSearch) {
    let sidebarSearchTimer = null;
    els.sidebarSearch.addEventListener("input", () => {
      clearTimeout(sidebarSearchTimer);
      sidebarSearchTimer = setTimeout(
        () => filterRecents(els.sidebarSearch.value), 100
      );
    });
  }
  els.bannerClear.addEventListener("click", () => {
    clearBanner();
    els.input.value = "";
    els.input.focus();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (!els.modal.hidden) closeModal();
      return;
    }
    // ⌘N / Ctrl+N — open a new brief (return to the hero state and focus
    // the query input). Heads-up: Chrome on macOS intercepts ⌘N at the OS
    // level to open a new browser window, regardless of preventDefault.
    // Firefox / Safari respect the override. ⌘K acts as a reliable
    // fallback that works in every browser.
    if ((e.metaKey || e.ctrlKey) && (e.key === "n" || e.key === "N" ||
                                      e.key === "k" || e.key === "K")) {
      e.preventDefault();
      clearBanner();
      els.input.value = "";
      els.input.focus();
    }
  });

  // --- Supabase auth calls (frontend → Supabase Auth REST API) ---------
  function supabaseConfigured() {
    return !!(window.SUPABASE_URL && window.SUPABASE_ANON_KEY);
  }

  async function supabaseSignUp(email, password) {
    const resp = await fetch(`${window.SUPABASE_URL}/auth/v1/signup`, {
      method: "POST",
      headers: {
        apikey: window.SUPABASE_ANON_KEY,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ email, password }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.msg || data.error_description || "Sign-up failed");
    // If email-confirmation is off in Supabase, this returns an
    // access_token immediately. Otherwise the user must confirm via email.
    return data;
  }

  async function supabaseSignIn(email, password) {
    const resp = await fetch(
      `${window.SUPABASE_URL}/auth/v1/token?grant_type=password`,
      {
        method: "POST",
        headers: {
          apikey: window.SUPABASE_ANON_KEY,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ email, password }),
      }
    );
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error_description || data.msg || "Sign-in failed");
    return data;
  }

  async function supabaseSignOut(accessToken) {
    if (!accessToken) return;
    try {
      await fetch(`${window.SUPABASE_URL}/auth/v1/logout`, {
        method: "POST",
        headers: {
          apikey: window.SUPABASE_ANON_KEY,
          Authorization: `Bearer ${accessToken}`,
        },
      });
    } catch (_) { /* ignore — local state still cleared */ }
  }

  // --- Auth UI state ---------------------------------------------------

  function renderAuthState() {
    if (!els.authPanel) return;
    // If Supabase isn't configured, hide the whole panel — the app falls
    // back to anonymous-link mode with no signup possible.
    if (!supabaseConfigured()) {
      els.authPanel.hidden = true;
      return;
    }
    els.authPanel.hidden = false;
    if (authState) {
      els.authAnon.hidden = true;
      els.authUser.hidden = false;
      els.authUserEmail.textContent = authState.email || "signed in";
    } else {
      els.authAnon.hidden = false;
      els.authUser.hidden = true;
    }
    // Top-right gradient profile circle on the home view: shows the first
    // letter of the user's email when authed, a "?" when anonymous. Click
    // routes to the auth modal (anon) or signs the user out (authed).
    if (els.folioMe) {
      const initial = authState && authState.email
        ? authState.email.trim().charAt(0).toUpperCase()
        : "?";
      els.folioMe.textContent = initial;
      els.folioMe.title = authState
        ? `${authState.email} · click to sign out`
        : "Sign in / sign up";
    }
  }

  function openAuthModal(mode) {
    if (!els.authModal) return;
    authMode = mode === "signup" ? "signup" : "signin";
    els.authModalTitle.textContent =
      authMode === "signup" ? "Create your Folio account" : "Sign in to Folio";
    els.authSubmit.textContent = authMode === "signup" ? "Sign up" : "Sign in";
    els.authToggle.textContent =
      authMode === "signup"
        ? "Already have an account? Sign in"
        : "Need an account? Sign up";
    els.authError.hidden = true;
    els.authError.textContent = "";
    els.authModal.hidden = false;
    setTimeout(() => els.authEmail.focus(), 50);
  }
  function closeAuthModal() {
    if (els.authModal) els.authModal.hidden = true;
  }

  async function handleAuthSubmit(event) {
    event.preventDefault();
    const email = els.authEmail.value.trim();
    const password = els.authPassword.value;
    if (!email || !password) return;
    els.authError.hidden = true;
    els.authSubmit.disabled = true;
    const originalLabel = els.authSubmit.textContent;
    els.authSubmit.textContent = "…";
    try {
      const data = authMode === "signup"
        ? await supabaseSignUp(email, password)
        : await supabaseSignIn(email, password);
      if (!data.access_token) {
        // Email confirmation is on: tell the user, but don't crash the UI.
        els.authError.textContent =
          "Account created. Check your inbox for a confirmation link, then sign in.";
        els.authError.hidden = false;
        return;
      }
      saveAuthState({
        access_token: data.access_token,
        refresh_token: data.refresh_token,
        email: (data.user && data.user.email) || email,
        user_id: data.user && data.user.id,
      });
      renderAuthState();
      closeAuthModal();
      // Reload history now that we have a user.
      loadHistory();
    } catch (err) {
      els.authError.textContent = err.message || "Could not complete sign-in.";
      els.authError.hidden = false;
    } finally {
      els.authSubmit.disabled = false;
      els.authSubmit.textContent = originalLabel;
    }
  }

  async function handleSignOut() {
    const token = authState && authState.access_token;
    saveAuthState(null);
    renderAuthState();
    // Sign-out on the server is best-effort; local state already cleared.
    supabaseSignOut(token);
    // Reload history — should now show only is_demo briefings.
    loadHistory();
  }

  // Wire auth UI.
  if (els.signinBtn) els.signinBtn.addEventListener("click", () => openAuthModal("signin"));
  if (els.signoutBtn) els.signoutBtn.addEventListener("click", handleSignOut);
  if (els.folioMe) {
    els.folioMe.addEventListener("click", () => {
      if (authState) {
        // Confirm before signing out so a stray click doesn't drop the session.
        if (confirm(`Sign out of ${authState.email}?`)) handleSignOut();
      } else {
        openAuthModal("signin");
      }
    });
  }
  if (els.authModalClose) els.authModalClose.addEventListener("click", closeAuthModal);
  if (els.authModalBackdrop) els.authModalBackdrop.addEventListener("click", closeAuthModal);
  if (els.authToggle) {
    els.authToggle.addEventListener("click", () => {
      openAuthModal(authMode === "signup" ? "signin" : "signup");
    });
  }
  if (els.authForm) els.authForm.addEventListener("submit", handleAuthSubmit);

  // Paint initial auth UI based on whatever's in localStorage.
  renderAuthState();

  // Auto-load history into the sidebar on first paint.
  loadHistory();

  // If the page was opened with a ?briefing=... permalink, load it now.
  loadFromUrlIfAny();
})();
