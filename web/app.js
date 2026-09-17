/* Newsreader client — vanilla JS, no build step, talks to /api only. */
"use strict";

const $ = (sel) => document.querySelector(sel);
const state = {
  q: "",
  categoryId: null,
  sourceId: null,
  tag: null,
  starred: false,
  starredCount: 0,
  showAllTags: false,
  tagFilter: "",
  sort: "new",
  offset: 0,
  total: 0,
  items: [],
  loading: false,
  done: false,
  sources: [],
  categories: [],
  tags: [],
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

let toastTimer;
function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("error", isError);
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 3200);
}

/* ---------------- data ---------------- */
async function loadItems(reset = true) {
  if (state.loading || (!reset && state.done)) return;
  state.loading = true;
  if (reset) { state.offset = 0; state.done = false; $("#itemList").replaceChildren(); }
  const params = new URLSearchParams({
    sort: state.sort, limit: "50", offset: String(state.offset),
  });
  if (state.q) params.set("q", state.q);
  if (state.categoryId) params.set("category_id", state.categoryId);
  if (state.sourceId) params.set("source_id", state.sourceId);
  if (state.tag) params.set("tag", state.tag);
  if (state.starred) params.set("starred", "true");
  try {
    const data = await api(`/items?${params}`);
    state.total = data.total;
    state.items = reset ? data.items : state.items.concat(data.items);
    state.offset += data.items.length;
    if (data.items.length === 0 && !reset) state.done = true;
    if (state.offset >= data.total) state.done = true;
    renderItems(data.items);
    renderChips();
  } catch (err) {
    toast(`Failed to load items: ${err.message}`, true);
  } finally {
    state.loading = false;
    if (state.items.length === 0 && state.total === 0) {
      $("#itemList").append(el("div", { class: "loading" },
        state.q ? "No results." : "No items yet — hit “Refresh all” to fetch your sources."));
      $("#endNote").classList.add("hidden");
    }
  }
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
    $("#dbStats").textContent =
      `${health.counts.items} items · ${state.starredCount} starred · ${health.counts.sources} sources`;
    renderCategories();
    renderSources();
    renderTags();
  } catch (err) {
    toast(`Failed to load sidebar: ${err.message}`, true);
  }
}

