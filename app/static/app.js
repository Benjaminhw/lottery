"use strict";

const appRoot = document.querySelector("#app");
const toastRegion = document.querySelector("#toast-region");
const basePath = document.querySelector('meta[name="app-base-path"]')?.content || "";

function appUrl(path) {
  return `${basePath}${path}`;
}

const state = {
  adminEvents: [],
  currentEvent: null,
  currentParticipant: null,
  adminAuthenticated: false,
  eventSource: null,
  drawing: false,
  selectedRoundId: null,
};

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function api(path, options = {}) {
  const requestOptions = { ...options };
  if (requestOptions.body && typeof requestOptions.body !== "string") {
    requestOptions.headers = {
      "Content-Type": "application/json",
      ...(requestOptions.headers || {}),
    };
    requestOptions.body = JSON.stringify(requestOptions.body);
  }
  const response = await fetch(appUrl(path), requestOptions);
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    const detail = payload?.detail;
    const message = Array.isArray(detail)
      ? detail.map((item) => item.msg).join("；")
      : detail || "请求失败，请稍后重试";
    throw new ApiError(message, response.status);
  }
  return payload;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function icon(name) {
  return `<i data-lucide="${name}" aria-hidden="true"></i>`;
}

function refreshIcons() {
  if (window.lucide) {
    window.lucide.createIcons({ attrs: { "stroke-width": 1.8 } });
  }
}

function showToast(message, type = "success") {
  const toast = document.createElement("div");
  toast.className = `toast${type === "error" ? " is-error" : ""}`;
  toast.innerHTML = `${icon(type === "error" ? "circle-alert" : "circle-check")}<span>${escapeHtml(message)}</span>`;
  toastRegion.append(toast);
  refreshIcons();
  window.setTimeout(() => toast.remove(), 3400);
}

function setButtonBusy(button, busy, busyLabel = "处理中") {
  if (!button) return;
  if (busy) {
    button.dataset.original = button.innerHTML;
    button.disabled = true;
    button.innerHTML = `${icon("loader-circle")}<span>${busyLabel}</span>`;
  } else {
    button.disabled = false;
    button.innerHTML = button.dataset.original || button.innerHTML;
  }
  refreshIcons();
}

function dateLabel(value) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function avatarMarkup(person, size = "") {
  const name = String(person.name || "?");
  const initial = [...name].slice(0, 2).join("");
  const hue = ((Number(person.id) || name.codePointAt(0) || 1) * 47) % 360;
  const image = person.avatar_url
    ? `<img data-avatar-img src="${escapeHtml(person.avatar_url)}" alt="" referrerpolicy="no-referrer">`
    : "";
  return `<span class="avatar${size ? ` avatar-${size}` : ""}" style="--avatar-hue:${hue}"><span>${escapeHtml(initial)}</span>${image}</span>`;
}

function bindAvatarFallbacks(root = document) {
  root.querySelectorAll("img[data-avatar-img]").forEach((image) => {
    image.addEventListener("error", () => image.remove(), { once: true });
  });
}

function openEventStream(slug, onSnapshot) {
  closeEventSource();
  const source = new EventSource(appUrl(`/api/events/${encodeURIComponent(slug)}/stream`));
  source.addEventListener("snapshot", (message) => {
    try {
      onSnapshot(JSON.parse(message.data));
    } catch {
      source.close();
    }
  });
  state.eventSource = source;
}

async function copyText(value) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const input = document.createElement("textarea");
  input.value = value;
  input.style.position = "fixed";
  input.style.opacity = "0";
  document.body.append(input);
  input.select();
  document.execCommand("copy");
  input.remove();
}

function defaultSlug() {
  const now = new Date();
  const parts = [now.getFullYear(), now.getMonth() + 1, now.getDate()]
    .map((part) => String(part).padStart(2, "0"))
    .join("");
  return `wedding-${parts}`;
}

function closeEventSource() {
  state.eventSource?.close();
  state.eventSource = null;
}

function adminLink(slug) {
  return appUrl(`/admin?event=${encodeURIComponent(slug)}`);
}

function renderAdminLogin() {
  document.title = "管理登录 · 喜礼现场";
  appRoot.innerHTML = `
    <main class="login-page">
      <section class="login-copy">
        <div class="login-brand">
          <div class="brand-mark" aria-hidden="true">囍</div>
          <strong>喜礼现场</strong>
        </div>
        <div class="login-copy-main">
          <p class="eyebrow">WEDDING LOTTERY</p>
          <h1>把这一刻的欢喜<br>送给在场的你</h1>
          <p>宾客扫码签到，多轮喜礼抽取。每一份惊喜都由服务端随机产生并留存。</p>
        </div>
        <span class="login-meta">WEDDING DESK / ADMIN ONLY</span>
      </section>
      <section class="login-form-wrap">
        <form id="login-form" class="login-form">
          <p class="eyebrow">WEDDING DESK</p>
          <h2>进入管理台</h2>
          <p>使用服务器环境变量中设置的管理密码。</p>
          <label class="field">
            <span>管理密码</span>
            <input name="password" type="password" required maxlength="200" autocomplete="current-password" autofocus>
          </label>
          <button class="button button-primary" type="submit">
            ${icon("log-in")}<span>登录</span>
          </button>
        </form>
      </section>
    </main>`;
  refreshIcons();

  document.querySelector("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.currentTarget.querySelector("button");
    const formData = new FormData(event.currentTarget);
    setButtonBusy(button, true, "正在验证");
    try {
      await api("/api/admin/login", {
        method: "POST",
        body: { password: formData.get("password") },
      });
      await bootAdmin();
    } catch (error) {
      showToast(error.message, "error");
      setButtonBusy(button, false);
    }
  });
}

