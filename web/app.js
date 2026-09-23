/* Newsreader client — vanilla JS, no build step, talks to /api only. */
"use strict";

const $ = (sel) => document.querySelector(sel);

/* after a committed swipe, swallow the synthetic click of that same
   touch sequence — but never a real follow-up tap (any new pointerdown
   clears the window) */
let suppressRowClickUntil = 0;
function armClickSwallow() { suppressRowClickUntil = Date.now() + 450; }
document.addEventListener("pointerdown", () => { suppressRowClickUntil = 0; }, true);
document.addEventListener("click", (ev) => {
  if (Date.now() < suppressRowClickUntil) {
    ev.preventDefault();
    ev.stopPropagation();
    ev.stopImmediatePropagation();
  }
}, true);

const state = {
  q: "",
  categoryId: null,
  sourceId: null,
  tag: null,
  starred: false,
  unread: false,
  range: null,
  starredCount: 0,
  unreadCount: 0,
  showAllTags: false,
  tagFilter: "",
  sort: localStorage.getItem("nr-sort") || "new",
  density: localStorage.getItem("nr-density") || "comfy",
  offset: 0,
  total: 0,
  items: [],
  loading: false,
  done: false,
  sources: [],
  categories: [],
  tags: [],
  selected: null,
  navIndex: null,
};

/* ---------------- helpers ---------------- */
async function api(path, opts) {
  const res = await fetch(`/api${path}`, opts);
  if (!res.ok) {
    let msg = `${res.status}`;
    try { msg = (await res.json()).detail || msg; } catch (_e) { /* keep */ }
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return res.json();
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "textContent") node.textContent = v;
    else if (k === "innerHTML") node.innerHTML = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(child));
  }
  return node;
}

/* inline SVG icons (feather-style strokes) */
const ICONS = {
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20.5 20.5L16.5 16.5"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  x: '<path d="M18 6L6 18M6 6l12 12"/>',
  refresh: '<path d="M20.5 9A8.5 8.5 0 1 0 21 12"/><path d="M21 3v6h-6"/>',
  star: '<path d="M12 2.6l2.9 5.9 6.5.9-4.7 4.6 1.1 6.5L12 17.4l-5.8 3.1 1.1-6.5-4.7-4.6 6.5-.9z"/>',
  starFill: '<path fill="currentColor" d="M12 2.6l2.9 5.9 6.5.9-4.7 4.6 1.1 6.5L12 17.4l-5.8 3.1 1.1-6.5-4.7-4.6 6.5-.9z"/>',
  copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  link: '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><path d="M15 3h6v6"/><path d="M10 14L21 3"/>',
  eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/>',
  eyeOff: '<path d="M3 3l18 18"/><path d="M10.6 5.2A11 11 0 0 1 12 5c6.4 0 10 7 10 7a17.6 17.6 0 0 1-3.4 4.3M6.6 6.7C3.9 8.5 2 12 2 12s3.6 7 10 7c1.5 0 2.9-.4 4.2-1"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
  pause: '<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>',
  play: '<path d="M7 4l13 8-13 8z"/>',
  pencil: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M7 10l5 5 5-5"/><path d="M12 15V3"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M17 8l-5-5-5 5"/><path d="M12 3v12"/>',
  help: '<circle cx="12" cy="12" r="10"/><path d="M9.2 9.2a3 3 0 0 1 5.7 1c0 2-2.9 2.8-2.9 2.8"/><path d="M12 17h.01"/>',
  alert: '<path d="M10.3 4L2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 4a2 2 0 0 0-3.4 0z"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
  checks: '<path d="M2 12.5L6 16.5 11.5 8"/><path d="M12 15l3 3L22 6"/>',
  check: '<path d="M20 6L9 17l-5-5"/>',
  back: '<path d="M15 18l-6-6 6-6"/>',
  inbox: '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.4 5.1L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.4-6.9A2 2 0 0 0 16.8 4H7.2a2 2 0 0 0-1.8 1.1z"/>',
  tag: '<path d="M20.6 13.4l-8.2 8.2a2 2 0 0 1-2.8 0l-8-8V4a1 1 0 0 1 1-1h9.6a2 2 0 0 1 1.4.6l6.9 6.9a2 2 0 0 1 .1 2.9z"/><path d="M7.5 7.5h.01"/>',
  folder: '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
  paper: '<rect x="3.5" y="5" width="17" height="14" rx="2"/><path d="M7 9.5h6M7 13h9M7 16.5h5"/>',
  rows: '<rect x="3" y="4.5" width="18" height="6.5" rx="1.5"/><rect x="3" y="13" width="18" height="6.5" rx="1.5"/>',
  rowsCompact: '<rect x="3" y="3.5" width="18" height="4.5" rx="1"/><rect x="3" y="9.75" width="18" height="4.5" rx="1"/><rect x="3" y="16" width="18" height="4.5" rx="1"/>',
  rss: '<path d="M4 11a9 9 0 0 1 9 9"/><path d="M4 4a16 16 0 0 1 16 16"/><circle cx="5" cy="19" r="1.5"/>',
  dot: '<circle cx="12" cy="12" r="5"/>',
  dotFill: '<circle cx="12" cy="12" r="5" fill="currentColor"/>',
};

function icon(name, cls = "") {
  const span = document.createElement("span");
  span.innerHTML =
    `<svg class="icon${cls ? " " + cls : ""}" viewBox="0 0 24 24" fill="none" stroke="currentColor"` +
    ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    (ICONS[name] || "") + "</svg>";
  return span.firstElementChild;
}

function hydrateIcons() {
  for (const node of document.querySelectorAll("[data-icon]")) {
    node.append(icon(node.dataset.icon));
  }
}

function timeago(epoch) {
  if (!epoch) return "";
  const s = Math.max(0, Date.now() / 1000 - epoch);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 2592000) return `${Math.floor(s / 86400)}d ago`;
  if (s < 31536000) return `${Math.max(1, Math.floor(s / 2592000))}mo ago`;
  return `${Math.floor(s / 31536000)}y ago`;
}

function dateStr(epoch) {
  return epoch ? new Date(epoch * 1000).toLocaleDateString(undefined,
    { year: "numeric", month: "short", day: "numeric" }) : "";
}

const CAT_COLORS = ["#4da3ff", "#7c5cff", "#4ade80", "#f59e0b", "#ec4899", "#14b8a6", "#f97316"];
function catColor(id) { return CAT_COLORS[(id || 0) % CAT_COLORS.length]; }

const TAG_PREVIEW = 12;  // tags shown before the "+N more…" expander

const RANGES = { today: "Today", "7d": "7 days", "30d": "30 days" };

function sinceFromRange(range) {
  if (range === "today") {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    return d.getTime() / 1000;
  }
  if (range === "7d") return Date.now() / 1000 - 7 * 86400;
  if (range === "30d") return Date.now() / 1000 - 30 * 86400;
  return null;
}

/* prefetched full items around the open one so j/k and swipes are instant */
const prefetchCache = new Map();
const prefetching = new Set();

function prefetchAround(idx) {
  for (const j of [idx + 1, idx + 2, idx - 1]) {
    const it = state.items[j];
    if (!it || prefetchCache.has(it.id) || prefetching.has(it.id)) continue;
    prefetching.add(it.id);
    api(`/items/${it.id}`, { priority: "low" })
      .then((full) => {
        // sanitize up front: the render path for a cached article then
        // costs nothing but DOM building
        if (window.DOMPurify && full.content) {
          full.content = DOMPurify.sanitize(full.content);
          full._sanitized = true;
        }
        prefetchCache.set(it.id, full);
        while (prefetchCache.size > 10) {
          prefetchCache.delete(prefetchCache.keys().next().value);
        }
      })
      .catch(() => { /* prefetch is best-effort */ })
      .finally(() => prefetching.delete(it.id));
  }
}

function dropPrefetch(id) { prefetchCache.delete(id); }

function escRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

function highlightNodes(text, q) {
  if (!text) return [];
  const tokens = (q || "").toLowerCase().split(/\s+/).filter((t) => t.length > 1);
  if (!tokens.length) return [text];
  const re = new RegExp(tokens.map(escRe).join("|"), "gi");
  const out = [];
  let last = 0;
  for (const m of text.matchAll(re)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    out.push(el("mark", {}, m[0]));
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

let toastTimer;
function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("error", isError);
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 3200);
}

/* toast with a single action button (e.g. "Dismissed — Undo") */
function actionToast(msg, actionLabel, onAction) {
  const t = $("#toast");
  t.classList.remove("error");
  t.replaceChildren(document.createTextNode(msg));
  t.append(el("button", {
    class: "toast-action", type: "button", textContent: actionLabel,
    onclick: async () => {
      clearTimeout(toastTimer);
      t.classList.remove("show");
      await onAction();
    },
  }));
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 6000);
}

/* clipboard write with an execCommand fallback for non-secure contexts */
async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    try { await navigator.clipboard.writeText(text); return true; }
    catch (_e) { /* fall through to the legacy path */ }
  }
  const ta = el("textarea", { style: "position:fixed;left:-9999px;top:0" });
  ta.value = text;
  document.body.append(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (_e) { /* unsupported */ }
  ta.remove();
  return ok;
}

/* ---------------- clipboard ---------------- */

/* ---------------- persistent failure notes ----------------
   One card per failed source update. Never auto-dismissed; the error
   detail (HTTP status, response excerpt, exception chain) is collapsed
   behind a Details toggle. Errors are deduplicated per source + text so
   polls and sidebar loads don't stack duplicates. */
const noteState = { seen: new Set() };

function notesChanged() {
  const notes = $("#notes");
  const n = notes.querySelectorAll(".note").length;
  $("#notesHead").classList.toggle("hidden", n < 2);
  $("#notesCount").textContent = n ? `${n} source(s) failed to update` : "";
}

