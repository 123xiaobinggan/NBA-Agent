const state = {
  sessions: [],
  activeSessionId: null,
  busy: false,
  autoScroll: true,
};

const sessionList = document.querySelector("#sessionList");
const messageList = document.querySelector("#messageList");
const chartList = document.querySelector("#chartList");
const sessionTitle = document.querySelector("#sessionTitle");
const sessionMeta = document.querySelector("#sessionMeta");
const chartMeta = document.querySelector("#chartMeta");
const newSessionBtn = document.querySelector("#newSessionBtn");
const messageForm = document.querySelector("#messageForm");
const messageInput = document.querySelector("#messageInput");
const sendBtn = document.querySelector("#sendBtn");

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `请求失败：${response.status}`);
  }
  return data;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function isNearMessageBottom() {
  const distance = messageList.scrollHeight - messageList.scrollTop - messageList.clientHeight;
  return distance < 80;
}

function scrollMessagesToBottom() {
  messageList.scrollTop = messageList.scrollHeight;
}

function maybeScrollMessagesToBottom() {
  if (state.autoScroll) {
    scrollMessagesToBottom();
  }
}

function renderSessions() {
  sessionList.innerHTML = "";
  for (const item of state.sessions) {
    const button = document.createElement("button");
    button.className = `session-item ${item.session_id === state.activeSessionId ? "active" : ""}`;
    button.innerHTML = `
      <span class="session-main">
        <strong>${item.is_pinned ? "置顶 · " : ""}${escapeHtml(item.title)}</strong>
        <span>${item.message_count} 条消息 · ${item.chart_count} 张图表</span>
      </span>
      <span class="session-actions">
        <span class="session-action pin" title="${item.is_pinned ? "取消置顶" : "置顶"}">${item.is_pinned ? "★" : "☆"}</span>
        <span class="session-action delete" title="删除">×</span>
      </span>
    `;
    button.addEventListener("click", () => loadSession(item.session_id));
    button.querySelector(".pin").addEventListener("click", (event) => {
      event.stopPropagation();
      setSessionPinned(item.session_id, !item.is_pinned);
    });
    button.querySelector(".delete").addEventListener("click", (event) => {
      event.stopPropagation();
      deleteSession(item.session_id);
    });
    sessionList.appendChild(button);
  }
}

