// Thin UI: all state comes from the API; the browser only remembers the session id.
(() => {
  const $ = (id) => document.getElementById(id);
  const els = {
    messages: $("messages"), composer: $("composer"), input: $("input"), send: $("send"),
    fields: $("fields"), json: $("json"), progressBar: $("progress-bar"), progressText: $("progress-text"),
    doc: $("document"), download: $("download"), banner: $("banner"), llm: $("llm-status"), reset: $("new-session"),
  };
  const SESSION_KEY = "intake.sessionId";
  let view = null;
  let busy = false;

  class ApiError extends Error {
    constructor(status, code, message) { super(message); this.status = status; this.code = code; }
  }

  async function api(method, path, body) {
    let res;
    try {
      res = await fetch(path, {
        method,
        headers: body ? { "Content-Type": "application/json" } : {},
        body: body ? JSON.stringify(body) : undefined,
      });
    } catch {
      throw new ApiError(0, "network", "Can't reach the server. Is it running?");
    }
    const data = await res.json().catch(() => null);
    if (!res.ok) {
      const err = data && data.error;
      throw new ApiError(res.status, err ? err.code : "http_" + res.status, err ? err.message : `Request failed (${res.status})`);
    }
    return data;
  }

  const storage = {
    get() { try { return localStorage.getItem(SESSION_KEY); } catch { return null; } },
    set(v) { try { localStorage.setItem(SESSION_KEY, v); } catch { /* private mode */ } },
  };

  function showBanner(text) { els.banner.textContent = text; els.banner.hidden = !text; }

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function formatValue(name, value) {
    if (value === null || value === undefined) return null;
    if (typeof value === "boolean") return value ? "Yes" : "No";
    if (Array.isArray(value)) {
      if (!value.length) return "None";
      if (name === "specific_gifts") return value.map((g) => `${g.item} → ${g.recipient}`).join("; ");
      return value.join(", ");
    }
    return String(value);
  }

  // ---- rendering -------------------------------------------------------

  function renderMessages(messages) {
    els.messages.replaceChildren(...messages.map((m) => el("div", `msg ${m.role}`, m.content)));
    els.messages.scrollTop = els.messages.scrollHeight;
  }

  function renderTurnMeta(turn) {
    const chips = [];
    const label = (f) => (view.fields.find((x) => x.name === f) || { label: f }).label;
    for (const c of turn.changes) {
      const text = { added: "Saved", corrected: "Corrected", flagged: "Needs confirmation", confirmed: "Confirmed" }[c.kind];
      chips.push(el("span", `chip ${c.kind}`, `${text}: ${label(c.field)}`));
    }
    for (const r of turn.rejected) {
      const chip = el("span", "chip rejected", `Not saved: ${label(r.field)}`);
      chip.title = r.reason;
      chips.push(chip);
    }
    if (!chips.length) return;
    const meta = el("div", "turn-meta");
    meta.append(...chips);
    // Place the chips just above the assistant's reply they relate to.
    els.messages.insertBefore(meta, els.messages.lastElementChild);
  }

  function renderFields(changedFields = []) {
    const { state, fields, progress } = view;
    els.fields.replaceChildren(...fields.map((f) => {
      const tracked = state[f.name];
      const li = el("li", "field" + (f.required ? "" : " not-required"));
      if (changedFields.includes(f.name)) li.classList.add("flash");
      li.append(el("span", "field-label", f.label));

      const valueText = formatValue(f.name, tracked.value);
      const value = el("span", "field-value");
      if (!f.required) value.append(el("span", "empty", "Not applicable"));
      else if (valueText === null) value.append(el("span", "empty", "—"));
      else value.textContent = valueText;
      li.append(value);

      const pillClass = { confirmed: "pill-ok", unconfirmed: "pill-warn", missing: "pill-missing" }[tracked.status];
      li.append(el("span", `pill ${pillClass}`, f.required ? tracked.status : "n/a"));
      if (tracked.note && tracked.status === "unconfirmed") li.append(el("span", "field-note", tracked.note));
      return li;
    }));

    els.json.textContent = JSON.stringify(view.confirmed_state, null, 2);
    const pct = progress.required ? Math.round((progress.confirmed / progress.required) * 100) : 0;
    els.progressBar.style.width = pct + "%";
    els.progressText.textContent = `${progress.confirmed} of ${progress.required} required answers confirmed`;
  }

  function renderDocument() {
    const d = view.document;
    const nodes = [el("h3", null, d.title), el("div", "disclaimer", d.disclaimer)];
    nodes.push(el("p", "doc-status " + (d.is_complete ? "" : "muted"),
      d.is_complete ? "✓ All required information collected." : `Incomplete draft — outstanding: ${d.outstanding.join(", ")}`));
    for (const s of d.sections) {
      nodes.push(el("h4", null, s.heading));
      for (const p of s.paragraphs) nodes.push(paragraphWithPlaceholders(p));
    }
    els.doc.replaceChildren(...nodes);
  }

  function paragraphWithPlaceholders(text) {
    const p = el("p");
    for (const part of text.split(/(\[[^\]]+\])/)) {
      if (!part) continue;
      p.append(/^\[.*\]$/.test(part) ? el("span", "placeholder", part) : document.createTextNode(part));
    }
    return p;
  }

  function render(newView, turn) {
    view = newView;
    storage.set(view.session_id);
    renderMessages(view.messages);
    if (turn) renderTurnMeta(turn);
    renderFields(turn ? turn.changes.map((c) => c.field) : []);
    renderDocument();
  }

  // ---- actions ---------------------------------------------------------

  function setBusy(value) {
    busy = value;
    els.send.disabled = value;
    els.input.disabled = value;
  }

  async function startSession() {
    showBanner("");
    render(await api("POST", "/api/sessions"));
  }

  async function loadSession() {
    const id = storage.get();
    if (id) {
      try { return render(await api("GET", `/api/sessions/${encodeURIComponent(id)}`)); }
      catch (e) { if (e.status !== 404) throw e; } // server restarted: sessions are in memory
    }
    await startSession();
  }

  async function sendMessage(text) {
    if (busy || !view) return;
    setBusy(true);
    showBanner("");
    els.messages.append(el("div", "msg user", text), el("div", "msg assistant typing", "Thinking…"));
    els.messages.scrollTop = els.messages.scrollHeight;
    els.input.value = "";
    try {
      const res = await api("POST", `/api/sessions/${encodeURIComponent(view.session_id)}/messages`, { message: text });
      render(res.session, res.turn);
    } catch (e) {
      // The turn was not committed server-side: restore the transcript and the user's text.
      renderMessages(view.messages);
      els.input.value = text;
      if (e.code === "session_not_found") { await startSession(); els.input.value = text; }
      showBanner(e.message);
    } finally {
      setBusy(false);
      els.input.focus();
    }
  }

  async function checkHealth() {
    try {
      const h = await api("GET", "/api/health");
      els.llm.textContent = `${h.llm_provider} · ${h.llm_model}`;
      els.llm.className = "llm-status " + (h.llm_configured ? "ok" : "bad");
      if (!h.llm_configured) showBanner(`Model not configured: ${h.llm_problem}`);
    } catch (e) {
      els.llm.textContent = "Server unreachable";
      els.llm.className = "llm-status bad";
    }
  }

  // ---- wiring ----------------------------------------------------------

  els.composer.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = els.input.value.trim();
    if (text) sendMessage(text);
  });
  els.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); els.composer.requestSubmit(); }
  });
  els.reset.addEventListener("click", () => {
    if (confirm("Start a new session? The current answers will be discarded.")) startSession().catch((e) => showBanner(e.message));
  });
  document.querySelectorAll(".seg").forEach((btn) => btn.addEventListener("click", () => {
    document.querySelectorAll(".seg").forEach((b) => b.classList.toggle("active", b === btn));
    els.fields.hidden = btn.dataset.view !== "fields";
    els.json.hidden = btn.dataset.view !== "json";
  }));
  els.download.addEventListener("click", () => {
    if (!view) return;
    const blob = new Blob([view.document.markdown], { type: "text/markdown" });
    const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: "personal-wishes-draft.md" });
    a.click();
    URL.revokeObjectURL(a.href);
  });

  (async () => {
    await checkHealth();
    // Keep the banner from health if the model isn't configured.
    const banner = els.banner.textContent;
    try { await loadSession(); } catch (e) { showBanner(e.message); return; }
    if (banner) showBanner(banner);
    els.input.focus();
  })();
})();
