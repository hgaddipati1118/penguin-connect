const state = {
  conversations: [],
  selected: null,
  messages: [],
  senderEmail: "",
  attachments: [],
};

const el = {
  statusLine: document.querySelector("#statusLine"),
  refreshButton: document.querySelector("#refreshButton"),
  conversationSearch: document.querySelector("#conversationSearch"),
  conversationList: document.querySelector("#conversationList"),
  threadProvider: document.querySelector("#threadProvider"),
  threadTitle: document.querySelector("#threadTitle"),
  syncButton: document.querySelector("#syncButton"),
  copyThreadButton: document.querySelector("#copyThreadButton"),
  messageFilter: document.querySelector("#messageFilter"),
  messageList: document.querySelector("#messageList"),
  senderBadge: document.querySelector("#senderBadge"),
  composer: document.querySelector("#composer"),
  emojiRow: document.querySelector("#emojiRow"),
  attachmentDrop: document.querySelector("#attachmentDrop"),
  fileInput: document.querySelector("#fileInput"),
  attachmentList: document.querySelector("#attachmentList"),
  sendButton: document.querySelector("#sendButton"),
  clearButton: document.querySelector("#clearButton"),
  sendState: document.querySelector("#sendState"),
  codexCount: document.querySelector("#codexCount"),
  codexPrompt: document.querySelector("#codexPrompt"),
  buildPromptButton: document.querySelector("#buildPromptButton"),
  copyPromptButton: document.querySelector("#copyPromptButton"),
};

const emojiChoices = ["👍", "🙏", "🔥", "❤️", "😂", "👀", "✅", "🤔", "😭", "🚀"];

function api(path, options = {}) {
  return fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  }).then(async (response) => {
    const text = await response.text();
    let payload = {};
    try {
      payload = text ? JSON.parse(text) : {};
    } catch (_err) {
      payload = { detail: text || "invalid_response" };
    }
    if (!response.ok) {
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }
    return payload;
  });
}

function formatTime(value) {
  if (!value) return "n/a";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function trim(value, length = 120) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length > length ? `${text.slice(0, length - 3).trim()}...` : text;
}

function basename(value) {
  return String(value || "attachment").split(/[\\/]/).pop() || "attachment";
}

function isOwnMessage(message) {
  return message.sender_name === "Me" || message.direction === "manual_to_imessage" || message.direction === "email_to_imessage";
}

function attachmentRows(message) {
  const metadata = message.metadata && typeof message.metadata === "object" ? message.metadata : {};
  if (Array.isArray(message.attachments)) return message.attachments;
  if (Array.isArray(metadata.attachments)) return metadata.attachments;
  if (metadata.manual_attachment_count) {
    return [{ transfer_name: `${metadata.manual_attachment_count} sent attachment(s)`, mime_type: "" }];
  }
  return [];
}

function renderEmojiButtons() {
  el.emojiRow.replaceChildren();
  for (const emoji of emojiChoices) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "emoji-button";
    button.textContent = emoji;
    button.title = `Insert ${emoji}`;
    button.addEventListener("click", () => {
      const start = el.composer.selectionStart ?? el.composer.value.length;
      const end = el.composer.selectionEnd ?? el.composer.value.length;
      el.composer.value = `${el.composer.value.slice(0, start)}${emoji}${el.composer.value.slice(end)}`;
      el.composer.focus();
      el.composer.selectionStart = start + emoji.length;
      el.composer.selectionEnd = start + emoji.length;
      buildCodexPrompt();
    });
    el.emojiRow.append(button);
  }
}

function renderConversations() {
  const query = el.conversationSearch.value.trim().toLowerCase();
  const rows = state.conversations.filter((conversation) => {
    const haystack = [
      conversation.conversation_id,
      conversation.display_name,
      conversation.source_provider,
      conversation.source_chat_identifier,
      conversation.alias_email,
      ...(conversation.participants || []),
    ].join(" ").toLowerCase();
    return !query || haystack.includes(query);
  });

  el.conversationList.replaceChildren();
  if (!rows.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "No conversations";
    el.conversationList.append(empty);
    return;
  }

  for (const conversation of rows) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `conversation-item ${state.selected?.conversation_id === conversation.conversation_id ? "active" : ""}`;
    button.innerHTML = `
      <span class="conversation-name"></span>
      <span class="conversation-meta"></span>
    `;
    button.querySelector(".conversation-name").textContent = conversation.display_name || "Conversation";
    button.querySelector(".conversation-meta").textContent = [
      conversation.chat_type || "chat",
      conversation.source_service_name || conversation.source_provider || "source",
      conversation.last_message_ts ? formatTime(conversation.last_message_ts) : conversation.status || "",
    ].filter(Boolean).join(" · ");
    button.addEventListener("click", () => selectConversation(conversation));
    el.conversationList.append(button);
  }
}