function adminShell(content, activeSlug = "") {
  const eventLinks = state.adminEvents
    .map(
      (event) => `
        <a class="sidebar-link${event.slug === activeSlug ? " is-active" : ""}" href="${adminLink(event.slug)}">
          ${icon("ticket")}
          <span>${escapeHtml(event.title)}</span>
        </a>`,
    )
    .join("");
  return `
    <div class="admin-shell">
      <aside class="admin-sidebar">
        <a class="admin-brand" href="${appUrl("/admin")}">
          <div class="brand-mark" aria-hidden="true">囍</div>
          <div><strong>喜礼现场</strong><span>WEDDING DESK</span></div>
        </a>
        <div class="sidebar-section">
          <p class="sidebar-label">婚礼场次</p>
          <nav class="sidebar-events">
            <a class="sidebar-link${!activeSlug ? " is-active" : ""}" href="${appUrl("/admin")}">
              ${icon("layout-dashboard")}<span>婚礼总览</span>
            </a>
            ${eventLinks}
            <a class="sidebar-link" href="${appUrl("/admin?new=1")}">
              ${icon("plus")}<span>新建婚礼</span>
            </a>
          </nav>
        </div>
        <div class="sidebar-spacer"></div>
        <div class="sidebar-footer">
          <button id="logout-button" class="sidebar-action" type="button">
            ${icon("log-out")}<span>退出管理台</span>
          </button>
        </div>
      </aside>
      <main class="admin-main">${content}</main>
    </div>`;
}

function bindAdminShell() {
  document.querySelector("#logout-button")?.addEventListener("click", async () => {
    await api("/api/admin/logout", { method: "POST" });
    state.adminEvents = [];
    history.replaceState({}, "", appUrl("/admin"));
    renderAdminLogin();
  });
}

function renderAdminOverview() {
  document.title = "婚礼总览 · 喜礼现场";
  const content = `
    <header class="page-heading">
      <div>
        <p class="eyebrow">WEDDINGS</p>
        <h1>婚礼总览</h1>
        <p>${state.adminEvents.length ? `共 ${state.adminEvents.length} 场婚礼` : "从第一场婚礼开始"}</p>
      </div>
      <a class="button button-primary" href="${appUrl("/admin?new=1")}">
        ${icon("plus")}<span>新建婚礼</span>
      </a>
    </header>
    ${
      state.adminEvents.length
        ? `<section class="events-grid">${state.adminEvents
            .map(
              (event) => `
                <a class="event-card${event.registration_open ? "" : " is-closed"}" href="${adminLink(event.slug)}">
                  <div class="event-card-top">
                    <div>
                      <h2>${escapeHtml(event.title)}</h2>
                      <div class="event-code">${escapeHtml(event.slug)}</div>
                    </div>
                    <span class="status-pill${event.registration_open ? "" : " is-closed"}">
                      ${event.registration_open ? "签到中" : "已关闭"}
                    </span>
                  </div>
                  <div class="event-card-stats">
                    <div><span class="stat-value">${event.participant_count}</span><span class="stat-label">已签到宾客</span></div>
                    <div><span class="stat-value">${event.drawn_rounds}/${event.round_count}</span><span class="stat-label">已开奖轮次</span></div>
                  </div>
                </a>`,
            )
            .join("")}</section>`
        : `<section class="empty-state">
            ${icon("scan-line")}
          <h2>还没有婚礼场次</h2>
          <p>创建婚礼后即可生成宾客签到二维码。</p>
            <a class="button button-primary" href="${appUrl("/admin?new=1")}">${icon("plus")}<span>新建婚礼</span></a>
          </section>`
    }`;
  appRoot.innerHTML = adminShell(content);
  bindAdminShell();
  refreshIcons();
}

function roundRow(index, round = {}) {
  return `
    <div class="round-row" data-round-row>
      <span class="round-number">${index + 1}</span>
      <label class="field">
        <span>轮次名称</span>
        <input name="round-name" required maxlength="40" value="${escapeHtml(round.name || `第 ${index + 1} 轮`)}">
      </label>
      <label class="field">
        <span>奖品</span>
        <input name="round-prize" required maxlength="100" placeholder="例如：旅行基金" value="${escapeHtml(round.prize || "")}">
      </label>
      <label class="field">
        <span>人数</span>
        <input name="round-count" type="number" required min="1" max="500" value="${round.winner_count || 1}">
      </label>
      <button class="icon-button" type="button" data-remove-round title="删除本轮" aria-label="删除本轮">
        ${icon("trash-2")}
      </button>
    </div>`;
}

function renumberRounds(container) {
  [...container.querySelectorAll("[data-round-row]")].forEach((row, index) => {
    row.querySelector(".round-number").textContent = index + 1;
    const nameInput = row.querySelector('[name="round-name"]');
    if (/^第 \d+ 轮$/.test(nameInput.value)) nameInput.value = `第 ${index + 1} 轮`;
  });
}

