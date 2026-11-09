(() => {
  "use strict";

  const state = {
    credential: null,
    username: null,
    tenantId: null,
    view: "memories",
    items: [],
    threads: [],
    selectedId: null,
    selectedThreadId: null,
    typeFilter: null,
    page: 0,
    pageSize: 20,
    threadLoaded: false,
  };

  const $ = (id) => document.getElementById(id);
  const SVG_NS = "http://www.w3.org/2000/svg";
  const VIEW_TITLES = { memories: "Memories", discussions: "Discussions", about: "About" };

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function icon(name, cls) {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("class", cls ? `icon ${cls}` : "icon");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS(SVG_NS, "use");
    use.setAttribute("href", `#i-${name}`);
    svg.append(use);
    return svg;
  }

  function toast(message, kind = "ok") {
    const node = el("div", `toast${kind === "error" ? " error" : ""}`);
    node.append(icon(kind === "error" ? "close" : "check"), el("span", "", message));
    $("toast-stack").append(node);
    setTimeout(() => {
      node.classList.add("leaving");
      setTimeout(() => node.remove(), 260);
    }, 2800);
  }

  // -- Formatting ----------------------------------------------------------
  function parseDate(value) {
    if (!value) return null;
    const date = new Date(value);
    return Number.isNaN(date.valueOf()) ? null : date;
  }

  function dateLabel(value) {
    const date = parseDate(value);
    return date ? new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(date) : "Unknown date";
  }

  function relativeTime(value) {
    const date = parseDate(value);
    if (!date) return "unknown";
    const diff = Date.now() - date.valueOf();
    if (diff < 0) return dateLabel(value);
    const min = 60e3, hour = 3600e3, day = 86400e3;
    if (diff < min) return "just now";
    if (diff < hour) return `${Math.floor(diff / min)}m ago`;
    if (diff < day) return `${Math.floor(diff / hour)}h ago`;
    if (diff < 7 * day) return `${Math.floor(diff / day)}d ago`;
    return new Intl.DateTimeFormat("en", { month: "short", day: "numeric", year: "numeric" }).format(date);
  }

  function hashOf(text) {
    let h = 0;
    const s = String(text || "");
    for (let i = 0; i < s.length; i += 1) h = (h * 31 + s.charCodeAt(i)) | 0;
    return Math.abs(h);
  }

  const PALETTES = [
    ["var(--accent)", "var(--accent-soft)"],
    ["var(--teal)", "var(--teal-soft)"],
    ["var(--blue)", "var(--blue-soft)"],
    ["var(--amber)", "var(--amber-soft)"],
    ["var(--rose)", "var(--rose-soft)"],
  ];
  const TYPE_PALETTE = { fact: 0, note: 1, context: 2, decision: 3, preference: 4 };

  function paletteFor(name) {
    const key = String(name || "fact").toLowerCase();
    const index = Object.prototype.hasOwnProperty.call(TYPE_PALETTE, key)
      ? TYPE_PALETTE[key] : hashOf(key) % PALETTES.length;
    const [fg, bg] = PALETTES[index];
    return { fg, bg };
  }

  const AVATAR_GRADIENTS = [
    "linear-gradient(135deg,#8d7cf5,#5f4cd9)",
    "linear-gradient(135deg,#45cdb0,#0e9f7c)",
    "linear-gradient(135deg,#7ba9f5,#3976cf)",
    "linear-gradient(135deg,#f0b657,#d98b16)",
    "linear-gradient(135deg,#f485a8,#d1517a)",
    "linear-gradient(135deg,#8e9bd8,#5a649e)",
  ];
  function avatarStyle(name) { return AVATAR_GRADIENTS[hashOf(name) % AVATAR_GRADIENTS.length]; }
  function initials(name) { return String(name || "?").slice(0, 1).toUpperCase(); }

  function apiError(data, fallback) {
    const detail = data && (data.detail || data.error);
    if (Array.isArray(detail)) return detail.map((d) => d.msg || String(d)).join("; ");
    return detail || fallback;
  }

  // -- Network -------------------------------------------------------------
  async function request(url, options = {}) {
    const headers = new Headers(options.headers || {});
    if (state.credential) headers.set("Authorization", `Bearer ${state.credential}`);
    const response = await fetch(url, { ...options, headers, cache: "no-store" });
    const data = response.status === 204 ? null : await response.json().catch(() => null);
    if (!response.ok) {
      if (response.status === 401) logout(true);
      throw new Error(apiError(data, `Request failed (${response.status})`));
    }
    return data;
  }

  // -- Theme ---------------------------------------------------------------
  function currentTheme() { return document.documentElement.dataset.theme === "dark" ? "dark" : "light"; }

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = theme === "dark" ? "#0c0e17" : "#f6f7fb";
    try { localStorage.setItem("allmemory-theme", theme); } catch (e) { /* ignore */ }
  }

  function toggleTheme() { applyTheme(currentTheme() === "dark" ? "light" : "dark"); }

  // -- Session -------------------------------------------------------------
  function updateViewUI() {
    const name = state.view;
    for (const key of Object.keys(VIEW_TITLES)) {
      $(`view-${key}`).hidden = key !== name;
    }
    $("view-title").textContent = VIEW_TITLES[name];
    document.querySelectorAll("[data-view]").forEach((button) => {
      button.classList.toggle("active", button.dataset.view === name);
    });
  }

  function switchView(name) {
    if (!VIEW_TITLES[name]) return;
    state.view = name;
    updateViewUI();
    if (name === "discussions" && !state.threadLoaded) loadThreads();
  }

  function showViewer(data) {
    state.username = data.username;
    state.tenantId = data.tenant_id || "default";
    $("login-view").hidden = true;
    $("app-view").hidden = false;
    $("current-user").textContent = state.username;
    $("side-agent").textContent = state.username;
    $("current-tenant").textContent = state.tenantId;
    $("side-tenant").textContent = state.tenantId;
    $("profile-avatar").textContent = initials(state.username);
    $("profile-avatar").style.background = avatarStyle(state.username);
    switchView("memories");
  }

  function logout(showLoginError = false) {
    state.credential = null;
    state.username = null;
    state.tenantId = null;
    state.items = [];
    state.threads = [];
    state.selectedId = null;
    state.selectedThreadId = null;
    state.typeFilter = null;
    state.page = 0;
    state.threadLoaded = false;
    $("app-view").hidden = true;
    $("login-view").hidden = false;
    $("credential").value = "";
    $("memory-list").replaceChildren();
    $("detail-panel").replaceChildren();
    $("thread-list").replaceChildren();
    $("thread-detail").replaceChildren();
    $("stat-grid").replaceChildren();
    $("type-filters").replaceChildren();
    $("view-memories").hidden = false;
    $("view-discussions").hidden = true;
    $("view-about").hidden = true;
    state.view = "memories";
    updateViewUI();
    $("login-error").textContent = showLoginError
      ? "The credential is no longer valid. Ask your administrator for a new read-only credential." : "";
    $("login-error").hidden = !showLoginError;
  }

  // -- Stats ---------------------------------------------------------------
  function countBy(values) {
    const counts = new Map();
    values.forEach((value) => counts.set(value, (counts.get(value) || 0) + 1));
    return counts;
  }

  function barsFrom(counts, color) {
    const bars = el("div", "stat-bars");
    const values = Array.from(counts.values()).slice(0, 8);
    const max = Math.max(1, ...values);
    (values.length ? values : [0]).forEach((value) => {
      const bar = el("i");
      bar.style.height = `${6 + 14 * (value / max)}px`;
      bar.style.background = color;
      bars.append(bar);
    });
    return bars;
  }

  function renderStats() {
    const grid = $("stat-grid");
    grid.replaceChildren();
    const items = state.items;
    const typeCounts = countBy(items.map((m) => m.memory_type || "fact"));
    const ownerCounts = countBy(items.map((m) => m.owner_agent_id || "unknown"));
    const tagCounts = countBy(items.flatMap((m) => m.tags || []));
    const statusCounts = countBy(state.threads.map((t) => t.status || "open"));

    const cards = [
      { label: "Memories", value: items.length, note: "on this page", icon: "grid", color: "var(--accent)", tint: "var(--accent-soft)", counts: typeCounts },
      { label: "Contributors", value: ownerCounts.size, note: "agents in results", icon: "user", color: "var(--teal)", tint: "var(--teal-soft)", counts: ownerCounts },
      { label: "Tags", value: tagCounts.size, note: "distinct in results", icon: "tag", color: "var(--amber)", tint: "var(--amber-soft)", counts: tagCounts },
      { label: "Discussions", value: state.threads.length, note: "threads in this tenant", icon: "chat", color: "var(--rose)", tint: "var(--rose-soft)", counts: statusCounts },
    ];

    cards.forEach((card, index) => {
      const node = el("article", "stat-card");
      node.style.setProperty("--stat-color", card.color);
      node.style.setProperty("--stat-tint", card.tint);
      node.style.animation = `fadeUp .4s ${index * 55}ms var(--ease) both`;
      const top = el("div", "stat-top");
      const iconBox = el("span", "stat-icon");
      iconBox.append(icon(card.icon));
      top.append(el("span", "stat-label", card.label), iconBox);
      node.append(top, el("div", "stat-value", card.value), el("div", "stat-note", card.note), barsFrom(card.counts, card.color));
      grid.append(node);
    });
  }

  // -- Type filters --------------------------------------------------------
  function renderTypeFilters() {
    const holder = $("type-filters");
    holder.replaceChildren();
    const counts = countBy(state.items.map((m) => m.memory_type || "fact"));
    const types = Array.from(counts.keys()).sort((a, b) => counts.get(b) - counts.get(a) || a.localeCompare(b));

    const all = el("button", `filter-chip${state.typeFilter === null ? " active" : ""}`, "All");
    all.type = "button";
    all.append(el("span", "chip-count", state.items.length));
    all.addEventListener("click", () => { state.typeFilter = null; renderTypeFilters(); renderList(); });
    holder.append(all);

    types.forEach((type) => {
      const chip = el("button", `filter-chip${state.typeFilter === type ? " active" : ""}`, type);
      chip.type = "button";
      chip.append(el("span", "chip-count", counts.get(type)));
      chip.addEventListener("click", () => {
        state.typeFilter = state.typeFilter === type ? null : type;
        renderTypeFilters();
        renderList();
      });
      holder.append(chip);
    });

    $("clear-filters").hidden = state.typeFilter === null
      && !$("query").value.trim() && !$("source-filter").value.trim();
  }

  // -- Memory list / detail ------------------------------------------------
  function addTag(parent, value, cls = "tag") {
    parent.append(el("span", cls, value));
  }

  function placeholder(title, hint, mark) {
    const wrap = el("div", "detail-placeholder");
    const art = el("span", "placeholder-art");
    art.append(icon(mark || "spark"));
    wrap.append(art, el("strong", "", title), el("p", "", hint));
    return wrap;
  }

  function renderDetail(item) {
    const panel = $("detail-panel");
    panel.replaceChildren();

    const head = el("div", "detail-head");
    const kicker = el("div", "detail-kicker");
    kicker.append(icon("layers"), el("span", "", "Memory detail"));
    head.append(kicker, el("h3", "detail-title", "Source and metadata"));
    panel.append(head);

    const body = el("div", "detail-body");
    body.append(el("p", "detail-content", item.content || "(no content)"));

    const meta = el("div", "meta-grid");
    const rows = [
      ["Source agent", item.owner_agent_id || "Unknown", false],
      ["Type", item.memory_type || "fact", false],
      ["Created", dateLabel(item.created_at), false],
      ["Last updated", dateLabel(item.updated_at), false],
      ["Version", `v${item.version ?? 1}`, false],
      ["Memory ID", item.id || "", true],
    ];
    rows.forEach(([label, value, mono]) => {
      const row = el("div", "meta-row");
      row.append(el("span", "meta-label", label), el("span", `meta-value${mono ? " mono" : ""}`, value));
      meta.append(row);
    });
    if (item.tags && item.tags.length) {
      const row = el("div", "meta-row");
      const tags = el("div", "detail-tags");
      item.tags.forEach((tag) => addTag(tags, tag));
      row.append(el("span", "meta-label", "Tags"), tags);
      meta.append(row);
    }
    body.append(meta);

    const actions = el("div", "detail-actions");
    const copy = el("button", "copy-button");
    copy.type = "button";
    copy.append(icon("copy"), el("span", "", "Copy memory ID"));
    copy.addEventListener("click", () => copyText(item.id));
    actions.append(copy);
    body.append(actions);
    panel.append(body);
  }

  async function copyText(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        const area = el("textarea");
        area.value = text;
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.append(area);
        area.select();
        document.execCommand("copy");
        area.remove();
      }
      toast("Memory ID copied");
    } catch (error) {
      toast("Could not access the clipboard", "error");
    }
  }

  function visibleItems() {
    return state.typeFilter === null
      ? state.items
      : state.items.filter((item) => (item.memory_type || "fact") === state.typeFilter);
  }

  function renderList() {
    const list = $("memory-list");
    list.replaceChildren();
    const items = visibleItems();

    items.forEach((item, index) => {
      const palette = paletteFor(item.memory_type);
      const card = el("article", `memory-card${item.id === state.selectedId ? " selected" : ""}`);
      card.tabIndex = 0;
      card.setAttribute("role", "button");
      card.setAttribute("aria-label", `Memory from ${item.owner_agent_id || "unknown agent"}`);
      card.style.setProperty("--card-color", palette.fg);
      card.style.animationDelay = `${Math.min(index, 8) * 35}ms`;

      const top = el("div", "card-top");
      const topLeft = el("div", "card-top-left");
      const badge = el("span", "type-badge", item.memory_type || "fact");
      badge.style.setProperty("--badge-color", palette.fg);
      badge.style.setProperty("--badge-bg", palette.bg);
      topLeft.append(badge, el("span", "version-badge", `v${item.version ?? 1}`));
      const date = el("span", "card-date", relativeTime(item.updated_at));
      date.title = dateLabel(item.updated_at);
      top.append(topLeft, date);

      const content = el("div", "card-content", item.content || "(no content)");
      const bottom = el("div", "card-bottom");
      const source = el("div", "source-chip");
      const avatar = el("span", "source-avatar", initials(item.owner_agent_id));
      avatar.style.setProperty("--avatar-bg", avatarStyle(item.owner_agent_id));
      source.append(avatar, el("span", "", item.owner_agent_id || "Unknown agent"));
      const tags = el("div", "tag-list");
      (item.tags || []).slice(0, 3).forEach((tag) => addTag(tags, tag));
      if ((item.tags || []).length > 3) addTag(tags, `+${item.tags.length - 3}`, "tag more");
      bottom.append(source, tags);

      card.append(top, content, bottom);
      const select = () => {
        state.selectedId = item.id;
        document.querySelectorAll(".memory-card.selected").forEach((node) => node.classList.remove("selected"));
        card.classList.add("selected");
        renderDetail(item);
      };
      card.addEventListener("click", select);
      card.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); select(); }
      });
      list.append(card);
    });

    $("empty-state").hidden = items.length !== 0;
    const suffix = state.typeFilter ? ` · filtered by ${state.typeFilter}` : "";
    $("result-count").textContent = `· ${items.length} shown${suffix}`;
    $("page-label").textContent = `Page ${state.page + 1}`;
    $("previous-button").disabled = state.page === 0;
    $("next-button").disabled = state.items.length < state.pageSize;

    if (!items.some((item) => item.id === state.selectedId)) {
      state.selectedId = null;
      $("detail-panel").replaceChildren(
        placeholder("Select a memory", "Full content, source agent and metadata appear here.", "layers")
      );
    }
  }

  function resetFilters() {
    $("query").value = "";
    $("source-filter").value = "";
    state.typeFilter = null;
    state.page = 0;
    loadMemories();
  }

  async function loadMemories() {
    $("skeleton").hidden = false;
    $("skeleton").replaceChildren(
      el("div", "skeleton-card"), el("div", "skeleton-card"), el("div", "skeleton-card")
    );
    $("empty-state").hidden = true;
    $("load-error").hidden = true;

    const params = new URLSearchParams({
      limit: String(state.pageSize), offset: String(state.page * state.pageSize),
    });
    const query = $("query").value.trim();
    const owner = $("source-filter").value.trim();
    if (query) params.set("q", query);
    if (owner) params.set("owner_agent_id", owner);

    try {
      const data = await request(`v1/memories?${params}`);
      state.items = (data && data.items) || [];
      $("result-label").textContent = query ? `Results for "${query}"` : "Recent memories";
      renderTypeFilters();
      renderList();
      renderStats();
    } catch (error) {
      if (state.credential) {
        $("load-error").textContent = error.message;
        $("load-error").hidden = false;
      }
    } finally {
      $("skeleton").hidden = true;
    }
  }

  // -- Discussions ---------------------------------------------------------
  function statusClass(status) {
    const s = String(status || "open").toLowerCase();
    return ["open", "resolved", "closed"].includes(s) ? s : "open";
  }

  function badge(kind, text, iconName) {
    const node = el("span", `${kind} ${String(text).toLowerCase()}`);
    node.append(icon(iconName), el("span", "", text));
    return node;
  }

  function renderThreadList() {
    const list = $("thread-list");
    list.replaceChildren();
    state.threads.forEach((thread, index) => {
      const card = el("article", `thread-card${thread.id === state.selectedThreadId ? " selected" : ""}`);
      card.tabIndex = 0;
      card.setAttribute("role", "button");
      card.setAttribute("aria-label", `Discussion: ${thread.subject}`);
      card.style.animationDelay = `${Math.min(index, 8) * 35}ms`;

      const top = el("div", "thread-top");
      const global = String(thread.channel).toLowerCase() === "global";
      top.append(
        badge("channel-badge", global ? "Global" : "Direct", global ? "globe" : "lock"),
        badge("status-badge", statusClass(thread.status), statusClass(thread.status) === "open" ? "chat" : "check")
      );
      const date = el("span", "card-date", relativeTime(thread.updated_at || thread.created_at));
      date.title = dateLabel(thread.updated_at || thread.created_at);
      top.append(date);

      const messages = thread.messages || [];
      const meta = el("div", "thread-meta");
      const creator = el("span");
      creator.append(icon("user"), el("span", "", thread.creator_agent_id || "unknown"));
      const messageCount = el("span");
      messageCount.append(icon("chat"), el("span", "", `${messages.length} message${messages.length === 1 ? "" : "s"}`));
      const recipientCount = el("span");
      const recipientText = global
        ? "Everyone in this tenant"
        : `${(thread.recipients || []).length} recipient${(thread.recipients || []).length === 1 ? "" : "s"}`;
      recipientCount.append(icon("globe"), el("span", "", recipientText));
      meta.append(creator, messageCount, recipientCount);

      const participants = el("div", "thread-participants");
      const names = Array.from(new Set([thread.creator_agent_id, ...(thread.recipients || [])].filter(Boolean)));
      names.slice(0, 6).forEach((name) => {
        const avatar = el("span", "mini-avatar", initials(name));
        avatar.style.background = avatarStyle(name);
        avatar.title = name;
        participants.append(avatar);
      });
      if (names.length > 6) participants.append(el("span", "participant-label", `+${names.length - 6} more`));
      participants.append(el("span", "participant-label", names.length ? names.join(", ") : "No participants"));

      card.append(top, el("div", "thread-subject", thread.subject || "(no subject)"), meta, participants);
      const select = () => {
        state.selectedThreadId = thread.id;
        document.querySelectorAll(".thread-card.selected").forEach((node) => node.classList.remove("selected"));
        card.classList.add("selected");
        renderThreadDetail(thread);
      };
      card.addEventListener("click", select);
      card.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); select(); }
      });
      list.append(card);
    });

    $("thread-count").textContent = `· ${state.threads.length} thread${state.threads.length === 1 ? "" : "s"}`;
    $("thread-empty").hidden = state.threads.length !== 0;
    if (!state.threads.some((t) => t.id === state.selectedThreadId)) {
      state.selectedThreadId = null;
      $("thread-detail").replaceChildren(
        placeholder("Select a discussion", "The conversation timeline and participants appear here.", "chat")
      );
    }
  }

  function renderThreadDetail(thread) {
    const panel = $("thread-detail");
    panel.replaceChildren();
    const global = String(thread.channel).toLowerCase() === "global";

    const head = el("div", "timeline-head");
    const kicker = el("div", "detail-kicker");
    kicker.append(icon(global ? "globe" : "lock"), el("span", "", global ? "Global discussion" : "Direct discussion"));
    const tags = el("div", "timeline-tags");
    tags.append(
      badge("channel-badge", global ? "Global" : "Direct", global ? "globe" : "lock"),
      badge("status-badge", statusClass(thread.status), statusClass(thread.status) === "open" ? "chat" : "check")
    );
    head.append(kicker, el("h3", "timeline-subject", thread.subject || "(no subject)"), tags);

    const body = el("div", "timeline-body");
    const facts = el("div", "meta-grid thread-facts");
    const rows = [
      ["Started by", thread.creator_agent_id || "Unknown"],
      ["Created", dateLabel(thread.created_at)],
      ["Last activity", dateLabel(thread.updated_at)],
      ["Recipients", (thread.recipients || []).join(", ") || "Everyone in this tenant"],
      ["Related memories", String((thread.related_memory_ids || []).length)],
      ["Thread ID", thread.id || ""],
    ];
    rows.forEach(([label, value], index) => {
      const row = el("div", "meta-row");
      row.append(el("span", "meta-label", label), el("span", `meta-value${index === 5 ? " mono" : ""}`, value));
      facts.append(row);
    });
    body.append(facts);

    const messages = thread.messages || [];
    if (!messages.length) {
      body.append(el("p", "no-messages", "No messages in this thread yet."));
    } else {
      const timeline = el("div", "timeline");
      messages.forEach((message) => {
        const item = el("div", "timeline-item");
        const dot = el("span", "timeline-dot", initials(message.sender_agent_id));
        dot.style.background = avatarStyle(message.sender_agent_id);
        const meta = el("div", "timeline-meta");
        meta.append(el("strong", "", message.sender_agent_id || "unknown"));
        const time = el("small", "", relativeTime(message.created_at));
        time.title = dateLabel(message.created_at);
        meta.append(time);
        item.append(dot, meta);
        if (message.reply_to_message_id) item.append(el("div", "reply-note", "↳ reply to an earlier message"));
        item.append(el("div", "message-bubble", message.content || "(empty message)"));
        timeline.append(item);
      });
      body.append(timeline);
    }
    panel.append(head, body);
  }

  async function loadThreads() {
    $("thread-error").hidden = true;
    try {
      const data = await request("v1/negotiations?limit=100");
      state.threads = (data && data.items) || [];
      renderThreadList();
      renderStats();
    } catch (error) {
      if (state.credential) {
        $("thread-error").textContent = error.message;
        $("thread-error").hidden = false;
      }
    } finally {
      state.threadLoaded = true;
    }
  }

  // -- Events --------------------------------------------------------------
  $("login-theme-button").addEventListener("click", toggleTheme);
  $("theme-button").addEventListener("click", toggleTheme);

  $("toggle-credential").addEventListener("click", () => {
    const input = $("credential");
    const button = $("toggle-credential");
    const revealed = input.type === "password";
    input.type = revealed ? "text" : "password";
    button.classList.toggle("revealed", revealed);
    button.setAttribute("aria-label", revealed ? "Hide credential" : "Show credential");
  });

  $("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const username = $("username").value.trim();
    const credential = $("credential").value.trim();
    const button = $("login-submit");
    const label = button.querySelector("span");
    $("login-error").hidden = true;
    button.disabled = true;
    label.textContent = "Signing in…";
    try {
      const response = await fetch("viewer/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, credential }),
        cache: "no-store",
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(apiError(data, "Sign-in failed. Check your username and credential."));
      state.credential = credential;
      showViewer(data);
      await Promise.all([loadMemories(), loadThreads()]);
    } catch (error) {
      $("login-error").textContent = error.message;
      $("login-error").hidden = false;
    } finally {
      button.disabled = false;
      label.textContent = "Sign in to memory space";
    }
  });

  $("logout-button").addEventListener("click", () => logout(false));
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.view));
  });

  $("search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    state.page = 0;
    state.selectedId = null;
    loadMemories();
  });
  $("query").addEventListener("input", () => {
    $("clear-filters").hidden = state.typeFilter === null
      && !$("query").value.trim() && !$("source-filter").value.trim();
  });
  $("source-filter").addEventListener("input", () => {
    $("clear-filters").hidden = state.typeFilter === null
      && !$("query").value.trim() && !$("source-filter").value.trim();
  });
  $("clear-filters").addEventListener("click", resetFilters);
  $("empty-reset").addEventListener("click", resetFilters);
  $("previous-button").addEventListener("click", () => {
    if (state.page > 0) { state.page -= 1; state.selectedId = null; loadMemories(); }
  });
  $("next-button").addEventListener("click", () => {
    if (state.items.length === state.pageSize) { state.page += 1; state.selectedId = null; loadMemories(); }
  });

  $("refresh-button").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.classList.add("is-loading");
    state.threadLoaded = false;
    if (state.view === "discussions") await loadThreads();
    else if (state.view === "memories") await Promise.all([loadMemories(), loadThreads()]);
    else toast("About this space is static");
    button.classList.remove("is-loading");
  });

  document.addEventListener("keydown", (event) => {
    const tag = (event.target && event.target.tagName) || "";
    const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(tag);
    if (event.key === "/" && !typing && !$("app-view").hidden && state.view === "memories") {
      event.preventDefault();
      $("query").focus();
    }
    if (event.key === "Escape" && $("app-view").hidden === false) {
      if (state.typeFilter !== null) { state.typeFilter = null; renderTypeFilters(); renderList(); }
      else if (state.selectedId) { state.selectedId = null; renderList(); }
      else if (document.activeElement === $("query")) $("query").blur();
    }
  });

  async function probeHealth() {
    const label = $("service-status");
    if (!label) return;
    try {
      const response = await fetch("health", { cache: "no-store" });
      const data = await response.json().catch(() => ({}));
      const ok = response.ok && data && data.status === "ok";
      label.textContent = ok ? "Service online" : "Service unavailable";
      label.parentElement.classList.toggle("offline", !ok);
    } catch (error) {
      label.textContent = "Service unreachable";
      label.parentElement.classList.add("offline");
    }
  }

  // Initial paint: restore placeholders before any network call.
  $("detail-panel").replaceChildren(
    placeholder("Select a memory", "Full content, source agent and metadata appear here.", "layers")
  );
  $("thread-detail").replaceChildren(
    placeholder("Select a discussion", "The conversation timeline and participants appear here.", "chat")
  );
  probeHealth();
})();