function showSourceError(sourceId, name, errText) {
  const key = `s${sourceId}:${errText || ""}`;
  if (noteState.seen.has(key)) return;
  noteState.seen.add(key);
  errText = errText || "unknown error";
  const short = errText.length > 180 ? `${errText.slice(0, 180)}…` : errText;
  const details = el("pre", { class: "note-details hidden" }, errText);
  const toggle = el("button", {
    class: "note-toggle", textContent: "Details ▸",
    onclick: () => {
      const hidden = details.classList.toggle("hidden");
      toggle.textContent = hidden ? "Details ▸" : "Details ▾";
    },
  });
  const card = el("div", { class: "note", role: "alert" },
    el("div", { class: "note-head" },
      el("span", { class: "note-title" }, icon("alert"),
        `Failed to update “${name}”`),
      el("button", {
        class: "x", title: "Dismiss this notification", "aria-label": "Dismiss",
        onclick: () => { card.remove(); notesChanged(); },
      }, icon("x"))),
    el("div", { class: "note-body" }, short),
    el("div", { class: "note-foot" }, toggle),
    details);
  $("#notes").append(card);
  notesChanged();
}

function checkSourceErrors(sources) {
  for (const s of sources || []) {
    if (s.last_status === "error" && s.last_error) {
      showSourceError(s.id, s.name || s.url, s.last_error);
    }
  }
}

/* ---------------- data ---------------- */
async function loadItems(reset = true) {
  if (state.loading || (!reset && state.done)) return;
  state.loading = true;
  if (reset) {
    state.offset = 0;
    state.done = false;
    state.navIndex = null;
    $("#itemList").replaceChildren(
      el("div", { class: "loading" }, el("div", { class: "spinner" })));
  } else if (state.offset > 0) {
    $("#itemList").append(el("div", { class: "loading", id: "moreLoading" },
      el("div", { class: "spinner" })));
  }
  const params = new URLSearchParams({
    sort: state.sort, limit: "50", offset: String(state.offset),
  });
  if (state.q) params.set("q", state.q);
  if (state.categoryId) params.set("category_id", state.categoryId);
  if (state.sourceId) params.set("source_id", state.sourceId);
  if (state.tag) params.set("tag", state.tag);
  if (state.starred) params.set("starred", "true");
  if (state.unread) params.set("unread", "true");
  const since = sinceFromRange(state.range);
  if (since) params.set("since", String(since));
  try {
    const data = await api(`/items?${params}`);
    state.total = data.total;
    const known = new Set(state.items.map((i) => i.id));
    const novel = data.items.filter((i) => !known.has(i.id));
    state.items = reset ? data.items : state.items.concat(novel);
    state.offset += data.items.length;
    if (data.items.length === 0 && !reset) state.done = true;
    if (state.offset >= data.total) state.done = true;
    if (reset) $("#itemList").replaceChildren();
    renderItems(reset ? data.items : novel);
    renderChips();
  } catch (err) {
    toast(`Failed to load items: ${err.message}`, true);
  } finally {
    $("#moreLoading")?.remove();
    state.loading = false;
    if (state.items.length === 0 && state.total === 0) {
      $("#itemList").replaceChildren(emptyState());
      $("#endNote").classList.add("hidden");
    }
  }
}

function emptyState() {
  const title = state.q ? "No results" : "Nothing here yet";
  let body = state.q
    ? `Nothing matches “${state.q}” with the current filters.`
    : state.starred || state.tag || state.sourceId || state.categoryId || state.range
      ? "No articles match these filters."
      : "Hit “Refresh all” to fetch your sources.";
  if (state.unread && !state.q) body = "You're all caught up — nothing unread here.";
  return el("div", { class: "empty-state" }, el("h2", {}, title), el("p", {}, body));
}

async function loadSidebar() {
  try {
    const [cats, srcs, tags, health] = await Promise.all([
      api("/categories"), api("/sources"), api("/tags"), api("/health"),
    ]);
    state.categories = cats.categories;
    state.sources = srcs.sources;
    state.tags = tags.tags;
    state.starredCount = health.counts.starred_items || 0;
    state.unreadCount = health.counts.unread_items || 0;
    $("#dbStats").textContent =
      `${health.counts.items} items · ${state.unreadCount} unread · ${health.counts.sources} sources`;
    renderSidebar();
    checkSourceErrors(state.sources);
  } catch (err) {
    toast(`Failed to load sidebar: ${err.message}`, true);
  }
}

function renderSidebar() {
  renderNav();
  renderCategories();
  renderSources();
  renderTags();
}

/* ---------------- rendering: sidebar ---------------- */
function isAllView() {
  return state.categoryId === null && state.sourceId === null &&
    !state.starred && !state.unread;
}

function sideBadge(unread, total) {
  const n = unread > 0 ? unread : total;
  return el("span", {
    class: "count" + (unread > 0 ? " hot" : ""),
    title: `${unread} unread · ${total} total`,
  }, String(n));
}

function renderNav() {
  const list = $("#navList");
  list.replaceChildren();
  const allActive = isAllView() && !state.tag;
  list.append(el("li", {
    class: allActive ? "active" : "",
    onclick: () => setView({ categoryId: null, sourceId: null, tag: null,
      starred: false, unread: false }),
  }, icon("inbox"), el("span", { class: "src-title" }, "All items"),
    el("span", { class: "count" },
      String(state.sources.reduce((n, s) => n + (s.hidden ? 0 : s.item_count), 0)))));
  list.append(el("li", {
    class: state.unread ? "active" : "",
    title: "Only articles you haven't opened",
    onclick: () => setView({ unread: !state.unread, starred: false }),
  }, icon("dotFill"), el("span", { class: "src-title" }, "Unread"),
    sideBadge(state.unreadCount, state.unreadCount)));
  list.append(el("li", {
    class: state.starred ? "active" : "",
    title: "Show only starred articles",
    onclick: () => setView({ starred: !state.starred }),
  }, icon("star"), el("span", { class: "src-title" }, "Starred"),
    el("span", { class: "count" }, String(state.starredCount))));
}

function renderCategories() {
  const list = $("#catList");
  list.replaceChildren();
  for (const cat of state.categories) {
    const actions = el("span", { class: "src-actions" },
      el("span", {
        class: "mini-btn", title: "Rename category", "aria-label": "Rename category",
        onclick: async (ev) => {
          ev.stopPropagation();
          const name = prompt("Rename category:", cat.name);
          if (!name || name.trim() === cat.name) return;
          try {
            await api(`/categories/${cat.id}`, { method: "PATCH",
              body: JSON.stringify({ name: name.trim() }),
              headers: { "Content-Type": "application/json" } });
            loadSidebar();
          } catch (err) { toast(err.message, true); }
        },
      }, icon("pencil")),
      el("span", {
        class: "mini-btn danger", title: "Delete category", "aria-label": "Delete category",
        onclick: async (ev) => {
          ev.stopPropagation();
          if (!confirm(`Delete category “${cat.name}”? Sources become uncategorized.`)) return;
          try {
            await api(`/categories/${cat.id}`, { method: "DELETE" });
            if (state.categoryId === cat.id) setView({ categoryId: null });
            loadSidebar();
          } catch (err) { toast(err.message, true); }
        },
      }, icon("x")));
    const li = el("li", {
      class: state.categoryId === cat.id ? "active" : "",
      onclick: () => setView({ categoryId: cat.id, sourceId: null }),
    },
      el("span", { class: "dot", style: `background:${catColor(cat.id)}` }),
      el("span", { class: "src-title" }, cat.name),
      actions,
      sideBadge(cat.unread_count, cat.item_count));
    list.append(li);
  }
  // fill add-source select
  const sel = $("#addCatSel");
  sel.replaceChildren(el("option", { value: "" }, "— none —"));
  for (const cat of state.categories) {
    sel.append(el("option", { value: cat.id }, cat.name));
  }
}

function renderSources() {
  const list = $("#srcList");
  list.replaceChildren();
  const grouped = new Map();
  for (const src of state.sources) {
    const key = src.category_name || "Uncategorized";
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(src);
  }
  for (const [group, srcs] of grouped) {
    list.append(el("li", { class: "side-group" }, group));
    for (const src of srcs) {
      const actions = el("span", { class: "src-actions" });
      actions.append(el("span", {
        class: "mini-btn",
        title: src.hidden ? "Show articles in the feed" : "Hide articles from the feed",
        "aria-label": src.hidden ? "Show source" : "Hide source",
        onclick: async (ev) => {
          ev.stopPropagation();
          try {
            await api(`/sources/${src.id}`, { method: "PATCH",
              body: JSON.stringify({ hidden: !src.hidden }),
              headers: { "Content-Type": "application/json" } });
            loadSidebar();
            // the feed only changes when the toggled source isn't the explicit view
            if (state.sourceId !== src.id) refresh();
          } catch (err) { toast(err.message, true); }
        },
      }, icon(src.hidden ? "eyeOff" : "eye")));
      actions.append(el("span", {
        class: "mini-btn", title: src.enabled ? "Disable" : "Enable",
        "aria-label": src.enabled ? "Disable source" : "Enable source",
        onclick: async (ev) => {
          ev.stopPropagation();
          try {
            await api(`/sources/${src.id}`, { method: "PATCH",
              body: JSON.stringify({ enabled: !src.enabled }),
              headers: { "Content-Type": "application/json" } });
            loadSidebar();
          } catch (err) { toast(err.message, true); }
        },
      }, icon(src.enabled ? "pause" : "play")));
      actions.append(el("span", {
        class: "mini-btn", title: "Refresh this source", "aria-label": "Refresh source",
        onclick: async (ev) => {
          ev.stopPropagation();
          try {
            await api("/refresh", { method: "POST",
              body: JSON.stringify({ source_id: src.id, force: true }),
              headers: { "Content-Type": "application/json" } });
            toast(`Refreshing ${src.name || src.url}…`);
            pollRefresh();
          } catch (err) { toast(err.message, true); }
        },
      }, icon("refresh")));
      actions.append(el("span", {
        class: "mini-btn", title: "Edit source", "aria-label": "Edit source",
        onclick: (ev) => {
          ev.stopPropagation();
          openEditDialog(src);
        },
      }, icon("pencil")));
      const li = el("li", {
        class: (state.sourceId === src.id ? "active" : "") + (src.hidden ? " muted" : ""),
        title: src.last_error ? `Last error: ${src.last_error}` : src.url,
        onclick: () => setView({ sourceId: src.id, categoryId: null }),
      },
        el("span", {
          class: "dot",
          style: `background:${src.hidden ? "#555" : src.enabled ? catColor(src.id) : "#555"}`,
        }),
        el("span", { class: "src-title" }, src.name || src.url),
        src.last_status === "error"
          ? el("span", { class: "err", title: src.last_error }, icon("alert")) : null,
        actions,
        sideBadge(src.unread_count, src.item_count));
      list.append(li);
    }
  }
}