function renderCreateEvent() {
  document.title = "新建婚礼 · 喜礼现场";
  const content = `
    <header class="page-heading">
      <div>
        <p class="eyebrow">NEW WEDDING</p>
        <h1>新建婚礼</h1>
        <p>保存后立即生成宾客签到二维码。</p>
      </div>
    </header>
    <form id="event-form" class="form-panel">
      <div class="form-grid">
        <label class="field">
          <span>婚礼名称</span>
          <input name="title" required maxlength="80" placeholder="例如：子衿 & 清和的婚礼" autofocus>
        </label>
        <label class="field">
          <span>婚礼代码</span>
          <input name="slug" required minlength="2" maxlength="40" pattern="[a-z0-9][a-z0-9\\-]{1,39}" value="${defaultSlug()}">
        </label>
      </div>
      <div class="form-divider"></div>
      <div class="form-section-head">
        <span class="form-section-label">喜礼轮次</span>
        <button id="add-round" class="button button-small button-ghost" type="button">
          ${icon("plus")}<span>添加一轮</span>
        </button>
      </div>
      <div id="round-editor" class="round-editor">
        ${roundRow(0, { name: "欢喜伴手礼", prize: "定制喜礼", winner_count: 5 })}
        ${roundRow(1, { name: "甜蜜幸运奖", prize: "品质家电", winner_count: 2 })}
        ${roundRow(2, { name: "幸福锦鲤", prize: "惊喜红包", winner_count: 1 })}
      </div>
      <div class="form-actions">
        <a class="button button-ghost" href="${appUrl("/admin")}">取消</a>
        <button class="button button-primary" type="submit">
          ${icon("check")}<span>创建婚礼</span>
        </button>
      </div>
    </form>`;
  appRoot.innerHTML = adminShell(content);
  bindAdminShell();
  refreshIcons();

  const editor = document.querySelector("#round-editor");
  document.querySelector("#add-round").addEventListener("click", () => {
    const index = editor.querySelectorAll("[data-round-row]").length;
    if (index >= 30) return showToast("最多设置 30 轮", "error");
    editor.insertAdjacentHTML("beforeend", roundRow(index));
    refreshIcons();
  });
  editor.addEventListener("click", (event) => {
    const removeButton = event.target.closest("[data-remove-round]");
    if (!removeButton) return;
    if (editor.querySelectorAll("[data-round-row]").length === 1) {
      return showToast("至少保留一轮抽奖", "error");
    }
    removeButton.closest("[data-round-row]").remove();
    renumberRounds(editor);
  });

  document.querySelector("#event-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.currentTarget.querySelector('[type="submit"]');
    const formData = new FormData(event.currentTarget);
    const rows = [...editor.querySelectorAll("[data-round-row]")];
    const rounds = rows.map((row) => ({
      name: row.querySelector('[name="round-name"]').value,
      prize: row.querySelector('[name="round-prize"]').value,
      winner_count: Number(row.querySelector('[name="round-count"]').value),
    }));
    setButtonBusy(button, true, "正在创建");
    try {
      const created = await api("/api/admin/events", {
        method: "POST",
        body: {
          title: formData.get("title"),
          slug: formData.get("slug"),
          rounds,
        },
      });
      showToast("婚礼已创建");
      history.pushState({}, "", adminLink(created.slug));
      await bootAdmin();
    } catch (error) {
      showToast(error.message, "error");
      setButtonBusy(button, false);
    }
  });
}

async function bootAdmin() {
  closeEventSource();
  document.body.className = "admin-mode";
  try {
    const session = await api("/api/admin/session");
    if (!session.authenticated) return renderAdminLogin();
    state.adminEvents = await api("/api/admin/events");
    const params = new URLSearchParams(location.search);
    if (params.has("new")) return renderCreateEvent();
    const slug = params.get("event");
    if (slug) return renderAdminEvent(slug);
    renderAdminOverview();
  } catch (error) {
    showToast(error.message, "error");
    renderAdminLogin();
  }
}

function renderAdminEvent(slug) {
  appRoot.innerHTML = adminShell(`
    <section class="empty-state">
      ${icon("loader-circle")}
      <h2>正在读取婚礼</h2>
    </section>`, slug);
  bindAdminShell();
  refreshIcons();
  loadAdminEvent(slug);
}

async function loadAdminEvent(slug) {
  try {
    state.currentEvent = await api(`/api/admin/events/${encodeURIComponent(slug)}`);
    renderAdminEventDetail();
    openEventStream(slug, (snapshot) => {
      const adminFields = {
        join_url: state.currentEvent.join_url,
        draw_url: state.currentEvent.draw_url,
        qr_url: state.currentEvent.qr_url,
      };
      state.currentEvent = { ...snapshot, ...adminFields };
      if (!document.querySelector("#admin-round-editor:focus-within")) {
        renderAdminEventDetail();
      } else {
        document.querySelectorAll("[data-participant-count]").forEach((element) => {
          element.textContent = snapshot.participant_count;
        });
      }
    });
  } catch (error) {
    if (error.status === 401) return renderAdminLogin();
    showToast(error.message, "error");
    history.replaceState({}, "", appUrl("/admin"));
    renderAdminOverview();
  }
}