/* ---------------- rendering ---------------- */
function renderCategories() {
  const list = $("#catList");
  list.replaceChildren();
  const starred = el("li", {
    class: state.starred ? "active" : "",
    title: "Show only starred articles",
    onclick: () => { state.starred = !state.starred; refresh(); },
  },
    el("span", { class: "dot", style: "background:#fbbf24" }),
    el("span", { class: "src-title" }, "★ Starred"),
    el("span", { class: "count" }, String(state.starredCount)));
  list.append(starred);
  const all = el("li", {
    class: !state.starred && state.categoryId === null && !state.sourceId && !state.tag
      ? "active" : "",
    onclick: () => {
      state.categoryId = null; state.sourceId = null; state.tag = null; state.starred = false;
      refresh();
    },
  }, el("span", {}, "All items"), el("span", { class: "count" },
    String(state.sources.reduce((n, s) => n + (s.hidden ? 0 : s.item_count), 0))));
  list.append(all);
  for (const cat of state.categories) {
    const del = el("span", {
      class: "x", title: "Delete category", textContent: "✕",
      onclick: async (ev) => {
        ev.stopPropagation();
        if (!confirm(`Delete category “${cat.name}”? Sources become uncategorized.`)) return;
        try { await api(`/categories/${cat.id}`, { method: "DELETE" }); refreshSidebar(); }
        catch (err) { toast(err.message, true); }
      },
    });
    const li = el("li", {
      class: state.categoryId === cat.id ? "active" : "",
      onclick: () => { state.categoryId = cat.id; state.sourceId = null; refresh(); },
    },
      el("span", { class: "dot", style: `background:${catColor(cat.id)}` }),
      el("span", { class: "src-title" }, cat.name),
      el("span", { class: "count" }, String(cat.item_count)),
      del);
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
    list.append(el("li", { class: "side-group", style: "pointer-events:none;opacity:.55;font-size:11px;text-transform:uppercase;letter-spacing:.08em;margin-top:10px;" }, group));
    for (const src of srcs) {
      const actions = el("span", { class: "src-actions" });
      actions.append(el("span", {
        class: "mini-btn", title: src.hidden ? "Show articles in the feed" : "Hide articles from the feed",
        textContent: src.hidden ? "🙈" : "👁",
        onclick: async (ev) => {
          ev.stopPropagation();
          try {
            await api(`/sources/${src.id}`, { method: "PATCH",
              body: JSON.stringify({ hidden: !src.hidden }),
              headers: { "Content-Type": "application/json" } });
            refreshSidebar();
            // the feed only changes when the toggled source isn't the explicit view
            if (state.sourceId !== src.id) refresh();
          } catch (err) { toast(err.message, true); }
        },
      }));
      actions.append(el("span", {
        class: "mini-btn", title: src.enabled ? "Disable" : "Enable",
        textContent: src.enabled ? "⏸" : "▶",
        onclick: async (ev) => {
          ev.stopPropagation();
          try {
            await api(`/sources/${src.id}`, { method: "PATCH",
              body: JSON.stringify({ enabled: !src.enabled }),
              headers: { "Content-Type": "application/json" } });
            refreshSidebar();
          } catch (err) { toast(err.message, true); }
        },
      }));
      actions.append(el("span", {
        class: "mini-btn", title: "Refresh this source", textContent: "⟳",
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
      }));
      actions.append(el("span", {
        class: "mini-btn", title: "Edit source", textContent: "✎",
        onclick: (ev) => {
          ev.stopPropagation();
          openEditDialog(src);
        },
      }));
      const li = el("li", {
        class: (state.sourceId === src.id ? "active" : "") + (src.hidden ? " muted" : ""),
        title: src.last_error ? `Last error: ${src.last_error}` : src.url,
        onclick: () => { state.sourceId = src.id; state.categoryId = null; refresh(); },
      },
        el("span", { class: "dot", style: `background:${src.hidden ? "#555" : src.enabled ? catColor(src.id) : "#555"}` }),
        el("span", { class: "src-title" }, src.name || src.url),
        src.last_status === "error" ? el("span", { class: "err", title: src.last_error }, "⚠") : null,
        el("span", { class: "count" }, String(src.item_count)),
        actions);
      list.append(li);
    }
  }
}