function renderTags() {
  const cloud = $("#tagCloud");
  cloud.replaceChildren();
  const tags = state.tags.filter((t) => t.item_count > 0 || t.name === state.tag);
  if (!tags.length) {
    cloud.append(el("span", { style: "color:var(--text-3);font-size:13px" }, "No tags yet."));
    $("#tagFilterWrap").classList.add("hidden");
    return;
  }

  let visible;
  if (state.showAllTags) {
    const needle = state.tagFilter.toLowerCase();
    visible = needle ? tags.filter((t) => t.name.toLowerCase().includes(needle)) : tags;
  } else {
    visible = tags.slice(0, TAG_PREVIEW);
    if (state.tag && !visible.some((t) => t.name === state.tag)) {
      const active = tags.find((t) => t.name === state.tag);
      if (active) visible = visible.slice(0, TAG_PREVIEW - 1).concat(active);
    }
  }

  const chipFor = (t) => el("button", {
    class: "chip" + (state.tag === t.name ? " accent" : ""),
    type: "button",
    title: `Filter by tag “${t.name}”`,
    onclick: () => setView({ tag: state.tag === t.name ? null : t.name }),
  }, t.name, el("span", { class: "count" }, String(t.item_count)));
  for (const t of visible) cloud.append(chipFor(t));

  if (state.showAllTags) {
    if (!visible.length) {
      cloud.append(el("span", { style: "color:var(--text-3);font-size:13px" }, "No tags match."));
    }
    cloud.append(el("button", {
      class: "chip toggle", type: "button", title: "Collapse the tag list",
      onclick: () => {
        state.showAllTags = false; state.tagFilter = "";
        $("#tagFilter").value = ""; renderTags();
      },
    }, "Show common only"));
  } else if (tags.length > TAG_PREVIEW) {
    cloud.append(el("button", {
      class: "chip toggle", type: "button",
      title: "Show every tag, with a filter box",
      onclick: () => { state.showAllTags = true; renderTags(); $("#tagFilter").focus(); },
    }, `+${tags.length - TAG_PREVIEW} more…`));
  }
  $("#tagFilterWrap").classList.toggle("hidden", !state.showAllTags);
}

function listTitle() {
  let base;
  if (state.sourceId) {
    const src = state.sources.find((s) => s.id === state.sourceId);
    base = src ? src.name || src.url : "Source";
  } else if (state.categoryId) {
    const cat = state.categories.find((c) => c.id === state.categoryId);
    base = cat ? cat.name : "Category";
  } else if (state.starred) base = "Starred";
  else if (state.unread) base = "Unread";
  else base = "All articles";
  return state.unread && base !== "Unread" ? `Unread · ${base}` : base;
}

function renderChips() {
  $("#listTitle").textContent = state.tag
    ? `${listTitle()} · #${state.tag}` : listTitle();
  $("#listCount").textContent = state.total === 1 ? "1 article" : `${state.total} articles`;
  for (const btn of document.querySelectorAll("#rangeChips [data-range]")) {
    btn.classList.toggle("active", state.range === (btn.dataset.range || null));
  }
  const chips = $("#filterChips");
  chips.replaceChildren();
  if (state.q) {
    chips.append(chip(`Search: “${state.q}”`, () => setView({ q: "" }, { replace: true })));
  }
  if (state.categoryId) {
    const cat = state.categories.find((c) => c.id === state.categoryId);
    chips.append(chip(`Category: ${cat ? cat.name : state.categoryId}`,
      () => setView({ categoryId: null })));
  }
  if (state.sourceId) {
    const src = state.sources.find((s) => s.id === state.sourceId);
    chips.append(chip(`Source: ${src ? src.name || src.url : state.sourceId}`,
      () => setView({ sourceId: null })));
  }
  if (state.tag) chips.append(chip(`Tag: ${state.tag}`, () => setView({ tag: null })));
  if (state.starred) chips.append(chip("★ Starred", () => setView({ starred: false })));
  if (state.unread) chips.append(chip("Unread", () => setView({ unread: false })));
  $("#sortSel").value = state.sort;
  $("#endNote").classList.toggle("hidden", !state.done || state.total === 0);
}

function chip(text, onRemove) {
  return el("span", { class: "chip accent" }, text,
    el("span", { class: "x", role: "button", "aria-label": "Remove filter",
      onclick: onRemove }, icon("x")));
}

/* ---------------- rendering: list ---------------- */
function rowActions(item) {
  const openBtn = el("a", {
    class: "mini-btn", href: item.url, target: "_blank", rel: "noopener noreferrer",
    title: "Open original", "aria-label": "Open original",
    onclick: (ev) => ev.stopPropagation(),
  }, icon("link"));
  const copyBtn = el("button", {
    class: "mini-btn", title: "Copy link", "aria-label": "Copy link",
    onclick: async (ev) => {
      ev.stopPropagation();
      if (!item.url) return toast("No link for this item", true);
      if (await copyText(item.url)) {
        copyBtn.replaceChildren(icon("check"));
        setTimeout(() => copyBtn.replaceChildren(icon("copy")), 1200);
      } else toast("Copy failed", true);
    },
  }, icon("copy"));
  const starBtn = el("button", {
    class: "mini-btn star" + (item.starred ? " star-on" : ""),
    title: item.starred ? "Unstar" : "Star", "aria-label": item.starred ? "Unstar" : "Star",
    onclick: async (ev) => {
      ev.stopPropagation();
      try { await toggleStar(item); } catch (err) { toast(err.message, true); }
    },
  }, icon(item.starred ? "starFill" : "star"));
  const dismissBtn = el("button", {
    class: "mini-btn danger", title: "Dismiss (undoable)", "aria-label": "Dismiss",
    onclick: (ev) => {
      ev.stopPropagation();
      dismissItem(item);
    },
  }, icon("x"));
  return el("div", { class: "row-actions" }, openBtn, copyBtn, starBtn, dismissBtn);
}

/* dismiss with an undo window — the action is one tap and common enough
   that an irreversible path was the sharpest edge in the UI */
async function dismissItem(item) {
  const row = document.querySelector(`.row[data-id="${item.id}"]`);
  const parent = row ? row.parentNode : null;
  const nextSib = row ? row.nextSibling : null;
  const idx = state.items.findIndex((i) => i.id === item.id);
  try {
    await api(`/items/${item.id}/dismiss`, { method: "POST" });
  } catch (err) { toast(err.message, true); return; }
  dropPrefetch(item.id);
  removeCard(item);
  refreshSidebar();
  actionToast("Article dismissed", "Undo", async () => {
    try {
      await api(`/items/${item.id}/dismiss`, { method: "DELETE" });
      if (idx >= 0 && !state.items.some((i) => i.id === item.id)) {
        state.items.splice(idx, 0, item);
        state.total += 1;
        state.navIndex = null;
        if (row && parent && parent.isConnected) {
          row.style.transform = "";
          row.style.transition = "";
          row.style.opacity = "";
          row.classList.remove("armed-dismiss", "armed-star");
          parent.insertBefore(row, nextSib && nextSib.isConnected ? nextSib : null);
        } else {
          loadItems(true);
        }
        renderChips();
      } else if (idx < 0 && !state.items.some((i) => i.id === item.id)) {
        loadItems(true);   // the list moved on; a reload brings it back
      }
      refreshSidebar();
    } catch (err) { toast(err.message, true); }
  });
}

function removeCard(item) {
  state.items = state.items.filter((i) => i.id !== item.id);
  state.total = Math.max(0, state.total - 1);
  state.navIndex = null;
  const node = document.querySelector(`.row[data-id="${item.id}"]`);
  if (node) node.remove();
  if (state.selected && state.selected.id === item.id) closeReader();
  renderChips();
  if (!state.items.length && !state.done) loadItems(true);
}

function rowNoimg() {
  return el("div", { class: "row-noimg" }, icon("paper"));
}