function renderAdminEventDetail() {
  const event = state.currentEvent;
  const winnerCount = event.rounds.reduce((total, round) => total + round.winners.length, 0);
  const drawnCount = event.rounds.filter((round) => round.status === "drawn").length;
  const hasResults = winnerCount > 0;
  const roundEditor = event.rounds
    .map((round, index) => roundRow(index, round))
    .join("");
  const readonlyRounds = event.rounds
    .map(
      (round) => `
        <div class="round-summary">
          <span class="round-number">${round.position}</span>
          <strong>${escapeHtml(round.name)}</strong>
          <span class="prize-name">${escapeHtml(round.prize)}</span>
          <span>${round.winner_count} 人</span>
          <span class="round-state${round.status === "drawn" ? " is-drawn" : ""}">${round.status === "drawn" ? "已开奖" : "待开奖"}</span>
        </div>`,
    )
    .join("");
  const winnerLedger = event.rounds
    .filter((round) => round.winners.length)
    .map(
      (round) => `
        <div class="winner-group">
          <div class="winner-group-title">
            <strong>${escapeHtml(round.name)}</strong>
            <span>${escapeHtml(round.prize)}</span>
          </div>
          <div class="winner-chips">
            ${round.winners
              .map(
                (winner) => `<span class="winner-chip">${avatarMarkup(winner, "small")}<strong>${escapeHtml(winner.name)}</strong></span>`,
              )
              .join("")}
          </div>
        </div>`,
    )
    .join("");
  const participants = event.participants
    .map(
      (participant) => `
        <div class="participant-item" data-participant data-name="${escapeHtml(participant.name.toLocaleLowerCase())}">
          ${avatarMarkup(participant)}
          <div class="participant-name">
            <strong>${escapeHtml(participant.name)}</strong>
            <span>${participant.source === "wechat" ? "微信签到" : dateLabel(participant.created_at)}</span>
          </div>
          ${
            hasResults && event.rounds.some((round) => round.winners.some((winner) => winner.id === participant.id))
              ? `<span title="已中奖">${icon("award")}</span>`
              : `<button class="icon-button" type="button" data-remove-participant="${participant.id}" title="移出名单" aria-label="移出 ${escapeHtml(participant.name)}">${icon("x")}</button>`
          }
        </div>`,
    )
    .join("");
  document.title = `${event.title} · 喜礼现场`;
  const content = `
    <header class="page-heading">
      <div>
        <p class="eyebrow">${escapeHtml(event.slug)}</p>
        <h1>${escapeHtml(event.title)}</h1>
        <p><span data-participant-count>${event.participant_count}</span> 位宾客已签到 · ${event.rounds.length} 轮喜礼</p>
      </div>
      <a class="button button-primary" href="${appUrl(`/draw/${encodeURIComponent(event.slug)}`)}">
        ${icon("presentation")}<span>进入喜礼大屏</span>
      </a>
    </header>
    <section class="control-strip">
      <div class="control-stat"><span>已签到宾客</span><strong data-participant-count>${event.participant_count}</strong></div>
      <div class="control-stat"><span>喜礼进度</span><strong>${drawnCount}/${event.rounds.length}</strong></div>
      <div class="control-stat"><span>已送出喜礼</span><strong>${winnerCount}</strong></div>
      <div class="registration-control">
        <div><strong>扫码签到</strong><small>${event.registration_open ? "入口当前开放" : "入口当前关闭"}</small></div>
        <label class="toggle-control" title="切换签到状态">
          <input id="registration-toggle" type="checkbox" role="switch" ${event.registration_open ? "checked" : ""}>
          <span class="toggle-track"></span>
        </label>
      </div>
    </section>
    <div class="admin-workspace">
      <div class="admin-primary">
        <section class="panel-section">
          <header class="section-head">
            <div><h2>喜礼轮次</h2><p>${hasResults ? "已有开奖结果，重置后可重新编辑" : "依次抽取，同一位宾客不会重复中奖"}</p></div>
            <div class="section-actions">
              ${hasResults ? `<button id="reset-draws" class="button button-small button-ghost" type="button">${icon("rotate-ccw")}<span>重置结果</span></button>` : ""}
              ${!hasResults ? `<button id="add-admin-round" class="button button-small button-ghost" type="button">${icon("plus")}<span>添加</span></button><button id="save-rounds" class="button button-small button-jade" type="button">${icon("save")}<span>保存轮次</span></button>` : ""}
            </div>
          </header>
          ${hasResults ? `<div class="rounds-readonly">${readonlyRounds}</div>` : `<div id="admin-round-editor" class="round-editor">${roundEditor}</div>`}
        </section>
        ${hasResults ? `<section class="panel-section"><header class="section-head"><div><h2>开奖结果</h2><p>结果已由服务端固化</p></div></header><div class="winner-ledger">${winnerLedger}</div></section>` : ""}
        <section class="panel-section">
          <header class="section-head">
            <div><h2>签到宾客</h2><p>共 <span data-participant-count>${event.participant_count}</span> 人</p></div>
            <div class="participant-tools">
              <label class="search-box">
                ${icon("search")}<input id="participant-search" type="search" placeholder="查找宾客" aria-label="查找宾客">
              </label>
            </div>
          </header>
          <div id="participant-list" class="participant-list">
            ${participants || `<div class="list-empty">宾客扫码签到后，名单会实时出现在这里。</div>`}
          </div>
        </section>
      </div>
      <aside class="qr-panel">
        <p class="eyebrow">GUEST CHECK-IN</p>
        <h2>扫码签到</h2>
        <p>${event.registration_open ? "签到入口已开放" : "签到入口已关闭"}</p>
        <div class="qr-frame"><img src="${escapeHtml(event.qr_url)}" alt="${escapeHtml(event.title)}签到二维码"></div>
        <span class="join-url" title="${escapeHtml(event.join_url)}">${escapeHtml(event.join_url)}</span>
        <div class="qr-actions">
          <button id="copy-join-url" class="button button-small" type="button">${icon("copy")}<span>复制链接</span></button>
          <a class="button button-small" href="${escapeHtml(event.qr_url)}" download="${escapeHtml(event.slug)}-qr.png">${icon("download")}<span>下载</span></a>
        </div>
      </aside>
    </div>`;
  appRoot.innerHTML = adminShell(content, event.slug);
  bindAdminShell();
  bindAvatarFallbacks();
  refreshIcons();

  document.querySelector("#registration-toggle").addEventListener("change", async (changeEvent) => {
    const toggle = changeEvent.currentTarget;
    toggle.disabled = true;
    try {
      await api(`/api/admin/events/${encodeURIComponent(event.slug)}/registration`, {
        method: "PATCH",
        body: { open: toggle.checked },
      });
      state.currentEvent.registration_open = toggle.checked;
      showToast(toggle.checked ? "签到入口已开放" : "签到入口已关闭");
      renderAdminEventDetail();
    } catch (error) {
      toggle.checked = !toggle.checked;
      toggle.disabled = false;
      showToast(error.message, "error");
    }
  });

  document.querySelector("#copy-join-url").addEventListener("click", async () => {
    try {
      await copyText(event.join_url);
      showToast("签到链接已复制");
    } catch {
      showToast("复制失败，请手动选择链接", "error");
    }
  });

  const editor = document.querySelector("#admin-round-editor");
  if (editor) {
    document.querySelector("#add-admin-round").addEventListener("click", () => {
      const index = editor.querySelectorAll("[data-round-row]").length;
      if (index >= 30) return showToast("最多设置 30 轮", "error");
      editor.insertAdjacentHTML("beforeend", roundRow(index));
      refreshIcons();
    });
    editor.addEventListener("click", (clickEvent) => {
      const remove = clickEvent.target.closest("[data-remove-round]");
      if (!remove) return;
      if (editor.querySelectorAll("[data-round-row]").length === 1) {
        return showToast("至少保留一轮抽奖", "error");
      }
      remove.closest("[data-round-row]").remove();
      renumberRounds(editor);
    });
    document.querySelector("#save-rounds").addEventListener("click", async (clickEvent) => {
      const rows = [...editor.querySelectorAll("[data-round-row]")];
      const rounds = rows.map((row) => ({
        name: row.querySelector('[name="round-name"]').value,
        prize: row.querySelector('[name="round-prize"]').value,
        winner_count: Number(row.querySelector('[name="round-count"]').value),
      }));
      if (rounds.some((round) => !round.name.trim() || !round.prize.trim() || round.winner_count < 1)) {
        return showToast("请完整填写每一轮", "error");
      }
      setButtonBusy(clickEvent.currentTarget, true, "保存中");
      try {
        const snapshot = await api(`/api/admin/events/${encodeURIComponent(event.slug)}/rounds`, {
          method: "PUT",
          body: { rounds },
        });
        state.currentEvent = {
          ...snapshot,
          join_url: event.join_url,
          draw_url: event.draw_url,
          qr_url: event.qr_url,
        };
        showToast("轮次已保存");
        renderAdminEventDetail();
      } catch (error) {
        showToast(error.message, "error");
        setButtonBusy(clickEvent.currentTarget, false);
      }
    });
  }

  document.querySelector("#reset-draws")?.addEventListener("click", async (clickEvent) => {
    if (!window.confirm("确定清空全部开奖结果吗？宾客签到名单会保留。")) return;
    setButtonBusy(clickEvent.currentTarget, true, "正在重置");
    try {
      await api(`/api/admin/events/${encodeURIComponent(event.slug)}/reset-draws`, { method: "POST" });
      state.currentEvent = await api(`/api/admin/events/${encodeURIComponent(event.slug)}`);
      showToast("开奖结果已重置");
      renderAdminEventDetail();
    } catch (error) {
      showToast(error.message, "error");
      setButtonBusy(clickEvent.currentTarget, false);
    }
  });

  document.querySelector("#participant-search").addEventListener("input", (inputEvent) => {
    const query = inputEvent.currentTarget.value.trim().toLocaleLowerCase();
    let visible = 0;
    document.querySelectorAll("[data-participant]").forEach((item) => {
      const match = !query || item.dataset.name.includes(query);
      item.hidden = !match;
      if (match) visible += 1;
    });
    const list = document.querySelector("#participant-list");
    list.querySelector("[data-search-empty]")?.remove();
    if (!visible && event.participants.length) {
      list.insertAdjacentHTML("beforeend", '<div class="list-empty" data-search-empty>没有匹配的姓名。</div>');
    }
  });

  document.querySelector("#participant-list").addEventListener("click", async (clickEvent) => {
    const button = clickEvent.target.closest("[data-remove-participant]");
    if (!button) return;
    const item = button.closest("[data-participant]");
    const name = item.querySelector(".participant-name strong").textContent;
    if (!window.confirm(`确定将“${name}”移出签到名单吗？`)) return;
    button.disabled = true;
    try {
      await api(`/api/admin/events/${encodeURIComponent(event.slug)}/participants/${button.dataset.removeParticipant}`, {
        method: "DELETE",
      });
      state.currentEvent.participants = state.currentEvent.participants.filter(
        (participant) => String(participant.id) !== button.dataset.removeParticipant,
      );
      state.currentEvent.participant_count -= 1;
      showToast("已移出签到名单");
      renderAdminEventDetail();
    } catch (error) {
      button.disabled = false;
      showToast(error.message, "error");
    }
  });
}