function renderMessages() {
  const query = el.messageFilter.value.trim().toLowerCase();
  const rows = [...state.messages].reverse().filter((message) => {
    const haystack = [
      message.sender_name,
      message.sender_email,
      message.body_text,
      JSON.stringify(attachmentRows(message)),
    ].join(" ").toLowerCase();
    return !query || haystack.includes(query);
  });

  el.messageList.replaceChildren();
  if (!state.selected) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "Select a conversation";
    el.messageList.append(empty);
    return;
  }
  if (!rows.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "No loaded messages";
    el.messageList.append(empty);
    return;
  }

  for (const message of rows) {
    const item = document.createElement("article");
    item.className = `message ${isOwnMessage(message) ? "mine" : ""}`;
    const attachments = attachmentRows(message);
    item.innerHTML = `
      <div class="message-head">
        <span></span>
        <time></time>
      </div>
      <div class="message-body"></div>
      <div class="message-attachments"></div>
    `;
    item.querySelector(".message-head span").textContent = message.sender_name || message.sender_email || message.direction || "unknown";
    item.querySelector("time").textContent = formatTime(message.message_timestamp || message.timestamp);
    item.querySelector(".message-body").textContent = message.body_text || message.text || "";
    const attachmentBox = item.querySelector(".message-attachments");
    for (const attachment of attachments) {
      const pill = document.createElement("span");
      pill.className = "pill";
      const label = basename(attachment.transfer_name || attachment.filename || attachment.mime_type || "attachment");
      pill.textContent = String(attachment.mime_type || "").startsWith("audio/") ? `audio:${label}` : label;
      attachmentBox.append(pill);
    }
    if (!attachments.length) attachmentBox.remove();
    el.messageList.append(item);
  }
}

function renderAttachments() {
  el.attachmentList.replaceChildren();
  for (const [index, file] of state.attachments.entries()) {
    const chip = document.createElement("div");
    chip.className = "attachment-chip";
    chip.innerHTML = `<span></span><button class="remove-button" type="button" title="Remove">×</button>`;
    chip.querySelector("span").textContent = `${file.name} · ${Math.ceil(file.size / 1024)} KB`;
    chip.querySelector("button").addEventListener("click", () => {
      state.attachments.splice(index, 1);
      renderAttachments();
      buildCodexPrompt();
    });
    el.attachmentList.append(chip);
  }
}

async function loadStatus() {
  try {
    const status = await api("/penguin-connect/health");
    state.senderEmail = status.gmail?.gmail_email || "";
    el.statusLine.textContent = status.ok ? `Bridge ready · ${state.senderEmail || "no gmail"}` : "Bridge warning";
    el.senderBadge.textContent = state.senderEmail || "No sender";
  } catch (error) {
    el.statusLine.textContent = `Bridge offline · ${error.message}`;
    el.senderBadge.textContent = "No sender";
  }
}

async function loadConversations() {
  try {
    const payload = await api("/penguin-connect/conversations");
    state.senderEmail = payload.gmail_email || state.senderEmail;
    state.conversations = payload.conversations || [];
    el.senderBadge.textContent = state.senderEmail || "No sender";
    renderConversations();
    if (!state.selected && state.conversations.length) {
      await selectConversation(state.conversations[0]);
    }
  } catch (error) {
    el.conversationList.innerHTML = `<div class="error-state">${error.message}</div>`;
  }
}

async function selectConversation(conversation) {
  state.selected = conversation;
  state.messages = [];
  el.threadProvider.textContent = [conversation.source_provider, conversation.source_service_name, conversation.chat_type].filter(Boolean).join(" · ");
  el.threadTitle.textContent = conversation.display_name || conversation.conversation_id;
  renderConversations();
  renderMessages();
  await loadMessages();
}

async function loadMessages() {
  if (!state.selected) return;
  try {
    const payload = await api(`/penguin-connect/conversations/${encodeURIComponent(state.selected.conversation_id)}/messages?limit=200`);
    state.messages = payload.messages || [];
    renderMessages();
    buildCodexPrompt();
    requestAnimationFrame(() => {
      el.messageList.scrollTop = el.messageList.scrollHeight;
    });
  } catch (error) {
    el.messageList.innerHTML = `<div class="error-state">${error.message}</div>`;
  }
}

function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const value = String(reader.result || "");
      resolve(value.includes(",") ? value.split(",", 2)[1] : value);
    };
    reader.onerror = () => reject(reader.error || new Error("file_read_failed"));
    reader.readAsDataURL(file);
  });
}