function renderItems(items, opts = {}) {
  const list = $("#itemList");
  const frag = document.createDocumentFragment();
  for (const item of items) {
    const thumb = item.image_url
      ? el("img", { src: item.image_url, loading: "lazy", alt: "",
          onerror: (ev) => { ev.target.replaceWith(rowNoimg()); } })
      : rowNoimg();
    const thumbBox = el("div", { class: "row-thumb" }, thumb);
    if (youtubeId(item.url)) {
      thumbBox.append(el("span", { class: "play-badge", "aria-hidden": "true" },
        icon("play")));
    }
    const titleLink = el("a", {
      class: "title-link", href: item.url, target: "_blank", rel: "noopener noreferrer",
      onclick: (ev) => ev.stopPropagation(),
    }, ...highlightNodes(item.title || "(untitled)", state.q));
    const readDot = el("button", {
      class: "read-dot" + (item.read_at ? "" : " on"), type: "button",
      title: item.read_at ? "Mark unread" : "Mark read",
      "aria-label": item.read_at ? "Mark unread" : "Mark read",
      onclick: (ev) => {
        ev.stopPropagation();
        toggleRead(item);
      },
    }, icon(item.read_at ? "dot" : "dotFill"));
    const row = el("article", {
      class: "row" + (item.read_at ? " read" : ""),
      "data-id": String(item.id),
      tabindex: "0",
      onclick: () => {
        if (Date.now() < suppressRowClickUntil) return;
        selectItem(item.id);
      },
      onkeydown: (ev) => {
        if (ev.key === "Enter") {
          ev.preventDefault();
          ev.stopPropagation();
          selectItem(item.id);
        }
      },
    },
      thumbBox,
      el("div", { class: "row-body" },
        el("h3", { class: "row-title" }, titleLink),
        el("div", { class: "row-meta" },
          readDot,
          el("span", { class: "src" }, item.source_name || ""),
          item.starred ? el("span", { class: "starred", title: "Starred" }, "★") : null,
          item.category_name ? el("span", {}, item.category_name) : null,
          el("span", {}, timeago(item.published_at))),
        item.summary
          ? el("p", { class: "row-summary" }, ...highlightNodes(item.summary, state.q)) : null,
        (item.tags || []).length ? el("div", { class: "row-tags" },
          item.tags.slice(0, 4).map((t) => el("button", {
            class: "chip", type: "button",
            onclick: (ev) => {
              ev.stopPropagation();
              setView({ tag: t });
            },
          }, t)),
          item.tags.length > 4 ? el("span", { class: "chip" }, `+${item.tags.length - 4}`) : null
        ) : null),
      rowActions(item));
    frag.append(row);
  }
  if (opts.prepend) list.prepend(frag);
  else list.append(frag);
  syncRowSelection(state.selected ? state.selected.id : null);
}

function syncRowSelection(id) {
  for (const row of document.querySelectorAll(".row")) {
    row.classList.toggle("selected", id !== null && row.dataset.id === String(id));
  }
}

function syncRowRead(id) {
  const item = state.items.find((i) => i.id === id) ||
    (state.selected && state.selected.id === id ? state.selected : null);
  const row = document.querySelector(`.row[data-id="${id}"]`);
  if (!row || !item) return;
  row.classList.toggle("read", !!item.read_at);
  const dot = row.querySelector(".read-dot");
  if (dot) {
    dot.replaceChildren(icon(item.read_at ? "dot" : "dotFill"));
    dot.classList.toggle("on", !item.read_at);
    dot.title = item.read_at ? "Mark unread" : "Mark read";
    dot.setAttribute("aria-label", dot.title);
  }
}

/* ---------------- item actions ---------------- */
async function toggleStar(item) {
  const res = await api(`/items/${item.id}/star`,
    { method: item.starred ? "DELETE" : "POST" });
  item.starred = res.starred;
  for (const it of state.items) if (it.id === item.id) it.starred = res.starred;
  if (state.selected && state.selected.id === item.id) state.selected.starred = res.starred;
  dropPrefetch(item.id);
  syncStarUI(item.id);
  refreshSidebar();
  if (state.starred && !res.starred) removeCard(item);
  return res.starred;
}

function syncStarUI(id) {
  const item = state.items.find((i) => i.id === id) ||
    (state.selected && state.selected.id === id ? state.selected : null);
  const starred = !!(item && item.starred);
  const row = document.querySelector(`.row[data-id="${id}"]`);
  if (row && item) {
    const meta = row.querySelector(".row-meta .starred");
    if (starred && !meta) {
      const head = row.querySelector(".row-meta");
      head.insertBefore(el("span", { class: "starred", title: "Starred" }, "★"),
        head.children[1] || null);
    } else if (!starred && meta) meta.remove();
    const btn = row.querySelector(".row-actions .mini-btn.star");
    if (btn) {
      btn.replaceChildren(icon(starred ? "starFill" : "star"));
      btn.classList.toggle("star-on", starred);
      btn.title = starred ? "Unstar" : "Star";
    }
  }
  if (state.selected && state.selected.id === id) updateReaderActions(state.selected);
}

/* ---------------- youtube facade ----------------
   Click-to-load embeds: a thumbnail + play button costs nothing and loads
   nothing from Google; the youtube-nocookie iframe only appears on tap. */
function youtubeId(url) {
  if (!url) return null;
  let u;
  try { u = new URL(url, location.href); } catch (_e) { return null; }
  const host = u.hostname.replace(/^(www\.|m\.|music\.)/, "");
  const ok = (id) => (/^[\w-]{6,20}$/.test(id) ? id : null);
  if (host === "youtu.be") return ok(u.pathname.split("/").filter(Boolean)[0] || "");
  if (host === "youtube.com" || host === "youtube-nocookie.com") {
    if (u.pathname === "/watch") return ok(u.searchParams.get("v") || "");
    const m = u.pathname.match(/^\/(?:embed|shorts|live|v)\/([\w-]{6,20})/);
    return m ? m[1] : null;
  }
  return null;
}

function youtubeFacade(vid, title, thumbUrl) {
  const box = el("div", {
    class: "yt-facade", role: "button", tabindex: "0",
    "aria-label": `Play video: ${title || "YouTube"}`,
  },
    el("img", {
      src: thumbUrl || `https://i.ytimg.com/vi/${vid}/hqdefault.jpg`,
      alt: "", loading: "lazy", referrerpolicy: "no-referrer",
    }),
    el("span", { class: "yt-play", "aria-hidden": "true" }, icon("play")),
    el("span", { class: "yt-label" }, "YouTube · tap to play"));
  const load = () => {
    const iframe = el("iframe", {
      class: "yt-frame",
      src: `https://www.youtube-nocookie.com/embed/${vid}?autoplay=1&rel=0`,
      title: title || "YouTube video",
      allow: "accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share",
      allowfullscreen: "",
      referrerpolicy: "strict-origin-when-cross-origin",
    });
    box.replaceWith(iframe);
  };
  box.addEventListener("click", load);
  box.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); load(); }
  });
  return box;
}

/* the reader's lead visual: a player facade for videos, else the hero image */
function heroNode(item) {
  const vid = youtubeId(item.url);
  if (vid) return youtubeFacade(vid, item.title, item.image_url);
  return item.image_url
    ? el("img", { class: "rd-hero", src: item.image_url, alt: "",
        onerror: (ev) => ev.target.remove() })
    : null;
}

/* detail article body: sanitized HTML when DOMPurify is available,
   otherwise plain text so a missing sanitizer can never inject markup.
   `sanitized` = already run through DOMPurify (prefetch path). YouTube
   links become click-to-load facades (ids in `skip` stay plain links). */
function contentNode(html, sanitized = false, skip = new Set()) {
  const div = el("div", { class: "content" });
  if (!window.DOMPurify) {
    div.classList.add("plain");
    div.textContent = html || "";
    return div;
  }
  div.innerHTML = sanitized ? (html || "") : DOMPurify.sanitize(html || "");
  for (const a of [...div.querySelectorAll("a[href]")]) {
    const vid = youtubeId(a.getAttribute("href"));
    if (vid && !skip.has(vid)) {
      skip.add(vid);   // one player per video even if linked twice
      a.replaceWith(youtubeFacade(vid, a.textContent.trim() || "YouTube video"));
      continue;
    }
    a.setAttribute("target", "_blank");
    a.setAttribute("rel", "noopener noreferrer");
  }
  for (const img of div.querySelectorAll("img")) img.setAttribute("loading", "lazy");
  return div;
}

/* ---------------- reader pane ---------------- */
let selectToken = 0;

async function selectItem(id, opts = {}) {
  const token = ++selectToken;
  let item = opts.force ? null : (prefetchCache.get(id) ||
    (id === (state.selected && state.selected.id) ? state.selected : null));
  if (!item) {
    try { item = await api(`/items/${id}`); }
    catch (err) { toast(err.message, true); return false; }
    if (token !== selectToken) return true;  // superseded: a newer selection owns the pane
  }
  state.selected = item;
  const idx = state.items.findIndex((i) => i.id === item.id);
  if (idx >= 0) state.navIndex = idx;
  renderReader(item, opts.slide || 0);
  markRead(item);
  syncRowSelection(item.id);
  $("#reader").classList.add("open");
  replaceItemHash();
  prefetchAround(idx >= 0 ? idx : state.items.findIndex((i) => i.id === item.id));
  return true;
}

/* server-side read state: opening an article marks it read everywhere */
let sideTimer;
function scheduleSidebarRefresh() {
  clearTimeout(sideTimer);
  sideTimer = setTimeout(() => loadSidebar(), 1200);
}

function markRead(item) {
  if (item.read_at) return;
  item.read_at = Date.now() / 1000;
  const listed = state.items.find((i) => i.id === item.id);
  if (listed) listed.read_at = item.read_at;
  syncRowRead(item.id);
  scheduleSidebarRefresh();
  api(`/items/${item.id}/read`, { method: "POST" }).catch(() => {});
}

async function toggleRead(item) {
  const read = !item.read_at;
  try {
    await api(`/items/${item.id}/read`, { method: read ? "POST" : "DELETE" });
  } catch (err) { toast(err.message, true); return; }
  item.read_at = read ? Date.now() / 1000 : null;
  const listed = state.items.find((i) => i.id === item.id);
  if (listed) listed.read_at = item.read_at;
  if (state.selected && state.selected.id === item.id) state.selected.read_at = item.read_at;
  dropPrefetch(item.id);
  syncRowRead(item.id);
  scheduleSidebarRefresh();
  if (state.unread && read) removeCard(item);
}