function route() {
  const path = basePath && location.pathname.startsWith(basePath)
    ? location.pathname.slice(basePath.length) || "/"
    : location.pathname;
  if (path === "/" || path === "/admin") return bootAdmin();
  const joinMatch = path.match(/^\/e\/([^/]+)$/);
  if (joinMatch) return bootJoin(decodeURIComponent(joinMatch[1]));
  const drawMatch = path.match(/^\/draw\/([^/]+)$/);
  if (drawMatch) return bootDraw(decodeURIComponent(drawMatch[1]));
  appRoot.innerHTML = `<main class="boot-screen"><div class="brand-mark">!</div><p>页面不存在</p></main>`;
}

function arrivalGridMarkup(participants) {
  const visibleParticipants = participants.slice(-25).reverse();
  if (!visibleParticipants.length) {
    return '<div class="arrival-empty">等待第一位宾客签到</div>';
  }
  return visibleParticipants
    .map(
      (participant, index) => `
        <div class="arrival-person" style="animation-delay:${Math.min(index * 25, 300)}ms">
          ${avatarMarkup(participant)}
          <span>${escapeHtml(participant.name)}</span>
        </div>`,
    )
    .join("");
}

function wechatOAuthUrl(slug) {
  return appUrl(`/auth/wechat/start?event=${encodeURIComponent(slug)}`);
}