function renderTags() {
  const cloud = $("#tagCloud");
  cloud.replaceChildren();
  const tags = state.tags.filter((t) => t.item_count > 0 || t.name === state.tag);
  if (!tags.length) {
    cloud.append(el("span", { style: "color:var(--text-dim);font-size:13px" }, "No tags yet."));
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

  const chipFor = (t) => el("span", {
    class: "chip" + (state.tag === t.name ? " accent" : ""),
    onclick: () => { state.tag = state.tag === t.name ? null : t.name; refresh(); },
  }, t.name, el("span", { class: "count", style: "font-size:11px;opacity:.7" },
    String(t.item_count)));
  for (const t of visible) cloud.append(chipFor(t));

  if (state.showAllTags) {
    if (!visible.length) {
      cloud.append(el("span", { style: "color:var(--text-dim);font-size:13px" },
        "No tags match."));
    }
    cloud.append(el("span", {
      class: "chip toggle", title: "Collapse the tag list",
      onclick: () => {
        state.showAllTags = false; state.tagFilter = "";
        $("#tagFilter").value = ""; renderTags();
      },
    }, "Show common only"));
  } else if (tags.length > TAG_PREVIEW) {
    cloud.append(el("span", {
      class: "chip toggle", title: "Show every tag, with a filter box",
      onclick: () => { state.showAllTags = true; renderTags(); $("#tagFilter").focus(); },
    }, `+${tags.length - TAG_PREVIEW} more…`));
  }
  $("#tagFilterWrap").classList.toggle("hidden", !state.showAllTags);
}

function renderChips() {
  const chips = $("#filterChips");
  chips.replaceChildren();
  if (state.q) {
    chips.append(chip(`Search: “${state.q}”`, () => { state.q = ""; $("#search").value = ""; refresh(); }));
  }
  if (state.categoryId) {
    const cat = state.categories.find((c) => c.id === state.categoryId);
    chips.append(chip(`Category: ${cat ? cat.name : state.categoryId}`, () => { state.categoryId = null; refresh(); }));
  }
  if (state.sourceId) {
    const src = state.sources.find((s) => s.id === state.sourceId);
    chips.append(chip(`Source: ${src ? src.name || src.url : state.sourceId}`, () => { state.sourceId = null; refresh(); }));
  }
  if (state.tag) chips.append(chip(`Tag: ${state.tag}`, () => { state.tag = null; refresh(); }));
  if (state.starred) chips.append(chip("★ Starred", () => { state.starred = false; refresh(); }));
  $("#sortSel").value = state.sort;
  $("#endNote").classList.toggle("hidden", !state.done || state.total === 0);
}

function chip(text, onRemove) {
  return el("span", { class: "chip accent" }, text,
    el("span", { class: "x", textContent: "✕", onclick: onRemove }));
}

function cardActions(item) {
  const openBtn = el("a", {
    class: "card-act open", href: item.url, target: "_blank", rel: "noopener",
    title: "Open original", textContent: "↗",
    onclick: (ev) => ev.stopPropagation(),
  });
  const starBtn = el("button", {
    class: "card-act star" + (item.starred ? " on" : ""),
    title: item.starred ? "Unstar" : "Star", textContent: item.starred ? "★" : "☆",
    onclick: async (ev) => {
      ev.stopPropagation();
      try {
        const res = await api(`/items/${item.id}/star`,
          { method: item.starred ? "DELETE" : "POST" });
        item.starred = res.starred;
        starBtn.textContent = item.starred ? "★" : "☆";
        starBtn.title = item.starred ? "Unstar" : "Star";
        starBtn.classList.toggle("on", item.starred);
        refreshSidebar();
        if (state.starred && !item.starred) removeCard(item);
      } catch (err) { toast(err.message, true); }
    },
  });
  const dismissBtn = el("button", {
    class: "card-act dismiss", title: "Dismiss — never show again", textContent: "✕",
    onclick: async (ev) => {
      ev.stopPropagation();
      try {
        await api(`/items/${item.id}/dismiss`, { method: "POST" });
        removeCard(item);
        refreshSidebar();
        toast("Article dismissed");
      } catch (err) { toast(err.message, true); }
    },
  });
  return el("div", { class: "card-actions" }, openBtn, starBtn, dismissBtn);
}

function removeCard(item) {
  state.items = state.items.filter((i) => i.id !== item.id);
  state.total = Math.max(0, state.total - 1);
  const node = document.querySelector(`.card[data-id="${item.id}"]`);
  if (node) node.remove();
  renderChips();
  if (!state.items.length && !state.done) loadItems(true);
}

function renderItems(items) {
  const list = $("#itemList");
  for (const item of items) {
    const thumb = item.image_url
      ? el("img", { src: item.image_url, loading: "lazy", alt: "",
          onerror: (ev) => { ev.target.parentNode.replaceWith(noimg()); } })
      : null;
    const media = el("div", { class: "thumb" }, thumb || noimg(), cardActions(item));
    const card = el("article", { class: "card", "data-id": String(item.id),
        onclick: () => openDetail(item.id) },
      media,
      el("div", { class: "body" },
        el("h3", {},
          el("a", {
            class: "title-link", href: item.url, target: "_blank", rel: "noopener",
            onclick: (ev) => ev.stopPropagation(),
          }, item.title || "(untitled)")),
        el("div", { class: "meta" },
          el("span", { class: "src" }, item.source_name || ""),
          item.starred ? el("span", { style: "color:#fbbf24" }, "★") : null,
          item.category_name ? el("span", {}, item.category_name) : null,
          el("span", {}, timeago(item.published_at))),
        item.summary ? el("p", { class: "summary" }, item.summary) : null,
        el("div", { class: "card-tags" },
          (item.tags || []).slice(0, 4).map((t) =>
            el("span", { class: "chip",             onclick: (ev) => {
              ev.stopPropagation(); state.tag = t; refresh();
              $(".main").scrollTo({ top: 0 });
            } }, t)),
          (item.tags || []).length > 4 ? el("span", { class: "chip" }, `+${item.tags.length - 4}`) : null)));
    list.append(card);
  }
}

function noimg() {
  return el("div", { class: "noimg" }, "📰");
}

/* ---------------- detail drawer ---------------- */
async function openDetail(id) {
  let item;
  try { item = await api(`/items/${id}`); }
  catch (err) { return toast(err.message, true); }
  const inner = $("#detailInner");
  // don't show the summary when it is just a truncated copy of the body
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim().slice(0, 300);
  const summaryDup = item.summary && item.content &&
    norm(item.content).includes(norm(item.summary));
  const kids = [
    el("div", { class: "dmeta" },
      el("span", { class: "src", style: "color:var(--accent);font-weight:600" }, item.source_name || ""),
      item.category_name ? el("span", {}, ` · ${item.category_name}`) : null,
      el("span", {}, ` · ${dateStr(item.published_at) || "date unknown"}`),
      item.author ? el("span", {}, ` · ${item.author}`) : null),
    el("h1", {},
      el("a", {
        class: "title-link", href: item.url, target: "_blank", rel: "noopener",
        title: "Open original",
      }, item.title || "(untitled)")),
    el("div", { class: "detail-actions" },
      el("button", {
        class: "btn" + (item.starred ? " star-on" : ""), textContent: item.starred ? "★ Starred" : "☆ Star",
        onclick: async (ev) => {
          try {
            const res = await api(`/items/${item.id}/star`,
              { method: item.starred ? "DELETE" : "POST" });
            item.starred = res.starred;
            ev.target.textContent = item.starred ? "★ Starred" : "☆ Star";
            ev.target.classList.toggle("star-on", item.starred);
            refreshSidebar();
          } catch (err) { toast(err.message, true); }
        },
      }),
      el("button", {
        class: "btn danger", textContent: "✕ Dismiss",
        onclick: async () => {
          try {
            await api(`/items/${item.id}/dismiss`, { method: "POST" });
            removeCard(item);
            closeDetail();
            refreshSidebar();
          } catch (err) { toast(err.message, true); }
        },
      })),
    item.image_url ? el("img", { class: "hero", src: item.image_url, alt: "",
      onerror: (ev) => ev.target.remove() }) : null,
    el("div", { class: "tag-editor" },
      el("span", { style: "font-size:12px;color:var(--text-dim)" }, "Tags:"),
      item.tags.map((t) => el("span", { class: "chip" }, t,
        el("span", { class: "tagdel", textContent: "✕", onclick: async () => {
          try {
            const { tags } = await api(`/items/${item.id}/tags/${encodeURIComponent(t)}`, { method: "DELETE" });
            openDetailRerender(item.id, tags);
          } catch (err) { toast(err.message, true); }
        } }))),
      el("input", { placeholder: "+ tag", maxlength: "40",
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
            openDetailRerender(item.id, tags);
            loadSidebar();
          } catch (err) { toast(err.message, true); }
        } })),
    item.summary && !summaryDup
      ? el("p", { style: "color:var(--text-dim)" }, item.summary) : null,
    item.content ? el("div", { class: "content", innerHTML: DOMPurify.sanitize(item.content) }) : null,
    el("p", { style: "margin-top:22px" },
      el("a", { href: item.url, target: "_blank", rel: "noopener" }, "Open original ↗")),
  ].filter((k) => k instanceof Node);
  inner.replaceChildren(...kids);
  $("#detail").classList.add("open");
  $("#detail").setAttribute("aria-hidden", "false");
  inner.scrollTop = 0;
}
async function openDetailRerender(id, tags) {
  // re-fetch to keep detail + lists in sync
  await loadSidebar();
  openDetail(id);
}