/* mark only what is loaded (matches the button's promise); a toast action
   offers whole-view catch-up as a deliberate second step */
async function markAllRead() {
  if (!state.items.length) return;
  const ids = state.items.map((i) => i.id);
  const changed = state.items.filter((i) => !i.read_at).length;
  let res;
  try {
    res = await api("/items/read-all", { method: "POST",
      body: JSON.stringify({ read: true, ids }),
      headers: { "Content-Type": "application/json" } });
  } catch (err) { toast(err.message, true); return; }
  const now = Date.now() / 1000;
  for (const it of state.items) {
    if (!it.read_at) {
      it.read_at = now;
      syncRowRead(it.id);
    }
  }
  scheduleSidebarRefresh();
  const msg = changed
    ? `Marked ${changed} visible article${changed === 1 ? "" : "s"} read`
    : "Visible articles were already read";
  const beyond = state.total - state.items.length;
  if (beyond > 0 && !state.q) {
    actionToast(msg, `Mark all ${state.total}`, markFilterRead);
  } else {
    toast(msg);
  }
  if (state.unread) loadItems(true);
  return res;
}

/* whole-view catch-up: every row matching the current filters, seen or not */
function filterReadBody() {
  const body = { read: true };
  if (state.sourceId) body.source_id = state.sourceId;
  if (state.categoryId) body.category_id = state.categoryId;
  if (state.tag) body.tag = state.tag;
  if (state.starred) body.starred = true;
  const since = sinceFromRange(state.range);
  if (since) body.since = since;
  return body;
}

async function markFilterRead() {
  try {
    const res = await api("/items/read-all", { method: "POST",
      body: JSON.stringify(filterReadBody()),
      headers: { "Content-Type": "application/json" } });
    const now = Date.now() / 1000;
    for (const it of state.items) {
      if (!it.read_at) {
        it.read_at = now;
        syncRowRead(it.id);
      }
    }
    scheduleSidebarRefresh();
    toast(`Everything in this view marked read (${res.updated})`);
    if (state.unread) loadItems(true);
  } catch (err) { toast(err.message, true); }
}

function closeReader(opts = {}) {
  state.selected = null;
  selectToken++;
  $("#reader").classList.remove("open");
  $("#readerBody").classList.add("hidden");
  $("#readerEmpty").classList.remove("hidden");
  syncRowSelection(null);
  if (!opts.silent) replaceItemHash();
}

function updateReaderActions(item) {
  const star = $("#readerStar");
  star.replaceChildren(icon(item.starred ? "starFill" : "star"),
    el("span", { class: "btn-label" }, item.starred ? "Starred" : "Star"));
  star.classList.toggle("star-on", item.starred);
  $("#readerCopy").replaceChildren(icon("copy"), el("span", { class: "btn-label" }, "Copy link"));
  $("#readerDismiss").replaceChildren(icon("x"), el("span", { class: "btn-label" }, "Dismiss"));
  $("#readerOpen").replaceChildren(icon("link"), el("span", { class: "btn-label" }, "Original"));
  $("#readerOpen").href = item.url || "#";
}

function renderReader(item, slide = 0) {
  updateReaderActions(item);
  const inner = $("#readerInner");
  // don't show the summary when it is just a truncated copy of the body
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim().slice(0, 300);
  const summaryDup = item.summary && item.content &&
    norm(item.content).includes(norm(item.summary));

  const tagEditor = el("div", { class: "tag-editor" },
    el("span", { class: "tag-editor-label" }, icon("tag"), "Tags"),
    item.tags.map((t) => el("span", { class: "chip" }, t,
      el("span", {
        class: "tagdel", role: "button", "aria-label": `Remove tag ${t}`,
        onclick: async () => {
          try {
            const { tags } = await api(
              `/items/${item.id}/tags/${encodeURIComponent(t)}`, { method: "DELETE" });
            await rerenderReader(item.id, tags);
          } catch (err) { toast(err.message, true); }
        },
      }, icon("x")))),
    el("input", { placeholder: "+ tag", maxlength: "40", "aria-label": "Add tag",
      onkeydown: async (ev) => {
        if (ev.key !== "Enter") return;
        ev.preventDefault();
        const input = ev.target;
        const value = input.value.trim();
        if (!value) return;
        try {
          const { tags } = await api(`/items/${item.id}/tags`, { method: "POST",
            body: JSON.stringify({ tags: [value] }),
            headers: { "Content-Type": "application/json" } });
          await rerenderReader(item.id, tags);
          loadSidebar();
        } catch (err) { toast(err.message, true); }
      } }));

  const heroVid = youtubeId(item.url);
  const heroSkip = heroVid ? new Set([heroVid]) : new Set();
  const kids = [
    el("div", { class: "rd-meta" },
      el("span", { class: "src" }, item.source_name || ""),
      item.category_name ? el("span", {}, `· ${item.category_name}`) : null,
      el("span", {}, `· ${dateStr(item.published_at) || "date unknown"}`),
      item.author ? el("span", {}, `· ${item.author}`) : null),
    el("h1", { class: "rd-title" },
      el("a", {
        href: item.url, target: "_blank", rel: "noopener noreferrer",
        title: "Open original",
      }, item.title || "(untitled)")),
    heroNode(item),
    tagEditor,
    item.summary && !summaryDup ? el("p", { class: "rd-summary" }, item.summary) : null,
    item.content ? contentNode(item.content, item._sanitized, heroSkip) : null,
    el("div", { class: "rd-foot" },
      el("a", { href: item.url, target: "_blank", rel: "noopener noreferrer" },
        "Open original ↗")),
  ].filter((k) => k instanceof Node);
  const wrap = el("div", { class: "reader-content" }, kids);
  inner.replaceChildren(wrap);
  $("#readerEmpty").classList.add("hidden");
  $("#readerBody").classList.remove("hidden");
  inner.scrollTop = 0;
  // directional entrance: +1 = next (enters from the right), -1 = previous.
  // Kept short and mostly opaque so navigation feels instant even when this
  // is a pure animation (the prefetch cache usually covers the fetch).
  if (slide && wrap.animate) {
    wrap.animate(
      [{ transform: `translateX(${slide > 0 ? 18 : -18}px)`, opacity: 0.7 },
       { transform: "none", opacity: 1 }],
      { duration: 130, easing: "cubic-bezier(0.32, 0.72, 0.28, 1)" });
  }
}

async function rerenderReader(id, tags) {
  if (state.selected && state.selected.id === id) state.selected.tags = tags;
  await loadSidebar();
  await selectItem(id, { force: true });
}

/* ---------------- view filters + hash routing ---------------- */
function viewSig(v) {
  return [v.q || "", v.categoryId || "", v.sourceId || "", v.tag || "",
    v.starred ? 1 : 0, v.unread ? 1 : 0, v.range || "", v.item || ""].join("|");
}