function shouldStartWechatOAuth(event, participant, query) {
  return (
    !participant &&
    event.registration_open &&
    event.wechat_enabled &&
    /MicroMessenger/i.test(navigator.userAgent) &&
    !query.has("wechat_error")
  );
}

function joinActionMarkup(event, participant) {
  if (participant) {
    return `
      <section class="join-ticket">
        ${avatarMarkup(participant)}
        <div class="ticket-copy">
          <span>签到成功 · 已进入喜礼名单</span>
          <strong>${escapeHtml(participant.name)}</strong>
        </div>
        <span class="ticket-number">NO.${String(participant.id).padStart(4, "0")}</span>
      </section>`;
  }
  if (!event.registration_open) {
    return `
      <section class="join-closed">
        ${icon("door-closed")}
        <h2>签到已经结束</h2>
        <p>已签到的宾客仍保留喜礼抽取资格。</p>
      </section>`;
  }
  return `
    <section class="join-form-panel">
      <h2>留下名字，接住喜气</h2>
      <p>填写姓名后，你会立即进入本场喜礼名单。</p>
      <form id="join-form">
        <label class="join-name-field">
          <span>你的姓名</span>
          <input name="name" required maxlength="40" autocomplete="name" placeholder="请输入姓名">
        </label>
        <button class="button button-primary join-submit" type="submit">
          ${icon("ticket-check")}<span>确认签到</span>
        </button>
      </form>
      ${
        event.wechat_enabled
          ? `<div class="join-separator">或</div>
             <a class="button wechat-button" href="${wechatOAuthUrl(event.slug)}">
               ${icon("message-circle")}<span>使用微信昵称与头像签到</span>
             </a>
             <p class="privacy-note">${icon("shield-check")}昵称与头像仅用于本场婚礼</p>`
          : ""
      }
    </section>`;
}

function renderJoinPage() {
  const event = state.currentEvent;
  const participant = state.currentParticipant;
  document.title = `${event.title} · 宾客签到`;
  appRoot.innerHTML = `
    <main class="join-page">
      <header class="join-topbar">
        <span class="join-brand"><span class="brand-mark" aria-hidden="true">囍</span><strong>喜礼现场</strong></span>
        <span class="live-status${event.registration_open ? "" : " is-closed"}" data-live-status>${event.registration_open ? "宾客签到中" : "签到已关闭"}</span>
      </header>
      <div class="join-layout">
        <section class="join-primary">
          <div class="event-kicker">WELCOME TO OUR WEDDING</div>
          <h1>${escapeHtml(event.title)}</h1>
          <p class="join-summary"><strong data-arrival-count>${event.participant_count}</strong> 位亲友已签到</p>
          <div id="join-action">${joinActionMarkup(event, participant)}</div>
        </section>
        <aside class="arrivals-panel">
          <header class="arrivals-head">
            <div><p>GUEST BOOK</p><h2>亲友签到</h2></div>
            <strong class="arrival-count" data-arrival-count>${event.participant_count}</strong>
          </header>
          <div id="arrival-grid" class="arrival-grid">${arrivalGridMarkup(event.participants)}</div>
        </aside>
      </div>
      <footer class="join-footer">${escapeHtml(event.slug).toUpperCase()} · WEDDING LOTTERY</footer>
    </main>`;
  bindAvatarFallbacks();
  refreshIcons();

  document.querySelector("#join-form")?.addEventListener("submit", async (submitEvent) => {
    submitEvent.preventDefault();
    const form = submitEvent.currentTarget;
    const button = form.querySelector("button");
    const name = new FormData(form).get("name");
    setButtonBusy(button, true, "正在加入");
    try {
      state.currentParticipant = await api(`/api/events/${encodeURIComponent(event.slug)}/participants`, {
        method: "POST",
        body: { name },
      });
      state.currentEvent = await api(`/api/events/${encodeURIComponent(event.slug)}`);
      renderJoinPage();
      showToast("签到成功，已进入喜礼名单");
    } catch (error) {
      showToast(error.message, "error");
      setButtonBusy(button, false);
    }
  });
}

function updateJoinSnapshot(snapshot) {
  const registrationChanged = state.currentEvent.registration_open !== snapshot.registration_open;
  state.currentEvent = snapshot;
  if (registrationChanged && !state.currentParticipant) {
    renderJoinPage();
    return;
  }
  document.querySelectorAll("[data-arrival-count]").forEach((element) => {
    element.textContent = snapshot.participant_count;
  });
  const statusElement = document.querySelector("[data-live-status]");
  if (statusElement) {
    statusElement.textContent = snapshot.registration_open ? "宾客签到中" : "签到已关闭";
    statusElement.classList.toggle("is-closed", !snapshot.registration_open);
  }
  const grid = document.querySelector("#arrival-grid");
  if (grid) {
    grid.innerHTML = arrivalGridMarkup(snapshot.participants);
    bindAvatarFallbacks(grid);
  }
}

async function bootJoin(slug) {
  closeEventSource();
  document.body.className = "join-mode";
  appRoot.innerHTML = `<main class="boot-screen"><div class="brand-mark">囍</div><p>婚礼签到页正在载入</p></main>`;
  try {
    [state.currentEvent, state.currentParticipant] = await Promise.all([
      api(`/api/events/${encodeURIComponent(slug)}`),
      api(`/api/events/${encodeURIComponent(slug)}/me`),
    ]);
    const query = new URLSearchParams(location.search);
    if (shouldStartWechatOAuth(state.currentEvent, state.currentParticipant, query)) {
      document.title = `${state.currentEvent.title} · 微信签到`;
      appRoot.innerHTML = `<main class="boot-screen"><div class="brand-mark">囍</div><p>正在连接微信资料</p></main>`;
      location.replace(wechatOAuthUrl(slug));
      return;
    }
    renderJoinPage();
    if (query.get("wechat_error")) showToast(query.get("wechat_error"), "error");
    if (query.get("joined") === "wechat") showToast("微信资料已确认，签到成功");
    openEventStream(slug, updateJoinSnapshot);
  } catch (error) {
    document.title = "婚礼不存在 · 喜礼现场";
    appRoot.innerHTML = `
      <main class="boot-screen">
        <div class="brand-mark">!</div>
        <h1>没有找到这场婚礼</h1>
        <p>${escapeHtml(error.message)}</p>
      </main>`;
  }
}