function closeDetail() {
  $("#detail").classList.remove("open");
  $("#detail").setAttribute("aria-hidden", "true");
  loadItems(false).catch(() => {});  // refresh tag chips silently
}

/* ---------------- refresh flow ---------------- */
let pollTimer;
let refreshWasRunning = false;

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

function pollRefresh() {
  clearTimeout(pollTimer);
  const poll = async () => {
    try {
      const status = await api("/refresh/status");
      renderRefreshProgress(status);
      $("#refreshIcon").textContent = status.running ? "◐" : "⟳";
      if (status.running) {
        pollTimer = setTimeout(poll, 800);
        return;
      }
      if (status.finished_at && status.total > 0) {
        const bits = [`${status.total} sources`];
        if (status.skipped) bits.push(`${status.skipped} skipped (recent)`);
        if (status.errors) bits.push(`${status.errors} failed`);
        toast(`Refresh done: +${status.inserted} new items (${bits.join(", ")})`);
        refreshSidebar();
        loadItems(true).catch(() => {});
      } else if (status.finished_at && status.skipped > 0) {
        toast(`All sources are up to date — ${status.skipped} skipped (updated recently)`);
      }
    } catch (_e) { /* server hiccup; stop polling */ }
  };
  poll();
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
  $("#itemList").replaceChildren(el("div", { class: "loading" }, "Loading…"));
  loadItems(true);
  $(".main").scrollTo({ top: 0 });
}
function refreshSidebar() { loadSidebar(); }