function parseHash() {
  const p = new URLSearchParams(location.hash.replace(/^#\/?/, ""));
  const num = (k) => (p.get(k) ? Number(p.get(k)) : null);
  return {
    q: p.get("q") || "",
    categoryId: num("cat"),
    sourceId: num("source"),
    tag: p.get("tag") || null,
    starred: p.get("starred") === "1",
    unread: p.get("unread") === "1",
    range: RANGES[p.get("range")] ? p.get("range") : null,
    item: num("item"),
  };
}

function hashFor(withItem) {
  const p = new URLSearchParams();
  if (state.q) p.set("q", state.q);
  if (state.categoryId) p.set("cat", String(state.categoryId));
  if (state.sourceId) p.set("source", String(state.sourceId));
  if (state.tag) p.set("tag", state.tag);
  if (state.starred) p.set("starred", "1");
  if (state.unread) p.set("unread", "1");
  if (state.range) p.set("range", state.range);
  if (withItem && state.selected) p.set("item", String(state.selected.id));
  const s = p.toString();
  return s ? `#${s}` : "#/";
}

function syncHash({ replace = false } = {}) {
  const target = hashFor(true);
  if (location.hash === target) return;
  if (replace) history.replaceState(null, "", target);
  else location.hash = target;
}

function replaceItemHash() {
  history.replaceState(null, "", hashFor(true));
}

/* change one or more filters; sync the URL (so back/forward works) and reload */
function setView(patch, { replace = false } = {}) {
  Object.assign(state, patch);
  if (patch.sourceId) state.categoryId = null;
  if (patch.categoryId) state.sourceId = null;
  if (patch.q !== undefined && $("#search").value !== patch.q) {
    $("#search").value = patch.q;
    toggleSearchClear();
  }
  renderSidebar();
  refresh();
  syncHash({ replace });
}

function applyHashView() {
  const v = parseHash();
  if (viewSig(v) === viewSig({ ...state, item: state.selected ? state.selected.id : null })) return;
  state.q = v.q;
  state.categoryId = v.categoryId;
  state.sourceId = v.sourceId;
  state.tag = v.tag;
  state.starred = v.starred;
  state.unread = v.unread;
  state.range = v.range;
  $("#search").value = state.q;
  toggleSearchClear();
  renderSidebar();
  refresh();
  if (v.item) selectItem(v.item);
  else closeReader({ silent: true });
}

/* ---------------- refresh flow ----------------
   Refresh state arrives pushed over SSE (/api/events): start snapshot,
   one event per finished source (progress + immediate failure notes),
   and a done event (summary toast + reload). No idle polling. Runs that
   finished before the page loaded are baselined and not announced.
   If the stream can't be established, a polling fallback takes over.
   Freshly inserted item ids are gathered along the way so a finishing
   refresh can feed the "N new" pill instead of resetting the list. */
let pollTimer;
let evtSource = null;
let pollFallback = false;
let refreshWasRunning = false;
let lastFinishedSeen = null;
const newIds = new Set();

function collectNewIds(result) {
  for (const id of (result && result.inserted_ids) || []) newIds.add(id);
}

function handleRefreshEvent(ev) {
  if (ev.type === "snapshot") {
    if (ev.running) {
      renderRefreshProgress(ev);
      $("#refreshIcon").classList.add("spinning");
      for (const r of ev.results || []) collectNewIds(r);
      if (lastFinishedSeen === null) lastFinishedSeen = 0;  // run in flight: await its done
    } else if (lastFinishedSeen === null) {
      lastFinishedSeen = ev.finished_at;   // boot baseline
    }
    return;
  }
  if (ev.type === "source") {
    renderRefreshProgress({ running: true, total: ev.total, done: ev.done,
      inserted: ev.inserted, errors: ev.errors, current: ev.result.source });
    $("#refreshIcon").classList.add("spinning");
    collectNewIds(ev.result);
    if (ev.result.status === "error" && ev.result.error) {
      showSourceError(ev.result.source_id, ev.result.source, ev.result.error);
    }
    return;
  }
  if (ev.type === "done") {
    const status = ev.status || {};
    renderRefreshProgress(status);
    $("#refreshIcon").classList.remove("spinning");
    if (lastFinishedSeen === null) {
      lastFinishedSeen = status.finished_at;
    } else if (status.finished_at && status.finished_at !== lastFinishedSeen) {
      lastFinishedSeen = status.finished_at;
      announceRefreshDone(status);
    }
  }
}

function connectRefreshStream() {
  if (pollFallback || !window.EventSource) { startPollFallback(); return; }
  if (evtSource) return;
  evtSource = new EventSource("/api/events");
  evtSource.onmessage = (msg) => {
    try { handleRefreshEvent(JSON.parse(msg.data)); }
    catch (_e) { /* malformed frame: ignore */ }
  };
  evtSource.onerror = () => {
    // CLOSED means the stream is really gone (e.g. old server without
    // /api/events); CONNECTING is the browser's automatic reconnect.
    if (evtSource && evtSource.readyState === EventSource.CLOSED) {
      evtSource = null;
      startPollFallback();
    }
  };
}

function startPollFallback() {
  if (pollTimer || pollFallback) return;
  pollFallback = true;
  const tick = async () => {
    try {
      const status = await api("/refresh/status");
      if (status.running) {
        renderRefreshProgress(status);
        $("#refreshIcon").classList.add("spinning");
        pollTimer = setTimeout(tick, 800);
        return;
      }
      renderRefreshProgress(status);
      $("#refreshIcon").classList.remove("spinning");
      if (lastFinishedSeen === null) {
        lastFinishedSeen = status.finished_at;
      } else if (status.finished_at && status.finished_at !== lastFinishedSeen) {
        lastFinishedSeen = status.finished_at;
        announceRefreshDone(status);
      }
    } catch (_e) { /* server hiccup; retry on the next tick */ }
    pollTimer = setTimeout(tick, 5000);
  };
  tick();
}

function pollRefresh() {   // one-shot sync right after triggering a run
  api("/refresh/status")
    .then((status) => handleRefreshEvent({ type: "snapshot", ...status }))
    .catch(() => {});
}

function renderRefreshProgress(status) {
  const bar = $("#refreshBar"), fill = $("#refreshBarFill"), label = $("#refreshStatus");
  if (status.running) {
    refreshWasRunning = true;
    bar.classList.remove("hidden");
    label.classList.remove("hidden");
    $("#refreshIcon").classList.add("spinning");
    fill.classList.remove("done");
    if (status.total > 0) {
      fill.classList.remove("indeterminate");
      fill.style.width = `${Math.round((status.done / status.total) * 100)}%`;
      label.textContent = `Refreshing ${status.done}/${status.total}` +
        (status.inserted ? ` · +${status.inserted} new` : "") +
        (status.errors ? ` · ${status.errors} failed` : "");
      label.title = status.current ? `Last finished: ${status.current}` : "";
    } else {
      fill.classList.add("indeterminate");
      fill.style.width = "";
      label.textContent = "Starting refresh…";
      label.title = "";
    }
    return;
  }
  $("#refreshIcon").classList.remove("spinning");
  if (!refreshWasRunning) return;   // nothing we showed a bar for
  refreshWasRunning = false;
  fill.classList.remove("indeterminate");
  fill.classList.add("done");
  fill.style.width = "100%";
  label.classList.add("hidden");
  setTimeout(() => {
    bar.classList.add("hidden");
    fill.classList.remove("done");
    fill.style.width = "0";
  }, 900);
}

function announceRefreshDone(status) {
  for (const r of status.results || []) {
    collectNewIds(r);
    if (r.status === "error" && r.error) {
      showSourceError(r.source_id, r.source, r.error);
    }
  }
  if (status.total > 0) {
    const bits = [`${status.total} sources`];
    if (status.skipped) bits.push(`${status.skipped} skipped (recent)`);
    if (status.errors) bits.push(`${status.errors} failed`);
    toast(`Refresh done: +${status.inserted} new items (${bits.join(", ")})`,
          status.errors > 0);
    refreshSidebar();
    if (status.inserted > 0) deliverNewItems(status.inserted);
    else loadItems(true).catch(() => {});
  } else if (status.skipped > 0) {
    toast(`All sources are up to date — ${status.skipped} skipped (updated recently)`);
    newIds.clear();
  }
}

/* what to do with the fresh arrivals: merge them into the open list when
   that is safe, otherwise announce them and let the user pull them in */
async function deliverNewItems(inserted) {
  const atTop = $("#listPane").scrollTop < 40;
  if (state.q || newIds.size > 200 || !state.items.length ||
      (atTop && !state.selected)) {
    // search rankings shift wholesale and big batches are simpler as a reload;
    // an untouched list can just refresh quietly
    newIds.clear();
    hideNewPill();
    loadItems(true).catch(() => {});
    return;
  }
  showNewPill(inserted);
}

function showNewPill(n) {
  const pill = $("#newPill");
  pill.replaceChildren(
    document.createTextNode(n === 1 ? "↑ 1 new article" : `↑ ${n} new articles`));
  pill.classList.remove("hidden");
}

function hideNewPill() {
  $("#newPill").classList.add("hidden");
}

async function pullNewItems() {
  const ids = [...newIds];
  newIds.clear();
  hideNewPill();
  if (!ids.length) return;
  const params = new URLSearchParams({
    ids: ids.join(","), limit: "200", sort: state.sort,
  });
  if (state.categoryId) params.set("category_id", state.categoryId);
  if (state.sourceId) params.set("source_id", state.sourceId);
  if (state.tag) params.set("tag", state.tag);
  if (state.starred) params.set("starred", "true");
  if (state.unread) params.set("unread", "true");
  const since = sinceFromRange(state.range);
  if (since) params.set("since", String(since));
  try {
    const data = await api(`/items?${params}`);
    const known = new Set(state.items.map((i) => i.id));
    const fresh = data.items.filter((i) => !known.has(i.id));
    if (!fresh.length) { renderChips(); return; }
    state.items = fresh.concat(state.items);
    state.total += fresh.length;
    state.offset += fresh.length;
    state.navIndex = state.navIndex === null ? null : state.navIndex + fresh.length;
    renderItems(fresh, { prepend: true });
    renderChips();
    $("#listPane").scrollTo({ top: 0, behavior: "smooth" });
  } catch (err) {
    toast(`Failed to pull new items: ${err.message}`, true);
    loadItems(true).catch(() => {});
  }
}

async function startRefresh() {
  try {
    await api("/refresh", { method: "POST", body: "{}",
      headers: { "Content-Type": "application/json" } });
    toast("Refreshing all sources…");
    pollRefresh();
  } catch (err) { toast(err.message, true); }
}

/* ---------------- wiring ---------------- */
function refresh() {
  loadItems(true);
  $("#listPane").scrollTo({ top: 0 });
}
function refreshSidebar() { loadSidebar(); }

function toggleSearchClear() {
  $("#searchClear").classList.toggle("hidden", !$("#search").value);
}

function setTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem("nr-theme", theme);
  $("#themeBtn").replaceChildren(icon(theme === "light" ? "sun" : "moon"));
  $("#themeBtn").title = theme === "light" ? "Switch to dark theme" : "Switch to light theme";
}

function toggleTheme() {
  setTheme(document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light");
}

function setDensity(mode) {
  state.density = mode;
  localStorage.setItem("nr-density", mode);
  $("#listPane").dataset.density = mode;
  const btn = $("#densityBtn");
  btn.replaceChildren(icon(mode === "compact" ? "rowsCompact" : "rows"));
  btn.title = mode === "compact" ? "Compact list — switch to comfortable" : "Comfortable list — switch to compact";
}

function moveSelection(delta) {
  if (!state.items.length) return Promise.resolve(false);
  // navIndex is a synchronous cursor so held-down j/k and rapid swipes queue
  // correctly while earlier selections are still fetching
  let cur = state.navIndex;
  if (cur === null || cur === undefined || !state.items[cur] ||
      (state.selected && state.items[cur].id !== state.selected.id)) {
    cur = state.selected ? state.items.findIndex((i) => i.id === state.selected.id) : -1;
  }
  const next = cur < 0 ? (delta > 0 ? 0 : state.items.length - 1) : cur + delta;
  if (next < 0 || (next >= state.items.length && state.done)) return Promise.resolve(false);
  state.navIndex = next;
  return (async () => {
    if (next >= state.items.length) {
      await loadItems(false);          // pull the next page and keep walking
      if (next >= state.items.length) return false;
    }
    const item = state.items[next];
    if (!item) return false;
    const ok = await selectItem(item.id, { slide: delta });
    if (!ok) return false;
    const row = document.querySelector(`.row[data-id="${item.id}"]`);
    if (row) row.scrollIntoView({ block: "nearest" });
    return true;
  })();
}

/* may a swipe/jump of `delta` articles actually go somewhere? */
function canSwipe(delta) {
  if (!state.selected || !state.items.length) return false;
  let cur = state.navIndex;
  if (cur === null || cur === undefined || !state.items[cur] ||
      state.items[cur].id !== state.selected.id) {
    cur = state.items.findIndex((i) => i.id === state.selected.id);
  }
  if (cur < 0) return false;
  const next = cur + delta;
  return next >= 0 && (next < state.items.length || !state.done);
}

/* swipe on the reader (touch): left = next article, right = previous — the
   gesture mirror of j/k. Edge swipes stay with the browser's back gesture;
   gestures starting on pre/table keep their own horizontal scrolling. Links
   are fair game: a drag is a swipe, a tap still clicks through. */
function setupReaderSwipe() {
  const inner = $("#readerInner");
  const THRESH = 72, FLICK = 26, EDGE = 28, AXIS = 12;
  let g = null;

  inner.addEventListener("pointerdown", (ev) => {
    if (ev.pointerType !== "touch" || !state.selected) return;
    if (ev.target.closest("pre, table, button, input, textarea, select")) return;
    const rect = inner.getBoundingClientRect();
    const x0 = ev.clientX - rect.left;
    if (x0 < EDGE || x0 > rect.width - EDGE) return;
    g = { x: ev.clientX, y: ev.clientY, t: Date.now(), axis: null, id: ev.pointerId };
  }, { passive: true });

  inner.addEventListener("pointermove", (ev) => {
    if (!g || ev.pointerId !== g.id) return;
    const dx = ev.clientX - g.x, dy = ev.clientY - g.y;
    if (!g.axis) {
      if (Math.abs(dx) < AXIS && Math.abs(dy) < AXIS) return;
      if (Math.abs(dx) <= Math.abs(dy)) { g = null; return; }  // vertical: native scroll
      g.axis = "x";
      try { inner.setPointerCapture(ev.pointerId); } catch (_e) { /* unmount race */ }
    }
    ev.preventDefault();
    const wrap = inner.firstElementChild;
    if (!wrap) return;
    const past = (dx < 0 && !canSwipe(1)) || (dx > 0 && !canSwipe(-1));
    wrap.style.transition = "none";
    wrap.style.transform = `translateX(${past ? dx * 0.25 : dx * 0.85}px)`;
  }, { passive: false });

  const end = (ev, cancelled) => {
    if (!g || ev.pointerId !== g.id) return;
    const dx = ev.clientX - g.x;
    const ms = Math.max(1, Date.now() - g.t);
    const axis = g.axis;
    const id = g.id;
    g = null;
    if (axis !== "x") return;
    try { inner.releasePointerCapture(id); } catch (_e) { /* already released */ }
    armClickSwallow();
    const wrap = inner.firstElementChild;
    if (!wrap) return;
    const flick = Math.abs(dx) > FLICK && Math.abs(dx) / ms > 0.35;
    const go = !cancelled && (Math.abs(dx) > THRESH || flick) &&
      (dx < 0 ? canSwipe(1) : canSwipe(-1));
    if (go) {
      // no out-animation: keep the current article readable and swap as soon
      // as the (usually prefetched) next one is ready — motion lives in the
      // short entrance animation of the incoming content
      wrap.style.transition = "";
      wrap.style.transform = "";
      wrap.style.opacity = "";
      const dir = dx < 0 ? 1 : -1;
      Promise.resolve(moveSelection(dir)).then((moved) => {
        if (moved || wrap !== inner.firstElementChild) return;
        // couldn't advance (empty next page): rubber-band feedback
        wrap.style.transition = "transform .18s cubic-bezier(0.32, 0.72, 0.28, 1)";
        wrap.style.transform = `translateX(${dx * 0.12}px)`;
        requestAnimationFrame(() => { wrap.style.transform = ""; });
      });
    } else {
      wrap.style.transition = "transform .18s cubic-bezier(0.32, 0.72, 0.28, 1)";
      wrap.style.transform = "";
      wrap.style.opacity = "";
    }
  };
  inner.addEventListener("pointerup", (ev) => end(ev, false));
  inner.addEventListener("pointercancel", (ev) => end(ev, true));
}

/* swipe a list row (touch): left dismisses (with undo), right stars.
   Same gesture grammar as the reader: intent by axis, edges left to the
   browser, form controls opt out. The row title is a link, so links are
   swipeable too — a committed gesture swallows the synthetic click. */
function setupRowSwipes() {
  const list = $("#itemList");
  const THRESH = 80, FLICK = 26, AXIS = 12, EDGE = 24;
  let g = null;

  list.addEventListener("pointerdown", (ev) => {
    if (ev.pointerType !== "touch") return;
    const row = ev.target.closest(".row");
    if (!row || ev.target.closest("button, input, textarea, select")) return;
    const rect = row.getBoundingClientRect();
    const x0 = ev.clientX - rect.left;
    if (x0 < EDGE || rect.width - x0 < EDGE) return;
    g = { row, x: ev.clientX, y: ev.clientY, t: Date.now(), axis: null,
      id: ev.pointerId };
  }, { passive: true });

  list.addEventListener("pointermove", (ev) => {
    if (!g || ev.pointerId !== g.id) return;
    const dx = ev.clientX - g.x, dy = ev.clientY - g.y;
    if (!g.axis) {
      if (Math.abs(dx) < AXIS && Math.abs(dy) < AXIS) return;
      if (Math.abs(dx) <= Math.abs(dy)) { g = null; return; }  // vertical scroll
      g.axis = "x";
      try { list.setPointerCapture(ev.pointerId); } catch (_e) { /* unmount race */ }
    }
    ev.preventDefault();
    g.dx = dx;
    g.row.style.transition = "none";
    g.row.style.transform = `translateX(${dx * 0.8}px)`;
    g.row.classList.toggle("armed-dismiss", dx < -THRESH / 2);
    g.row.classList.toggle("armed-star", dx > THRESH / 2);
  }, { passive: false });

  const end = (ev, cancelled) => {
    if (!g || ev.pointerId !== g.id) return;
    const { row, axis, t, id } = g;
    const dx = ev.clientX - g.x;
    g = null;
    if (axis !== "x") return;
    try { list.releasePointerCapture(id); } catch (_e) { /* already released */ }
    armClickSwallow();
    row.classList.remove("armed-dismiss", "armed-star");
    const flick = Math.abs(dx) > FLICK && Math.abs(dx) / Math.max(1, Date.now() - t) > 0.35;
    const go = !cancelled && (Math.abs(dx) > THRESH || flick);
    const item = state.items.find((i) => String(i.id) === row.dataset.id);
    if (!go || !item) {
      row.style.transition = "transform .18s cubic-bezier(0.32, 0.72, 0.28, 1)";
      row.style.transform = "";
      return;
    }
    if (dx < 0) {
      row.style.transform = "";        // dismissItem removes the row
      row.style.transition = "";
      dismissItem(item);
    } else {
      row.style.transition = "transform .18s cubic-bezier(0.32, 0.72, 0.28, 1)";
      row.style.transform = "";
      if (!item.starred) {
        toggleStar(item).then((on) => { if (on) toast("Starred"); })
          .catch((err) => toast(err.message, true));
      }
    }
  };
  list.addEventListener("pointerup", (ev) => end(ev, false));
  list.addEventListener("pointercancel", (ev) => end(ev, true));
}

function isTyping() {
  const t = document.activeElement;
  return t && (t.matches("input, textarea, select") || t.isContentEditable);
}

let searchTimer;
$("#search").addEventListener("input", (ev) => {
  toggleSearchClear();
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => setView({ q: ev.target.value.trim() }, { replace: true }), 250);
});
$("#search").addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") {
    ev.stopPropagation();
    ev.target.value = "";
    toggleSearchClear();
    clearTimeout(searchTimer);
    setView({ q: "" }, { replace: true });
  }
});
$("#searchClear").addEventListener("click", () => {
  $("#search").value = "";
  toggleSearchClear();
  clearTimeout(searchTimer);
  setView({ q: "" }, { replace: true });
  $("#search").focus();
});