async function sendMessage() {
  if (!state.selected) return;
  const message = el.composer.value;
  if (!message.trim() && !state.attachments.length) {
    el.sendState.textContent = "Nothing to send";
    return;
  }
  if (!state.senderEmail) {
    el.sendState.textContent = "No connected Gmail sender";
    return;
  }

  el.sendButton.disabled = true;
  el.sendState.textContent = "Sending";
  try {
    const attachments = [];
    for (const file of state.attachments) {
      attachments.push({
        filename: file.name,
        mime_type: file.type || "application/octet-stream",
        size: file.size,
        data_base64: await readFileAsBase64(file),
      });
    }
    await api(`/penguin-connect/conversations/${encodeURIComponent(state.selected.conversation_id)}/send`, {
      method: "POST",
      body: JSON.stringify({
        sender_email: state.senderEmail,
        message,
        attachments,
      }),
    });
    el.composer.value = "";
    state.attachments = [];
    renderAttachments();
    el.sendState.textContent = "Sent";
    await loadMessages();
  } catch (error) {
    el.sendState.textContent = error.message;
  } finally {
    el.sendButton.disabled = false;
  }
}

function threadText(limit = 18) {
  return state.messages.slice(0, limit).reverse().map((message) => {
    const sender = message.sender_name || message.sender_email || message.direction || "unknown";
    const text = trim(message.body_text || message.text || "", 260);
    const attachments = attachmentRows(message).map((attachment) => basename(attachment.transfer_name || attachment.filename || "attachment"));
    const suffix = attachments.length ? ` [attachments: ${attachments.join(", ")}]` : "";
    return `${formatTime(message.message_timestamp || message.timestamp)} | ${sender}: ${text}${suffix}`;
  }).join("\n");
}

function buildCodexPrompt() {
  const title = state.selected?.display_name || "No conversation selected";
  const draft = el.composer.value.trim() || "(no draft yet)";
  const attachmentNames = state.attachments.map((file) => file.name).join(", ") || "none";
  const prompt = [
    "Help me respond to this iMessage conversation.",
    "",
    `Conversation: ${title}`,
    `Attachments I plan to send: ${attachmentNames}`,
    "",
    "Recent messages:",
    threadText(),
    "",
    "My draft:",
    draft,
    "",
    "Please suggest a concise reply, flag any ambiguity, and preserve my tone.",
  ].join("\n");
  el.codexPrompt.value = prompt;
  el.codexCount.textContent = `${Math.min(state.messages.length, 18)} msgs`;
  return prompt;
}

async function copyText(value) {
  await navigator.clipboard.writeText(value);
}

function addFiles(fileList) {
  for (const file of Array.from(fileList || [])) {
    state.attachments.push(file);
  }
  renderAttachments();
  buildCodexPrompt();
}

el.refreshButton.addEventListener("click", () => {
  loadStatus();
  loadConversations();
});
el.conversationSearch.addEventListener("input", renderConversations);
el.messageFilter.addEventListener("input", renderMessages);
el.sendButton.addEventListener("click", sendMessage);
el.clearButton.addEventListener("click", () => {
  el.composer.value = "";
  state.attachments = [];
  renderAttachments();
  buildCodexPrompt();
});
el.syncButton.addEventListener("click", async () => {
  el.sendState.textContent = "Sync requested";
  try {
    await api("/penguin-connect/conversations/sync", {
      method: "POST",
      body: JSON.stringify({ mode: "incremental", days: 7 }),
    });
    await loadConversations();
    await loadMessages();
  } catch (error) {
    el.sendState.textContent = error.message;
  }
});
el.copyThreadButton.addEventListener("click", async () => {
  await copyText(threadText(40));
  el.sendState.textContent = "Thread copied";
});
el.fileInput.addEventListener("change", (event) => addFiles(event.target.files));
el.attachmentDrop.addEventListener("dragover", (event) => {
  event.preventDefault();
  el.attachmentDrop.classList.add("dragging");
});
el.attachmentDrop.addEventListener("dragleave", () => el.attachmentDrop.classList.remove("dragging"));
el.attachmentDrop.addEventListener("drop", (event) => {
  event.preventDefault();
  el.attachmentDrop.classList.remove("dragging");
  addFiles(event.dataTransfer.files);
});
el.composer.addEventListener("input", buildCodexPrompt);
el.buildPromptButton.addEventListener("click", buildCodexPrompt);
el.copyPromptButton.addEventListener("click", async () => {
  await copyText(buildCodexPrompt());
  el.sendState.textContent = "Codex prompt copied";
});

renderEmojiButtons();
renderMessages();
loadStatus();
loadConversations();
