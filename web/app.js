(() => {
  const state = { credential: null, username: null, items: [], selectedId: null, page: 0, pageSize: 20 };
  const $ = (id) => document.getElementById(id);
  const loginView = $("login-view");
  const memoryView = $("memory-view");
  const memoryList = $("memory-list");
  const detailPanel = $("detail-panel");

  function dateLabel(value) {
    if (!value) return "Unknown date";
    const date = new Date(value);
    return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(date);
  }

  function apiError(data, fallback) {
    return data?.detail || data?.error || fallback;
  }

  async function request(url, options = {}) {
    const headers = new Headers(options.headers || {});
    if (state.credential) headers.set("Authorization", `Bearer ${state.credential}`);
    const response = await fetch(url, { ...options, headers, cache: "no-store" });
    const data = response.status === 204 ? null : await response.json().catch(() => null);
    if (!response.ok) {
      if (response.status === 401) logout(false);
      throw new Error(apiError(data, `Request failed (${response.status})`));
    }
    return data;
  }

  function showViewer(username) {
    state.username = username;
    loginView.hidden = true;
    memoryView.hidden = false;
    $("user-area").hidden = false;
    $("current-user").textContent = username;
  }

  function logout(showLoginError = false) {
    state.credential = null;
    state.username = null;
    state.items = [];
    state.selectedId = null;
    loginView.hidden = false;
    memoryView.hidden = true;
    $("user-area").hidden = true;
    $("credential").value = "";
    memoryList.replaceChildren();
    detailPanel.replaceChildren();
    if (showLoginError) {
      $("login-error").textContent = "The credential is no longer valid. Ask your agent for a new read-only credential.";
      $("login-error").hidden = false;
    }
  }

  function addTag(parent, value) {
    const el = document.createElement("span");
    el.className = "tag";
    el.textContent = value;
    parent.append(el);
  }

  function renderDetail(item) {
    detailPanel.replaceChildren();
    const heading = document.createElement("p");
    heading.className = "detail-title";
    heading.textContent = "Memory details";
    const content = document.createElement("p");
    content.className = "detail-content";
    content.textContent = item.content || "(no content)";
    const meta = document.createElement("div");
    meta.className = "detail-meta";
    const rows = [
      ["Source agent", item.owner_agent_id || "Unknown"],
      ["Type", item.memory_type || "fact"],
      ["Created", dateLabel(item.created_at)],
      ["Last updated", dateLabel(item.updated_at)],
      ["Version", String(item.version ?? 1)],
      ["Memory ID", item.id || ""],
    ];
    for (const [label, value] of rows) {
      const row = document.createElement("div");
      row.className = "meta-row";
      const key = document.createElement("span");
      key.className = "meta-label";
      key.textContent = label;
      const val = document.createElement("span");
      val.className = "meta-value";
      val.textContent = value;
      row.append(key, val);
      meta.append(row);
    }
    if (item.tags?.length) {
      const row = document.createElement("div");
      row.className = "meta-row";
      const label = document.createElement("span");
      label.className = "meta-label";
      label.textContent = "Tags";
      const values = document.createElement("div");
      values.className = "detail-tags";
      item.tags.forEach((tag) => addTag(values, tag));
      row.append(label, values);
      meta.append(row);
    }
    detailPanel.append(heading, content, meta);
  }

  function renderList() {
    memoryList.replaceChildren();
    for (const item of state.items) {
      const card = document.createElement("article");
      card.className = `memory-card${item.id === state.selectedId ? " selected" : ""}`;
      card.tabIndex = 0;
      card.setAttribute("role", "button");
      card.setAttribute("aria-label", `Memory uploaded by ${item.owner_agent_id || "unknown agent"}`);
      const top = document.createElement("div");
      top.className = "card-top";
      const type = document.createElement("span");
      type.className = "type-badge";
      type.textContent = item.memory_type || "fact";
      const date = document.createElement("span");
      date.className = "card-date";
      date.textContent = dateLabel(item.updated_at);
      top.append(type, date);
      const body = document.createElement("div");
      body.className = "card-content";
      body.textContent = item.content || "(no content)";
      const bottom = document.createElement("div");
      bottom.className = "card-bottom";
      const source = document.createElement("span");
      source.className = "source-chip";
      const avatar = document.createElement("span");
      avatar.className = "source-avatar";
      avatar.textContent = (item.owner_agent_id || "?").slice(0, 1).toUpperCase();
      const sourceName = document.createElement("span");
      sourceName.textContent = item.owner_agent_id || "Unknown agent";
      source.append(avatar, sourceName);
      const tags = document.createElement("span");
      tags.className = "tag-list";
      (item.tags || []).slice(0, 3).forEach((tag) => addTag(tags, tag));
      bottom.append(source, tags);
      card.append(top, body, bottom);
      const select = () => {
        state.selectedId = item.id;
        renderList();
        renderDetail(item);
      };
      card.addEventListener("click", select);
      card.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); select(); }
      });
      memoryList.append(card);
    }
    $("empty-state").hidden = state.items.length !== 0;
    $("result-count").textContent = state.items.length ? `· ${state.items.length} item${state.items.length === 1 ? "" : "s"}` : "· 0 items";
    $("page-label").textContent = `Page ${state.page + 1}`;
    $("previous-button").disabled = state.page === 0;
    $("next-button").disabled = state.items.length < state.pageSize;
    if (!state.items.some((item) => item.id === state.selectedId)) {
      state.selectedId = null;
      const placeholder = document.createElement("div");
      placeholder.className = "detail-placeholder";
      const mark = document.createElement("span");
      mark.className = "placeholder-mark";
      mark.textContent = "✳";
      const title = document.createElement("strong");
      title.textContent = "Select a memory";
      const hint = document.createElement("p");
      hint.textContent = "The full content and its source will appear here.";
      placeholder.append(mark, title, hint);
      detailPanel.replaceChildren(placeholder);
    }
  }

  async function loadMemories() {
    $("loading-state").hidden = false;
    $("load-error").hidden = true;
    $("empty-state").hidden = true;
    const params = new URLSearchParams({ limit: String(state.pageSize), offset: String(state.page * state.pageSize) });
    const query = $("query").value.trim();
    const owner = $("source-filter").value.trim();
    if (query) params.set("q", query);
    if (owner) params.set("owner_agent_id", owner);
    try {
      const data = await request(`v1/memories?${params}`);
      state.items = data.items || [];
      $("result-label").textContent = query ? `Results for "${query}"` : "Recent memories";
      renderList();
    } catch (error) {
      if (state.credential) {
        $("load-error").textContent = error.message;
        $("load-error").hidden = false;
      }
    } finally {
      $("loading-state").hidden = true;
    }
  }

  $("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const username = $("username").value.trim();
    const credential = $("credential").value.trim();
    const button = event.currentTarget.querySelector("button[type=submit]");
    $("login-error").hidden = true;
    button.disabled = true;
    button.textContent = "Signing in…";
    try {
      const response = await fetch("viewer/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, credential }), cache: "no-store",
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(apiError(data, "Sign-in failed. Check your username and credential."));
      state.credential = credential;
      showViewer(data.username);
      await loadMemories();
    } catch (error) {
      $("login-error").textContent = error.message;
      $("login-error").hidden = false;
    } finally {
      button.disabled = false;
      button.innerHTML = 'Sign in <span aria-hidden="true">→</span>';
    }
  });

  $("logout-button").addEventListener("click", () => logout(false));
  $("search-form").addEventListener("submit", (event) => { event.preventDefault(); state.page = 0; loadMemories(); });
  $("previous-button").addEventListener("click", () => { if (state.page > 0) { state.page -= 1; state.selectedId = null; loadMemories(); } });
  $("next-button").addEventListener("click", () => { if (state.items.length === state.pageSize) { state.page += 1; state.selectedId = null; loadMemories(); } });
})();