document.addEventListener("keydown", (ev) => {
  if (document.querySelector("dialog[open]")) return;
  const k = ev.key;
  if (k === "Escape") {
    if ($("#sidebar").classList.contains("open")) return closeSidebar();
    if ($("#reader").classList.contains("open") || state.selected) {
      ev.preventDefault();
      closeReader();
    }
    return;
  }
  if (isTyping()) return;
  if (k === "/") { ev.preventDefault(); $("#search").focus(); return; }
  if (k === "?") { ev.preventDefault(); $("#helpDialog").showModal(); return; }
  if (k === "j" || k === "ArrowDown" || k === "ArrowRight") { ev.preventDefault(); moveSelection(1); return; }
  if (k === "k" || k === "ArrowUp" || k === "ArrowLeft") { ev.preventDefault(); moveSelection(-1); return; }
  if (k === "r") { ev.preventDefault(); startRefresh(); return; }
  if (k === "t") { ev.preventDefault(); toggleTheme(); return; }
  if (!state.selected) return;
  const item = state.selected;
  if (k === "Enter" || k === "o") {
    ev.preventDefault();
    if (item.url) window.open(item.url, "_blank", "noopener");
  } else if (k === "s") {
    ev.preventDefault();
    toggleStar(item).catch((err) => toast(err.message, true));
  } else if (k === "d") {
    ev.preventDefault();
    dismissItem(item);
  } else if (k === "u") {
    ev.preventDefault();
    toggleRead(item);
  } else if (k === "c") {
    ev.preventDefault();
    if (!item.url) return toast("No link for this item", true);
    copyText(item.url).then((ok) => toast(ok ? "Link copied" : "Copy failed", !ok));
  }
});