function winnerIdSet(event) {
  return new Set(
    event.rounds.flatMap((round) => round.winners.map((winner) => winner.id)),
  );
}

function chooseDrawRound() {
  const rounds = state.currentEvent.rounds;
  if (rounds.some((round) => round.id === state.selectedRoundId)) return;
  const firstPending = rounds.find((round) => round.status === "pending");
  state.selectedRoundId = (firstPending || rounds.at(-1))?.id ?? null;
}

function drawRosterMarkup(participants) {
  return participants
    .slice(-32)
    .map(
      (participant) => `
        <div class="roster-person">
          ${avatarMarkup(participant)}
          <span>${escapeHtml(participant.name)}</span>
        </div>`,
    )
    .join("");
}

function drawRoundTabs(event) {
  return event.rounds
    .map(
      (round) => `
        <button class="round-tab${round.id === state.selectedRoundId ? " is-active" : ""}${round.status === "drawn" ? " is-drawn" : ""}" type="button" data-select-round="${round.id}">
          ${icon(round.status === "drawn" ? "circle-check" : "circle-dashed")}
          <span>${escapeHtml(round.name)}</span>
        </button>`,
    )
    .join("");
}

function pendingStageMarkup(event, round) {
  const winnerIds = winnerIdSet(event);
  const eligible = event.participants.filter((participant) => !winnerIds.has(participant.id));
  const preview = eligible.length
    ? eligible[round.id % eligible.length]
    : { id: 0, name: "等待宾客", avatar_url: null };
  const firstPending = event.rounds.find((item) => item.status === "pending");
  const inSequence = firstPending?.id === round.id;
  const shortage = Math.max(0, round.winner_count - eligible.length);

  let action;
  if (!state.adminAuthenticated) {
    action = `<a class="draw-secondary-button" href="${adminLink(event.slug)}">${icon("lock-keyhole")}<span>登录管理台后开奖</span></a>`;
  } else if (!inSequence) {
    action = `<button class="draw-primary-button" type="button" disabled>${icon("list-ordered")}<span>请先完成前序轮次</span></button>`;
  } else if (shortage) {
    action = `<button class="draw-primary-button" type="button" disabled>${icon("user-round-plus")}<span>还差 ${shortage} 人</span></button>`;
  } else {
    action = `<button id="start-draw" class="draw-primary-button" type="button">${icon("play")}<span>开始抽奖</span></button>`;
  }

  return `
    <section class="pending-stage">
      <p class="stage-kicker">${escapeHtml(round.name).toUpperCase()}</p>
      <h1 class="stage-prize">${escapeHtml(round.prize)}</h1>
      <p class="stage-meta">本轮抽取 ${round.winner_count} 人 · 剩余候选 ${eligible.length} 人</p>
      <div class="candidate-machine">
        <div id="candidate-focus" class="candidate-focus">
          ${avatarMarkup(preview)}<strong>${escapeHtml(preview.name)}</strong>
        </div>
      </div>
      ${action}
    </section>`;
}

function resultStageMarkup(event, round) {
  const nextRound = event.rounds.find(
    (item) => item.position > round.position && item.status === "pending",
  );
  const winnerCards = round.winners
    .map(
      (winner, index) => `
        <article class="winner-card" style="animation-delay:${index * 90}ms">
          <span class="winner-position">LUCKY GUEST ${String(index + 1).padStart(2, "0")}</span>
          ${avatarMarkup(winner)}
          <strong>${escapeHtml(winner.name)}</strong>
        </article>`,
    )
    .join("");
  return `
    <section class="result-stage">
      <p class="stage-kicker">LUCKY GUESTS</p>
      <h1 class="stage-prize">${escapeHtml(round.name)}</h1>
      <p class="result-prize-name">${escapeHtml(round.prize)}</p>
      <div class="winner-grid${round.winners.length === 1 ? " is-solo" : ""}">${winnerCards}</div>
      <div class="result-actions">
        ${
          nextRound
            ? `<button class="draw-secondary-button" type="button" data-select-round="${nextRound.id}">${icon("arrow-right")}<span>进入下一轮</span></button>`
            : `<a class="draw-secondary-button" href="${adminLink(event.slug)}">${icon("layout-dashboard")}<span>返回管理台</span></a>`
        }
      </div>
    </section>`;
}