let searchTimer;
$("#search").addEventListener("input", (ev) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.q = ev.target.value.trim(); refresh(); }, 250);
});
$("#search").addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") { ev.target.value = ""; state.q = ""; refresh(); }
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "/" && document.activeElement.tagName !== "INPUT") {
    ev.preventDefault();
    $("#search").focus();
  }
  if (ev.key === "Escape") closeDetail();
});
$("#sortSel").addEventListener("change", (ev) => { state.sort = ev.target.value; refresh(); });
$("#refreshBtn").addEventListener("click", startRefresh);
$("#detailClose").addEventListener("click", closeDetail);
$("#detail").addEventListener("click", (ev) => {
  if (!ev.target.closest(".detail-inner") && !ev.target.closest(".detail-close")) closeDetail();
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
  try { await api("/categories", { method: "POST",
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
    if (state.sourceId === editingSource.id) {
      state.sourceId = null;
      refresh();
    }
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
$("#menuBtn").addEventListener("click", () => {
  $("#sidebar").classList.toggle("open");
  $("#scrim").classList.toggle("show");
});
$("#tagFilter").addEventListener("input", (ev) => {
  state.tagFilter = ev.target.value.trim();
  renderTags();
});
$("#scrim").addEventListener("click", () => {
  $("#sidebar").classList.remove("open");
  $("#scrim").classList.remove("show");
});
$("#themeBtn").addEventListener("click", () => {
  const cur = document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", cur);
  localStorage.setItem("nr-theme", cur);
  $("#themeBtn").textContent = cur === "light" ? "☀" : "☾";
});

// infinite scroll
new IntersectionObserver((entries) => {
  if (entries[0].isIntersecting) loadItems(false);
}, { rootMargin: "600px" }).observe($("#sentinel"));

// theme init
const saved = localStorage.getItem("nr-theme") ||
  (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
document.documentElement.setAttribute("data-theme", saved);
$("#themeBtn").textContent = saved === "light" ? "☀" : "☾";

// boot
refreshSidebar();
loadItems(true);
pollRefresh();