function appendMessage(message) {
  const wrapper = document.createElement("article");
  wrapper.className = `message ${message.role || "assistant"}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = message.content || "";
  wrapper.appendChild(bubble);

  const charts = Array.isArray(message.charts) ? message.charts : [];
  for (const chart of charts) {
    wrapper.appendChild(createMessageChart(chart));
  }

  messageList.appendChild(wrapper);
  maybeScrollMessagesToBottom();
  return wrapper;
}

function createMessageChart(chart) {
  const image = document.createElement("img");
  image.className = "message-chart";
  image.src = chart.url;
  image.alt = chart.title || "NBA 图表";
  return image;
}

function renderMessages(messages) {
  messageList.innerHTML = "";
  if (!messages.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    messageList.appendChild(empty);
    return;
  }
  for (const message of messages) {
    appendMessage(message);
  }
}

function renderCharts(charts) {
  chartList.innerHTML = "";
  chartMeta.textContent = charts.length ? `当前会话共有 ${charts.length} 张图表` : "当前会话生成的图表会显示在这里";
  for (const chart of charts) {
    const card = document.createElement("section");
    card.className = "chart-card";
    card.innerHTML = `
      <strong>${escapeHtml(chart.title || "NBA 图表")}</strong>
      <img src="${chart.url}" alt="${escapeHtml(chart.title || "NBA 图表")}" />
      <a href="${chart.url}" target="_blank" rel="noreferrer">打开 SVG</a>
    `;
    chartList.appendChild(card);
  }
}

function upsertSession(session) {
  const index = state.sessions.findIndex((item) => item.session_id === session.session_id);
  if (index >= 0) {
    state.sessions[index] = session;
  } else {
    state.sessions.unshift(session);
  }
  renderSessions();
}

async function refreshSessions() {
  const data = await api("/api/sessions");
  state.sessions = data.sessions;
  if (!state.activeSessionId && state.sessions.length) {
    state.activeSessionId = state.sessions[0].session_id;
  }
  renderSessions();
}

async function loadSession(sessionId) {
  if (state.busy && sessionId !== state.activeSessionId) return;
  state.activeSessionId = sessionId;
  state.autoScroll = true;
  renderSessions();
  const data = await api(`/api/sessions/${sessionId}`);
  sessionTitle.textContent = data.session.title;
  sessionMeta.textContent = `会话 ID：${data.session.session_id}`;
  renderMessages(data.messages);
  renderCharts(data.charts);
  upsertSession(data.session);
}

async function createSession() {
  const data = await api("/api/sessions", {
    method: "POST",
    body: JSON.stringify({ title: "新会话" }),
  });
  state.activeSessionId = data.session.session_id;
  state.autoScroll = true;
  upsertSession(data.session);
  sessionTitle.textContent = data.session.title;
  sessionMeta.textContent = `会话 ID：${data.session.session_id}`;
  renderMessages([]);
  renderCharts([]);
}

async function setSessionPinned(sessionId, isPinned) {
  const data = await api(`/api/sessions/${sessionId}/pin`, {
    method: "PATCH",
    body: JSON.stringify({ is_pinned: isPinned }),
  });
  state.sessions = data.sessions;
  renderSessions();
}

async function deleteSession(sessionId) {
  if (state.busy && sessionId === state.activeSessionId) return;
  const target = state.sessions.find((item) => item.session_id === sessionId);
  const title = target ? target.title : "当前会话";
  if (!confirm(`确定删除“${title}”吗？`)) return;

  const data = await api(`/api/sessions/${sessionId}`, { method: "DELETE" });
  state.sessions = data.sessions;
  if (state.activeSessionId === sessionId) {
    state.activeSessionId = state.sessions[0]?.session_id || null;
    if (state.activeSessionId) {
      await loadSession(state.activeSessionId);
    } else {
      sessionTitle.textContent = "新会话";
      sessionMeta.textContent = "准备就绪";
      renderMessages([]);
      renderCharts([]);
    }
  } else {
    renderSessions();
  }
}

function parseSseEvents(buffer) {
  const events = [];
  const blocks = buffer.split("\n\n");
  const rest = blocks.pop() || "";

  for (const block of blocks) {
    let event = "message";
    const dataLines = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) {
        event = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      }
    }
    if (dataLines.length) {
      events.push({ event, data: JSON.parse(dataLines.join("\n")) });
    }
  }

  return { events, rest };
}

async function streamMessage(sessionId, text, assistantNode) {
  const response = await fetch(`/api/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: text }),
  });

  if (!response.ok || !response.body) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error || `请求失败：${response.status}`);
  }

  const decoder = new TextDecoder();
  const reader = response.body.getReader();
  const bubble = assistantNode.querySelector(".bubble");
  let buffer = "";
  let allCharts = [];
  const currentCharts = new Set();

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parsed = parseSseEvents(buffer);
    buffer = parsed.rest;

    for (const item of parsed.events) {
      if (item.event === "session") {
        upsertSession(item.data.session);
        sessionTitle.textContent = item.data.session.title;
      } else if (item.event === "status" && !bubble.textContent) {
        bubble.textContent = item.data.message || "";
      } else if (item.event === "token") {
        if (bubble.textContent === "正在分析数据库...") {
          bubble.textContent = "";
        }
        bubble.textContent += item.data.text || "";
        maybeScrollMessagesToBottom();
      } else if (item.event === "chart") {
        const chart = item.data.chart;
        if (chart && !currentCharts.has(chart.url)) {
          currentCharts.add(chart.url);
          assistantNode.appendChild(createMessageChart(chart));
          maybeScrollMessagesToBottom();
        }
      } else if (item.event === "error") {
        assistantNode.classList.add("error");
        bubble.textContent = item.data.error || "请求失败。";
      } else if (item.event === "done") {
        upsertSession(item.data.session);
        allCharts = item.data.charts || [];
        renderCharts(allCharts);
        sessionTitle.textContent = item.data.session.title;
      }
    }
  }
}

async function sendMessage(event) {
  event.preventDefault();
  if (state.busy || !state.activeSessionId) return;
  const text = messageInput.value.trim();
  if (!text) return;

  state.busy = true;
  state.autoScroll = true;
  sendBtn.disabled = true;
  messageInput.value = "";
  appendMessage({ role: "user", content: text });
  const assistantNode = appendMessage({ role: "assistant", content: "正在分析数据库..." });
  scrollMessagesToBottom();

  try {
    await streamMessage(state.activeSessionId, text, assistantNode);
  } catch (error) {
    assistantNode.classList.add("error");
    assistantNode.querySelector(".bubble").textContent = error.message;
  } finally {
    state.busy = false;
    sendBtn.disabled = false;
    messageInput.focus();
  }
}

newSessionBtn.addEventListener("click", createSession);
messageForm.addEventListener("submit", sendMessage);
messageList.addEventListener("scroll", () => {
  state.autoScroll = isNearMessageBottom();
});
messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    messageForm.requestSubmit();
  }
});

refreshSessions()
  .then(() => {
    if (state.activeSessionId) {
      loadSession(state.activeSessionId);
    }
  })
  .catch((error) => {
    sessionMeta.textContent = "初始化失败";
    renderMessages([{ role: "error", content: error.message }]);
  });