function renderDrawPage() {
  const event = state.currentEvent;
  chooseDrawRound();
  const round = event.rounds.find((item) => item.id === state.selectedRoundId);
  document.body.className = "draw-mode";
  document.title = `${event.title} · 喜礼大屏`;

  if (!round) {
    appRoot.innerHTML = `
      <main class="draw-page"><section class="draw-stage"><div class="draw-error">
        <div class="brand-mark">!</div><h1>还没有抽奖轮次</h1>
        <a class="draw-secondary-button" href="${adminLink(event.slug)}">返回管理台</a>
      </div></section></main>`;
    return;
  }

  appRoot.innerHTML = `
    <main class="draw-page">
      <header class="draw-topbar">
        <div class="draw-brand"><span class="brand-mark" aria-hidden="true">囍</span><strong>喜礼现场</strong></div>
        <span class="draw-event-title">${escapeHtml(event.title)}</span>
        <div class="draw-top-actions">
          <span class="draw-count"><span>签到宾客</span><strong data-draw-count>${event.participant_count}</strong></span>
          <button id="fullscreen-button" class="draw-icon-button" type="button" title="全屏" aria-label="切换全屏">${icon("maximize")}</button>
        </div>
      </header>
      <nav class="round-rail" aria-label="抽奖轮次">${drawRoundTabs(event)}</nav>
      <section class="draw-stage">
        ${round.status === "drawn" ? resultStageMarkup(event, round) : pendingStageMarkup(event, round)}
        <div class="stage-roster">${drawRosterMarkup(event.participants)}</div>
      </section>
    </main>`;
  bindAvatarFallbacks();
  refreshIcons();
  bindDrawControls(round);
}

function bindDrawControls(round) {
  document.querySelectorAll("[data-select-round]").forEach((button) => {
    button.addEventListener("click", () => {
      if (state.drawing) return;
      state.selectedRoundId = Number(button.dataset.selectRound);
      renderDrawPage();
    });
  });
  document.querySelector("#fullscreen-button")?.addEventListener("click", async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen();
    } catch {
      showToast("浏览器未允许全屏", "error");
    }
  });
  document.querySelector("#start-draw")?.addEventListener("click", (clickEvent) => {
    startDrawAnimation(round, clickEvent.currentTarget);
  });
}

function animateCandidateRoll(participants) {
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const duration = reducedMotion ? 350 : 3400;
  const focus = document.querySelector("#candidate-focus");
  const page = document.querySelector(".draw-page");
  page?.classList.add("is-spinning");
  if (!focus || !participants.length) return Promise.resolve();

  return new Promise((resolve) => {
    const startedAt = performance.now();
    let lastChange = 0;
    let previousIndex = -1;

    function frame(now) {
      const elapsed = now - startedAt;
      const progress = Math.min(1, elapsed / duration);
      const interval = 45 + Math.pow(progress, 3) * 210;
      if (now - lastChange >= interval) {
        let index = Math.floor(Math.random() * participants.length);
        if (participants.length > 1 && index === previousIndex) {
          index = (index + 1) % participants.length;
        }
        const participant = participants[index];
        focus.innerHTML = `${avatarMarkup(participant)}<strong>${escapeHtml(participant.name)}</strong>`;
        bindAvatarFallbacks(focus);
        previousIndex = index;
        lastChange = now;
      }
      if (progress < 1) requestAnimationFrame(frame);
      else resolve();
    }

    requestAnimationFrame(frame);
  });
}

function launchConfetti() {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const layer = document.createElement("div");
  layer.className = "confetti-layer";
  const colors = ["#a53a3f", "#d7b56d", "#fffaf7", "#55705f"];
  for (let index = 0; index < 90; index += 1) {
    const piece = document.createElement("i");
    piece.className = "confetti-piece";
    piece.style.setProperty("--confetti-x", `${Math.random() * 100}%`);
    piece.style.setProperty("--confetti-color", colors[index % colors.length]);
    piece.style.setProperty("--confetti-duration", `${2.6 + Math.random() * 2}s`);
    piece.style.setProperty("--confetti-delay", `${Math.random() * 0.7}s`);
    piece.style.setProperty("--confetti-drift", `${-90 + Math.random() * 180}px`);
    piece.style.setProperty("--confetti-turn", `${360 + Math.random() * 900}deg`);
    layer.append(piece);
  }
  document.body.append(layer);
  window.setTimeout(() => layer.remove(), 5200);
}

async function startDrawAnimation(round, button) {
  if (state.drawing) return;
  state.drawing = true;
  setButtonBusy(button, true, "正在抽取");
  try {
    await api(
      `/api/admin/events/${encodeURIComponent(state.currentEvent.slug)}/rounds/${round.id}/draw`,
      { method: "POST" },
    );
    const winnerIds = winnerIdSet(state.currentEvent);
    const candidates = state.currentEvent.participants.filter(
      (participant) => !winnerIds.has(participant.id),
    );
    await animateCandidateRoll(candidates);
    state.currentEvent = await api(`/api/events/${encodeURIComponent(state.currentEvent.slug)}`);
    state.selectedRoundId = round.id;
    state.drawing = false;
    renderDrawPage();
    document.querySelectorAll(".winner-card").forEach((card) => card.classList.add("is-revealing"));
    launchConfetti();
  } catch (error) {
    state.drawing = false;
    document.querySelector(".draw-page")?.classList.remove("is-spinning");
    showToast(error.message, "error");
    renderDrawPage();
  }
}

async function bootDraw(slug) {
  closeEventSource();
  document.body.className = "draw-mode";
  appRoot.innerHTML = `<main class="boot-screen"><div class="brand-mark">囍</div><p>喜礼大屏正在载入</p></main>`;
  try {
    const [event, session] = await Promise.all([
      api(`/api/events/${encodeURIComponent(slug)}`),
      api("/api/admin/session"),
    ]);
    state.currentEvent = event;
    state.adminAuthenticated = session.authenticated;
    state.selectedRoundId = null;
    renderDrawPage();
    openEventStream(slug, (snapshot) => {
      state.currentEvent = snapshot;
      if (!state.drawing) renderDrawPage();
    });
  } catch (error) {
    document.title = "无法进入大屏 · 喜礼现场";
    appRoot.innerHTML = `
      <main class="draw-page">
        <section class="draw-stage"><div class="draw-error">
          <div class="brand-mark">!</div><h1>无法进入抽奖现场</h1><p>${escapeHtml(error.message)}</p>
        </div></section>
      </main>`;
  }
}

window.addEventListener("popstate", route);
route();