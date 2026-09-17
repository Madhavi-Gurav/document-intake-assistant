// Document Intake Assistant — UI.
// The server owns all state; the browser only remembers the session id and presentation choices.
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const els = {
    messages: $("#messages"), composer: $("#composer"), input: $("#input"), send: $("#send"),
    suggestions: $("#suggestions"), stepLabel: $("#step-label"), stepCount: $("#step-count"), barFill: $("#bar-fill"),
    groups: $("#groups"), json: $("#json"), ringFill: $("#ring-fill"), ringText: $("#ring-text"),
    summaryTitle: $("#summary-title"), summarySub: $("#summary-sub"),
    detailsView: $("#details-view"), documentView: $("#document-view"), paper: $("#paper"), docStatus: $("#doc-status"),
    docDot: $("#doc-dot"), mDocDot: $("#m-doc-dot"), mDetailsBadge: $("#m-details-badge"),
    notice: $("#notice"), toast: $("#toast"), dialog: $("#restart-dialog"),
  };

  // ---- presentation config ------------------------------------------------

  const GROUPS = [
    { title: "About you", fields: ["full_name", "home_address"] },
    { title: "Scope", fields: ["covers_worldwide_assets"] },
    { title: "Family", fields: ["has_children", "children"] },
    { title: "Executor", fields: ["executor_name", "executor_relationship"] },
    { title: "Gifts & wishes", fields: ["specific_gifts", "additional_wishes"] },
  ];

  const LABELS = {
    full_name: "Full name",
    home_address: "Home address",
    covers_worldwide_assets: "Covers assets worldwide",
    has_children: "Children",
    children: "Children's names",
    executor_name: "Executor",
    executor_relationship: "Relationship to you",
    specific_gifts: "Specific gifts",
    additional_wishes: "Additional wishes",
  };

  // What the progress header says we're currently doing.
  const TOPICS = {
    full_name: "Your name", home_address: "Your address", covers_worldwide_assets: "Worldwide assets",
    has_children: "Children", children: "Your children", executor_name: "Choosing an executor",
    executor_relationship: "Your executor", specific_gifts: "Specific gifts", additional_wishes: "Final wishes",
  };

  // One-tap answers for questions that have obvious short replies.
  const QUICK_REPLIES = {
    covers_worldwide_assets: ["Yes, all my assets worldwide", "No, not worldwide"],
    has_children: ["Yes, I have children", "No, I don't have children"],
    specific_gifts: ["No specific gifts"],
    additional_wishes: ["No additional wishes"],
  };

  // Starter text when the user chooses to add or change a detail from the preview.
  const PREFILL = {
    full_name: "My full name is ",
    home_address: "My home address is ",
    covers_worldwide_assets: "About worldwide assets: ",
    has_children: "About children: ",
    children: "My children's names are ",
    executor_name: "I'd like my executor to be ",
    executor_relationship: "My executor is my ",
    specific_gifts: "I'd like to leave ",
    additional_wishes: "One more wish: ",
  };

  const ICON = {
    check: '<svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
    alert: '<svg viewBox="0 0 24 24"><path d="M12 7v6M12 17h.01"/></svg>',
    doc: '<svg viewBox="0 0 24 24"><path d="M6 3h8l4 4v14H6z"/><path d="M9 12h6M9 15h4"/></svg>',
    ready: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.8 2.8L16 10"/></svg>',
    pending: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
  };

  const SESSION_KEY = "wishes.sessionId";

  const ui = {
    view: null,        // SessionView from the API
    turn: null,        // details of the latest turn (for "saved" chips and highlights)
    outbox: null,      // { text, status: "sending" | "failed" }
    side: "details",   // desktop preview tab
    mobile: "chat",    // mobile section
    docUnseen: false,
  };

  // ---- helpers --------------------------------------------------------------

  function h(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === false || v == null) continue;
      if (k === "class") node.className = v;
      else if (k === "html") node.innerHTML = v; // only used with static ICON markup
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) if (c != null && c !== false) node.append(c);
    return node;
  }

  let toastTimer;
  function toast(message, kind = "") {
    els.toast.textContent = message;
    els.toast.className = `toast show ${kind}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (els.toast.className = "toast"), kind === "error" ? 5000 : 2200);
  }

  const storage = {
    get: () => { try { return localStorage.getItem(SESSION_KEY); } catch { return null; } },
    set: (v) => { try { localStorage.setItem(SESSION_KEY, v); } catch { /* storage unavailable */ } },
  };

  class ApiError extends Error {
    constructor(status, code, message) { super(message); this.status = status; this.code = code; }
  }

  async function api(method, path, body) {
    let res;
    try {
      res = await fetch(path, {
        method,
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
    } catch {
      throw new ApiError(0, "network", "Can't reach the server. Check your connection and try again.");
    }
    const data = await res.json().catch(() => null);
    if (!res.ok) {
      const err = data && data.error;
      throw new ApiError(res.status, err ? err.code : `http_${res.status}`, friendlyError(err ? err.code : "", res.status));
    }
    return data;
  }

  // Server messages can carry configuration details; users get plain language instead.
  function friendlyError(code, status) {
    switch (code) {
      case "llm_unavailable": return "The assistant is busy right now. Your answers are safe — please try again.";
      case "malformed_model_response": return "The assistant got a bit confused. Please try again, or rephrase.";
      case "llm_not_configured": return "The assistant isn't connected yet. Please contact the site owner.";
      case "session_not_found": return "Your session expired, so we've started a fresh one.";
      case "invalid_request": return "That message couldn't be sent. Please check it and try again.";
      default: return status >= 500 ? "Something went wrong on our side. Please try again." : "Something went wrong. Please try again.";
    }
  }

  function formatValue(name, value) {
    if (value === null || value === undefined) return null;
    if (typeof value === "boolean") {
      if (name === "has_children") return value ? "Yes" : "No children";
      return value ? "Yes" : "No";
    }
    if (Array.isArray(value)) {
      if (!value.length) return "None";
      const items = name === "specific_gifts" ? value.map((g) => `${g.item} → ${g.recipient}`) : value;
      return items.length === 1 ? items[0] : h("ul", {}, items.map((t) => h("li", {}, t)));
    }
    return String(value);
  }

  const isRequired = (name) => ui.view.fields.find((f) => f.name === name)?.required ?? true;

  // ---- rendering ------------------------------------------------------------

  function render() {
    renderProgress();
    renderMessages();
    renderSuggestions();
    renderDetails();
    renderDocument();
    renderTabs();
  }

  function renderProgress() {
    const { progress, state } = ui.view;
    const pct = progress.required ? (progress.confirmed / progress.required) * 100 : 0;
    els.barFill.style.width = `${pct}%`;
    els.stepCount.textContent = `${progress.confirmed} of ${progress.required} details`;
    const next = progress.next_field;
    if (!next) els.stepLabel.textContent = "All details collected";
    else if (state[next].status === "unconfirmed") els.stepLabel.textContent = `Checking: ${TOPICS[next]}`;
    else els.stepLabel.textContent = TOPICS[next];
  }

  function renderMessages() {
    const { messages, progress } = ui.view;
    const nodes = messages.map((m, i) => messageNode(m.role, m.content, i === messages.length - 1 ? ui.turn : null));

    if (!progress.next_field && !ui.outbox) {
      nodes.push(h("div", { class: "done-card" },
        h("div", {}, h("strong", {}, "Your draft is ready"), h("span", {}, "Review it, save it as a PDF, or tell me anything you'd like to change.")),
        h("button", { class: "btn btn-primary btn-sm", type: "button", onclick: () => showDocument() }, "View document"),
      ));
    }

    if (ui.outbox) {
      const failed = ui.outbox.status === "failed";
      nodes.push(h("div", { class: `msg user ${ui.outbox.status}` },
        h("div", { class: "msg-body" },
          h("div", { class: "bubble" }, ui.outbox.text),
          failed && h("div", { class: "msg-status" }, "Not sent",
            h("button", { class: "link-btn", type: "button", onclick: () => sendMessage(ui.outbox.text) }, "Retry"),
            h("button", { class: "link-btn", type: "button", onclick: editFailed }, "Edit")),
        )));
      if (!failed) nodes.push(h("div", { class: "msg assistant typing", "aria-label": "Assistant is typing" },
        avatar(), h("div", { class: "msg-body" }, h("div", { class: "bubble" }, h("i"), h("i"), h("i")))));
    }

    els.messages.replaceChildren(...nodes);
    els.messages.scrollTop = els.messages.scrollHeight;
  }

  function avatar() {
    return h("span", { class: "avatar", "aria-hidden": "true", html: ICON.doc });
  }

  function messageNode(role, text, turn) {
    const body = h("div", { class: "msg-body" }, h("div", { class: "bubble" }, text));
    if (role === "assistant" && turn) {
      // Only user-meaningful outcomes; internal validation detail stays out of the UI.
      const saved = turn.changes.filter((c) => c.status === "confirmed" && isRequired(c.field));
      const flagged = turn.changes.filter((c) => c.status === "unconfirmed");
      if (saved.length || flagged.length) {
        body.prepend(h("div", { class: "updates" },
          [...new Map(saved.map((c) => [c.field, c])).values()].map((c) =>
            h("span", { class: "update", html: ICON.check }, c.kind === "corrected" ? `Updated ${LABELS[c.field].toLowerCase()}` : LABELS[c.field])),
          [...new Map(flagged.map((c) => [c.field, c])).values()].map((c) =>
            h("span", { class: "update flagged", html: ICON.alert }, `Check ${LABELS[c.field].toLowerCase()}`)),
        ));
      }
    }
    return h("div", { class: `msg ${role}` }, role === "assistant" && avatar(), body);
  }

  function renderSuggestions() {
    const next = ui.view.progress.next_field;
    const busy = !!ui.outbox && ui.outbox.status === "sending";
    let chips = [];
    if (next && QUICK_REPLIES[next] && !ui.outbox) {
      chips = QUICK_REPLIES[next].map((text) =>
        h("button", { class: "suggestion", type: "button", disabled: busy, onclick: () => sendMessage(text) }, text));
    }
    els.suggestions.replaceChildren(...chips);
  }

  function renderDetails() {
    const { state, progress, confirmed_state: confirmed } = ui.view;
    const changed = new Set((ui.turn?.changes || []).map((c) => c.field));

    const circumference = 119.4;
    const ratio = progress.required ? progress.confirmed / progress.required : 0;
    els.ringFill.style.strokeDashoffset = String(circumference * (1 - ratio));
    els.ringText.textContent = `${progress.confirmed}/${progress.required}`;

    const needsCheck = progress.outstanding.filter((f) => state[f].status === "unconfirmed").length;
    if (!progress.next_field) {
      els.summaryTitle.textContent = "Everything's in place";
      els.summarySub.textContent = "You can still change any detail below.";
    } else if (progress.confirmed === 0 && !needsCheck) {
      els.summaryTitle.textContent = "Let's begin";
      els.summarySub.textContent = "Your answers will appear here as you chat.";
    } else {
      const left = progress.outstanding.length;
      els.summaryTitle.textContent = `${left} ${left === 1 ? "detail" : "details"} to go`;
      els.summarySub.textContent = needsCheck ? `${needsCheck} ${needsCheck === 1 ? "answer needs" : "answers need"} a quick check.` : "Keep chatting — or add details directly below.";
    }

    els.groups.replaceChildren(...GROUPS.map((group) => {
      const rows = group.fields.filter(isRequired).map((name) => {
        const f = state[name];
        const isCurrent = name === progress.next_field;
        const value = f.status === "missing" ? "Not answered yet" : formatValue(name, f.value) ?? "Needs a clearer answer";
        return h("div", {
          class: `row ${f.status}${isCurrent ? " current" : ""}${changed.has(name) ? " flash" : ""}`,
          onanimationend: (e) => e.currentTarget.classList.remove("flash"),
        },
          h("span", { class: "row-icon", html: f.status === "confirmed" ? ICON.check : f.status === "unconfirmed" ? ICON.alert : "" }),
          h("div", {},
            h("div", { class: "row-label" }, LABELS[name],
              isCurrent && f.status !== "unconfirmed" && h("span", { class: "pill" }, "Now"),
              f.status === "unconfirmed" && h("span", { class: "pill warn" }, "Check")),
            h("div", { class: "row-value" }, value),
            f.status === "unconfirmed" && f.note && h("div", { class: "row-note" }, f.note)),
          h("button", { class: "row-action", type: "button", onclick: () => prefill(name) },
            f.status === "confirmed" ? "Change" : f.status === "unconfirmed" ? "Fix" : "Add"));
      });
      return h("section", { class: "group" }, h("h3", {}, group.title), h("div", { class: "group-card" }, rows));
    }));

    els.json.textContent = JSON.stringify(confirmed, null, 2);
    const left = progress.outstanding.length;
    els.mDetailsBadge.hidden = !left;
    els.mDetailsBadge.textContent = left;
  }

  function renderDocument() {
    const d = ui.view.document;
    els.docStatus.className = `doc-status ${d.is_complete ? "ready" : "progress"}`;
    els.docStatus.innerHTML = d.is_complete ? ICON.ready : ICON.pending;
    els.docStatus.append(d.is_complete
      ? "Ready to save"
      : `Draft in progress · ${d.outstanding.length} ${d.outstanding.length === 1 ? "detail" : "details"} missing`);

    const nodes = [
      h("h2", {}, "Personal Wishes Document"),
      h("p", { class: "paper-sub" }, d.is_complete ? "Draft" : "Draft — incomplete"),
      h("div", { class: "disclaimer" }, d.disclaimer),
    ];
    for (const s of d.sections) {
      const isSignature = s.heading.toLowerCase() === "signature";
      const block = h("section", { class: isSignature ? "signature" : "" },
        !isSignature && h("h4", {}, s.heading),
        s.paragraphs.map(paragraph));
      nodes.push(block);
    }
    els.paper.replaceChildren(...nodes);
  }

  // Placeholders like "[not yet provided]" render as soft highlighted tags.
  function paragraph(text) {
    const p = h("p");
    for (const part of text.split(/(\[[^\]]+\])/)) {
      if (!part) continue;
      p.append(/^\[.*\]$/.test(part) ? h("span", { class: "ph" }, part.slice(1, -1)) : document.createTextNode(part));
    }
    return p;
  }

  function renderTabs() {
    const side = isMobile() && ui.mobile !== "chat" ? ui.mobile : ui.side;
    els.detailsView.hidden = side !== "details";
    els.documentView.hidden = side !== "document";
    $$(".side-tabs [data-side]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.side === ui.side)));
    $$(".mobile-tabs [data-mobile]").forEach((b) => b.classList.toggle("active", b.dataset.mobile === ui.mobile));
    const viewingDoc = isMobile() ? ui.mobile === "document" : ui.side === "document";
    if (viewingDoc) ui.docUnseen = false;
    els.docDot.hidden = !ui.docUnseen;
    els.mDocDot.hidden = !ui.docUnseen;
  }

  // ---- interactions ---------------------------------------------------------

  const isMobile = () => window.matchMedia("(max-width: 900px)").matches;

  function setSide(tab) {
    ui.side = tab;
    renderTabs();
  }

  function setMobile(section) {
    ui.mobile = section;
    document.body.dataset.mobileView = section;
    if (section !== "chat") ui.side = section;
    renderTabs();
    if (section === "chat") els.messages.scrollTop = els.messages.scrollHeight;
  }

  function showDocument() {
    if (isMobile()) setMobile("document");
    else setSide("document");
  }

  function prefill(field) {
    if (isMobile()) setMobile("chat");
    els.input.value = PREFILL[field] || "";
    autosize();
    els.input.focus();
    els.input.setSelectionRange(els.input.value.length, els.input.value.length);
  }

  function editFailed() {
    els.input.value = ui.outbox.text;
    ui.outbox = null;
    render();
    autosize();
    els.input.focus();
  }

  function autosize() {
    els.input.style.height = "auto";
    els.input.style.height = `${Math.min(els.input.scrollHeight, 140)}px`;
    els.input.style.overflowY = els.input.scrollHeight > 140 ? "auto" : "hidden";
    els.send.disabled = !els.input.value.trim() || ui.outbox?.status === "sending";
  }

  function applyView(view, turn = null) {
    const docChanged = ui.view && JSON.stringify(ui.view.document.sections) !== JSON.stringify(view.document.sections);
    ui.view = view;
    ui.turn = turn;
    storage.set(view.session_id);
    if (docChanged) {
      const viewingDoc = isMobile() ? ui.mobile === "document" : ui.side === "document";
      ui.docUnseen = !viewingDoc;
    }
    render();
  }

  async function sendMessage(text) {
    text = text.trim();
    if (!text || !ui.view || ui.outbox?.status === "sending") return;
    ui.outbox = { text, status: "sending" };
    els.input.value = "";
    autosize();
    render();
    try {
      const res = await api("POST", `/api/sessions/${encodeURIComponent(ui.view.session_id)}/messages`, { message: text });
      ui.outbox = null;
      applyView(res.session, res.turn);
    } catch (e) {
      if (e.code === "session_not_found") {
        ui.outbox = null;
        await startSession();
        els.input.value = text;
        autosize();
      } else {
        ui.outbox = { text, status: "failed" };
        render();
      }
      toast(e.message, "error");
    } finally {
      autosize();
      if (!isMobile()) els.input.focus();
    }
  }

  async function startSession() {
    ui.outbox = null;
    ui.docUnseen = false;
    applyView(await api("POST", "/api/sessions"));
  }

  async function loadSession() {
    const id = storage.get();
    if (id) {
      try {
        applyView(await api("GET", `/api/sessions/${encodeURIComponent(id)}`));
        return;
      } catch (e) {
        if (e.status !== 404) throw e; // sessions are in memory, so a server restart clears them
      }
    }
    await startSession();
  }

  async function checkHealth() {
    try {
      const health = await api("GET", "/api/health");
      if (!health.llm_configured) {
        console.warn("LLM not configured:", health.llm_problem);
        els.notice.textContent = "The assistant isn't connected to a language model yet, so replies are unavailable. See the README to add an API key.";
        els.notice.hidden = false;
      }
    } catch {
      /* the session request below will surface connectivity problems */
    }
  }

  // ---- wiring ---------------------------------------------------------------

  els.composer.addEventListener("submit", (e) => {
    e.preventDefault();
    sendMessage(els.input.value);
  });
  els.input.addEventListener("input", autosize);
  els.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      sendMessage(els.input.value);
    }
  });

  $$(".side-tabs [data-side]").forEach((b) => b.addEventListener("click", () => setSide(b.dataset.side)));
  $$(".mobile-tabs [data-mobile]").forEach((b) => b.addEventListener("click", () => setMobile(b.dataset.mobile)));
  window.matchMedia("(max-width: 900px)").addEventListener("change", () => ui.view && renderTabs());

  $("#restart").addEventListener("click", () => {
    if (typeof els.dialog.showModal === "function") els.dialog.showModal();
    else if (confirm("Start over? Your current answers will be cleared.")) startSession();
  });
  els.dialog.addEventListener("close", async () => {
    if (els.dialog.returnValue !== "confirm") return;
    try {
      await startSession();
      setMobile("chat");
      toast("Started a new draft");
    } catch (e) {
      toast(e.message, "error");
    }
  });

  $("#copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(ui.view.document.markdown);
      toast("Document copied");
    } catch {
      toast("Couldn't copy — try Download instead", "error");
    }
  });
  $("#download").addEventListener("click", () => {
    const blob = new Blob([ui.view.document.markdown], { type: "text/markdown" });
    const a = h("a", { href: URL.createObjectURL(blob), download: "personal-wishes-draft.md" });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  });
  $("#print").addEventListener("click", () => window.print());

  (async () => {
    await checkHealth();
    try {
      await loadSession();
      autosize();
      if (!isMobile()) els.input.focus();
    } catch (e) {
      els.messages.replaceChildren(h("div", { class: "msg assistant" }, avatar(),
        h("div", { class: "msg-body" }, h("div", { class: "bubble" }, e.message))));
    }
  })();
})();