$("#sortSel").addEventListener("change", (ev) => {
  state.sort = ev.target.value;
  localStorage.setItem("nr-sort", state.sort);
  refresh();
});
$("#refreshBtn").addEventListener("click", startRefresh);
$("#notesDismissAll").addEventListener("click", () => {
  for (const n of document.querySelectorAll("#notes .note")) n.remove();
  notesChanged();
});
$("#readerBack").addEventListener("click", () => closeReader());
$("#readerStar").addEventListener("click", () => {
  if (state.selected) toggleStar(state.selected).catch((err) => toast(err.message, true));
});
$("#readerCopy").addEventListener("click", async (ev) => {
  const item = state.selected;
  if (!item) return;
  if (!item.url) return toast("No link for this item", true);
  const btn = ev.currentTarget;
  if (await copyText(item.url)) {
    btn.replaceChildren(icon("check"), el("span", { class: "btn-label" }, "Copied"));
    setTimeout(() => updateReaderActions(item), 1200);
  } else toast("Copy failed", true);
});
$("#readerDismiss").addEventListener("click", () => {
  if (state.selected) dismissItem(state.selected);
});

$("#addBtn").addEventListener("click", () => $("#addDialog").showModal());
$("#addCancel").addEventListener("click", () => $("#addDialog").close());
$("#addForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const form = new FormData(ev.target);
  try {
    const src = await api("/sources", { method: "POST",
      body: JSON.stringify({
        url: form.get("url"), name: form.get("name") || "",
        category_id: form.get("category_id") ? Number(form.get("category_id")) : null,
        category_name: form.get("category_name") || null,
      }),
      headers: { "Content-Type": "application/json" } });
    $("#addDialog").close();
    ev.target.reset();
    toast(`Source added (${src.plugin}). Refreshing…`);
    refreshSidebar();
    await api("/refresh", { method: "POST",
      body: JSON.stringify({ source_id: src.id, force: true }),
      headers: { "Content-Type": "application/json" } });
    pollRefresh();
  } catch (err) { toast(err.message, true); }
});

/* ---------------- export / import sources ---------------- */
$("#exportBtn").addEventListener("click", async () => {
  try {
    const data = await api("/sources/export");
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = el("a", {
      href: URL.createObjectURL(blob),
      download: `newsreader-sources-${new Date().toISOString().slice(0, 10)}.json`,
    });
    a.click();
    URL.revokeObjectURL(a.href);
    toast(`Exported ${data.sources.length} sources`);
  } catch (err) { toast(err.message, true); }
});

$("#importBtn").addEventListener("click", () => $("#importFile").click());

$("#importFile").addEventListener("change", async (ev) => {
  const file = ev.target.files && ev.target.files[0];
  ev.target.value = "";
  if (!file) return;
  let doc;
  try {
    doc = JSON.parse(await file.text());
  } catch (_e) { return toast("Not a valid JSON file", true); }
  const sources = Array.isArray(doc) ? doc : doc.sources;
  if (!Array.isArray(sources)) return toast("File has no sources array", true);
  if (!sources.length) return toast("Nothing to import", true);
  if (!confirm(`Import ${sources.length} source(s) from ${file.name}? Existing sources with the same URL will be updated.`)) return;
  try {
    const res = await api("/sources/import", { method: "POST",
      body: JSON.stringify({ sources }),
      headers: { "Content-Type": "application/json" } });
    const bits = [`${res.created} added`, `${res.updated} updated`];
    if (res.skipped) bits.push(`${res.skipped} skipped`);
    toast(`Import done: ${bits.join(", ")}`);
    refreshSidebar();
  } catch (err) { toast(err.message, true); }
});

$("#addCatBtn").addEventListener("click", async () => {
  const name = prompt("New category name:");
  if (!name) return;
  try {
    await api("/categories", { method: "POST",
      body: JSON.stringify({ name }), headers: { "Content-Type": "application/json" } });
    refreshSidebar();
  } catch (err) { toast(err.message, true); }
});

/* ---------------- edit source dialog ---------------- */
let editingSource = null;

function openEditDialog(src) {
  editingSource = src;
  const form = $("#editForm");
  form.reset();
  form.elements.name.value = src.name || "";
  form.elements.url.value = src.url;
  form.elements.enabled.checked = !!src.enabled;
  form.elements.hidden.checked = !src.hidden;
  form.elements.category_name.value = "";
  form.elements.config.value =
    src.config && Object.keys(src.config).length
      ? JSON.stringify(src.config, null, 2) : "";
  const sel = $("#editCatSel");
  sel.replaceChildren(el("option", { value: "" }, "— none —"));
  for (const cat of state.categories) {
    sel.append(el("option", { value: cat.id, selected: cat.id === src.category_id ? "" : null },
      cat.name));
  }
  $("#editHint").textContent =
    `Plugin: ${src.plugin}. Changing the URL re-detects the plugin and refreshes the source.`;
  $("#editDialog").showModal();
}

$("#editCancel").addEventListener("click", () => $("#editDialog").close());

$("#editDelete").addEventListener("click", async () => {
  if (!editingSource) return;
  const label = editingSource.name || editingSource.url;
  if (!confirm(`Delete source “${label}” and its ${editingSource.item_count} items? This cannot be undone.`)) return;
  try {
    await api(`/sources/${editingSource.id}`, { method: "DELETE" });
    $("#editDialog").close();
    toast("Source deleted");
    if (state.sourceId === editingSource.id) setView({ sourceId: null });
    refreshSidebar();
  } catch (err) { toast(err.message, true); }
});

$("#editForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (!editingSource) return;
  const form = ev.target;
  let config = {};
  const raw = form.elements.config.value.trim();
  if (raw) {
    try { config = JSON.parse(raw); }
    catch (_e) { return toast("Advanced options must be valid JSON", true); }
  }
  const body = {
    name: form.elements.name.value.trim(),
    url: form.elements.url.value.trim(),
    enabled: form.elements.enabled.checked,
    hidden: !form.elements.hidden.checked,
    category_id: form.elements.category_id.value ? Number(form.elements.category_id.value) : null,
    config,
  };
  try {
    if (form.elements.category_name.value.trim()) {
      const cat = await api("/categories", { method: "POST",
        body: JSON.stringify({ name: form.elements.category_name.value.trim() }),
        headers: { "Content-Type": "application/json" } });
      body.category_id = cat.id;
    }
    const urlChanged = body.url !== editingSource.url;
    await api(`/sources/${editingSource.id}`, { method: "PATCH",
      body: JSON.stringify(body), headers: { "Content-Type": "application/json" } });
    $("#editDialog").close();
    toast("Source updated");
    refreshSidebar();
    if (urlChanged) {
      await api("/refresh", { method: "POST",
        body: JSON.stringify({ source_id: editingSource.id, force: true }),
        headers: { "Content-Type": "application/json" } });
      pollRefresh();
    }
  } catch (err) { toast(err.message, true); }
});

/* ---------------- help dialog ---------------- */
$("#helpBtn").addEventListener("click", () => $("#helpDialog").showModal());
$("#helpClose").addEventListener("click", () => $("#helpDialog").close());

/* ---------------- layout chrome ---------------- */
function openSidebar() {
  $("#sidebar").classList.add("open");
  $("#scrim").classList.add("show");
}
function closeSidebar() {
  $("#sidebar").classList.remove("open");
  $("#scrim").classList.remove("show");
}
$("#menuBtn").addEventListener("click", () => {
  $("#sidebar").classList.contains("open") ? closeSidebar() : openSidebar();
});
$("#scrim").addEventListener("click", closeSidebar);
$("#tagFilter").addEventListener("input", (ev) => {
  state.tagFilter = ev.target.value.trim();
  renderTags();
});
$("#themeBtn").addEventListener("click", toggleTheme);
$("#densityBtn").addEventListener("click", () => {
  setDensity(state.density === "compact" ? "comfy" : "compact");
});
$("#newPill").addEventListener("click", () => pullNewItems());
$("#rangeChips").addEventListener("click", (ev) => {
  const btn = ev.target.closest("[data-range]");
  if (!btn) return;
  // radio-style group: "All time" (empty data-range) clears the filter
  setView({ range: btn.dataset.range || null });
});
$("#markReadBtn").addEventListener("click", markAllRead);
window.addEventListener("hashchange", applyHashView);

// infinite scroll
new IntersectionObserver((entries) => {
  if (entries[0].isIntersecting) loadItems(false);
}, { root: $("#listPane"), rootMargin: "600px" }).observe($("#sentinel"));

setupReaderSwipe();
setupRowSwipes();

// theme + density init
const savedTheme = localStorage.getItem("nr-theme") ||
  (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
setTheme(savedTheme);
setDensity(state.density);
$("#sortSel").value = state.sort;
toggleSearchClear();

// boot
hydrateIcons();
const initial = parseHash();
state.q = initial.q;
state.categoryId = initial.categoryId;
state.sourceId = initial.sourceId;
state.tag = initial.tag;
state.starred = initial.starred;
state.unread = initial.unread;
state.range = initial.range;
$("#search").value = state.q;
toggleSearchClear();
refreshSidebar();
loadItems(true);
connectRefreshStream();
if (initial.item) selectItem(initial.item);
