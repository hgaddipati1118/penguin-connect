const state = {
  view: "inbox",
  source: "all",
  conversations: [],
  contacts: [],
  selected: null,
  messages: [],
  attachments: [],
  search: {
    query: "",
    conversations: [],
    contacts: [],
    messages: [],
    loading: false,
    token: 0,
  },
  agent: {
    scope: "thread",
    status: null,
    answer: "",
    busy: false,
  },
};

const el = {
  shell: document.querySelector(".app-shell"),
  navButtons: [...document.querySelectorAll(".nav-button[data-view]")],
  paneTitle: document.querySelector("#paneTitle"),
  globalSearch: document.querySelector("#globalSearch"),
  sourceTabs: document.querySelector("#sourceTabs"),
  listSummary: document.querySelector("#listSummary"),
  conversationList: document.querySelector("#conversationList"),
  peopleList: document.querySelector("#peopleList"),
  searchList: document.querySelector("#searchList"),
  refreshButton: document.querySelector("#refreshButton"),
  syncStatus: document.querySelector("#syncStatus"),
  connectionOrbit: document.querySelector("#connectionOrbit"),
  threadEmpty: document.querySelector("#threadEmpty"),
  threadContent: document.querySelector("#threadContent"),
  threadAvatar: document.querySelector("#threadAvatar"),
  threadTitle: document.querySelector("#threadTitle"),
  threadProvider: document.querySelector("#threadProvider"),
  threadSubtitle: document.querySelector("#threadSubtitle"),
  threadSearchButton: document.querySelector("#threadSearchButton"),
  threadSearchBar: document.querySelector("#threadSearchBar"),
  threadSearch: document.querySelector("#threadSearch"),
  threadSearchCount: document.querySelector("#threadSearchCount"),
  closeThreadSearchButton: document.querySelector("#closeThreadSearchButton"),
  messageList: document.querySelector("#messageList"),
  messageComposer: document.querySelector("#messageComposer"),
  sendButton: document.querySelector("#sendButton"),
  composerStatus: document.querySelector("#composerStatus"),
  attachmentInput: document.querySelector("#attachmentInput"),
  attachmentPreview: document.querySelector("#attachmentPreview"),
  agentPane: document.querySelector("#agentPane"),
  toggleAgentButton: document.querySelector("#toggleAgentButton"),
  closeAgentButton: document.querySelector("#closeAgentButton"),
  threadAgentButton: document.querySelector("#threadAgentButton"),
  agentStatus: document.querySelector("#agentStatus"),
  agentQuestion: document.querySelector("#agentQuestion"),
  agentContextLabel: document.querySelector("#agentContextLabel"),
  askAgentButton: document.querySelector("#askAgentButton"),
  agentQuickActions: document.querySelector("#agentQuickActions"),
  agentWelcome: document.querySelector("#agentWelcome"),
  agentAnswer: document.querySelector("#agentAnswer"),
  agentAnswerContent: document.querySelector("#agentAnswerContent"),
  copyAgentAnswerButton: document.querySelector("#copyAgentAnswerButton"),
  useAgentAnswerButton: document.querySelector("#useAgentAnswerButton"),
  composeButton: document.querySelector("#composeButton"),
  composeDialog: document.querySelector("#composeDialog"),
  composeSearch: document.querySelector("#composeSearch"),
  composeResults: document.querySelector("#composeResults"),
  emptySearchButton: document.querySelector("#emptySearchButton"),
  toastRegion: document.querySelector("#toastRegion"),
};

const quickAgentActions = {
  summary: {
    question: "Summarize this conversation.",
    instruction: "Give me a compact summary, the current state, and anything unresolved.",
  },
  reply: {
    question: "Draft a reply to the latest message.",
    instruction: "Write one natural, concise reply. Do not add facts that are not in the messages.",
  },
  commitments: {
    question: "What commitments and next steps are in these messages?",
    instruction: "List each commitment, who owns it, and any date mentioned. Flag uncertainty.",
  },
};

function apiErrorMessage(payload, response) {
  const detail = payload?.detail;
  if (typeof detail === "string") return detail.replaceAll("_", " ");
  if (typeof payload?.error === "string") return payload.error.replaceAll("_", " ");
  return `Request failed (${response.status})`;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
    ...options,
  });
  let payload = {};
  try {
    payload = await response.json();
  } catch (_error) {
    payload = {};
  }
  if (!response.ok) throw new Error(apiErrorMessage(payload, response));
  return payload;
}

function providerKey(value) {
  const provider = String(value || "").toLowerCase();
  if (provider === "whatsapp") return "whatsapp";
  return "imessage";
}

function providerLabel(value) {
  return providerKey(value) === "whatsapp" ? "WhatsApp" : "iMessage";
}

function normalizedHandle(value) {
  const text = String(value || "").trim().toLowerCase();
  if (!text) return "";
  if (text.includes("@") && !text.endsWith("@s.whatsapp.net") && !text.endsWith("@g.us")) {
    return `email:${text}`;
  }
  const digits = text.replace(/\D+/g, "");
  if (digits.length >= 7) return `phone:${digits.slice(-10)}`;
  return `handle:${text}`;
}

function conversationParticipants(conversation) {
  const participants = Array.isArray(conversation?.participants) ? conversation.participants : [];
  return participants.map((value) => String(value || "").trim()).filter(Boolean);
}

function savedConversationContact(conversation) {
  const contacts = Array.isArray(conversation?.contact_context) ? conversation.contact_context : [];
  return contacts.find((contact) => contact?.is_saved !== false) || contacts[0] || null;
}

function conversationName(conversation) {
  const localTitle = String(conversation?.title || "").trim();
  if (localTitle) return localTitle;
  const contact = savedConversationContact(conversation);
  if (contact?.display_name) return contact.display_name;
  const displayName = String(conversation?.display_name || "").trim();
  if (displayName) return displayName;
  const participants = conversationParticipants(conversation);
  return participants[0] || "Unknown conversation";
}

function conversationTimestamp(conversation) {
  const raw = conversation?.last_message_ts || conversation?.updated_at || conversation?.management_updated_at || "";
  const timestamp = Date.parse(raw);
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function sortedConversations(rows = state.conversations) {
  return [...rows].sort((a, b) => (
    conversationTimestamp(b) - conversationTimestamp(a)
    || conversationName(a).localeCompare(conversationName(b))
  ));
}

function sourceMatches(conversation) {
  return state.source === "all" || providerKey(conversation?.source_provider) === state.source;
}

function visibleConversations() {
  return sortedConversations(state.conversations.filter((conversation) => (
    sourceMatches(conversation)
    && !conversation.is_archived
    && conversation.status !== "disconnected"
    && !conversation.excluded
  )));
}

function initials(value) {
  const parts = String(value || "?").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  return parts.slice(0, 2).map((part) => part[0] || "").join("").toUpperCase();
}

function timeLabel(raw) {
  const date = new Date(raw || "");
  if (Number.isNaN(date.getTime())) return "";
  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  if (sameDay) {
    return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(date);
  }
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) return "Yesterday";
  if (now.getTime() - date.getTime() < 6 * 86400000) {
    return new Intl.DateTimeFormat(undefined, { weekday: "short" }).format(date);
  }
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(date);
}

function fullDateLabel(raw) {
  const date = new Date(raw || "");
  if (Number.isNaN(date.getTime())) return "Unknown date";
  const now = new Date();
  if (date.toDateString() === now.toDateString()) return "Today";
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) return "Yesterday";
  return new Intl.DateTimeFormat(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: date.getFullYear() !== now.getFullYear() ? "numeric" : undefined,
  }).format(date);
}

function truncate(value, limit = 90) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

function basename(value) {
  return String(value || "attachment").split(/[\\/]/).pop() || "attachment";
}

function createIcon(id) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#${id}`);
  svg.append(use);
  return svg;
}

function avatarFor(name, provider, { large = false } = {}) {
  const avatar = document.createElement("span");
  avatar.className = `person-avatar${large ? " large" : ""}`;
  avatar.dataset.provider = provider;
  avatar.textContent = initials(name);
  if (!large) {
    const dot = document.createElement("span");
    dot.className = `avatar-provider ${provider}`;
    avatar.append(dot);
  }
  return avatar;
}

function toast(message, kind = "") {
  const item = document.createElement("div");
  item.className = `toast ${kind}`.trim();
  item.textContent = message;
  el.toastRegion.append(item);
  window.setTimeout(() => item.remove(), 3600);
}

function skeletonRows(target, count = 6) {
  const wrapper = document.createElement("div");
  wrapper.className = "skeleton-list";
  for (let index = 0; index < count; index += 1) {
    const row = document.createElement("div");
    row.className = "skeleton-row";
    row.innerHTML = `
      <span class="skeleton-avatar"></span>
      <span class="skeleton-copy"><span class="skeleton-line"></span><span class="skeleton-line short"></span></span>
    `;
    wrapper.append(row);
  }
  target.replaceChildren(wrapper);
}

function renderView() {
  const searching = Boolean(state.search.query);
  el.paneTitle.textContent = state.view === "people" ? "People" : "Inbox";
  for (const button of el.navButtons) {
    const active = button.dataset.view === state.view;
    button.classList.toggle("active", active);
    button.setAttribute("aria-current", active ? "page" : "false");
  }
  el.conversationList.hidden = searching || state.view !== "inbox";
  el.peopleList.hidden = searching || state.view !== "people";
  el.searchList.hidden = !searching;
  el.sourceTabs.hidden = state.view === "people" && !searching;
  renderConversationList();
  renderPeopleList();
  renderSearchResults();
}

function renderConversationList() {
  const rows = visibleConversations();
  el.conversationList.replaceChildren();
  el.listSummary.textContent = `${rows.length} conversation${rows.length === 1 ? "" : "s"} · latest first`;
  if (!rows.length) {
    const empty = document.createElement("div");
    empty.className = "pane-empty";
    empty.textContent = state.source === "whatsapp"
      ? "No WhatsApp conversations yet. Start the local WhatsApp bridge, then refresh."
      : "No conversations found for this view.";
    el.conversationList.append(empty);
    return;
  }

  for (const conversation of rows) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "conversation-row";
    row.dataset.conversationId = conversation.conversation_id;
    row.setAttribute("role", "listitem");
    const active = conversation.conversation_id === state.selected?.conversation_id;
    row.classList.toggle("active", active);
    row.setAttribute("aria-current", active ? "true" : "false");

    const provider = providerKey(conversation.source_provider);
    const name = conversationName(conversation);
    row.append(avatarFor(name, provider));

    const copy = document.createElement("span");
    copy.className = "conversation-copy";
    const top = document.createElement("span");
    top.className = "conversation-topline";
    const nameNode = document.createElement("span");
    nameNode.className = "conversation-name";
    nameNode.textContent = name;
    const time = document.createElement("time");
    time.className = "conversation-time";
    time.dateTime = conversation.last_message_ts || "";
    time.textContent = timeLabel(conversation.last_message_ts || conversation.updated_at);
    top.append(nameNode, time);

    const preview = document.createElement("span");
    preview.className = "conversation-preview";
    const previewText = document.createElement("span");
    previewText.className = "conversation-preview-text";
    if (conversation.draft_text) {
      const draft = document.createElement("span");
      draft.className = "draft-label";
      draft.textContent = "Draft";
      previewText.append(draft);
    }
    previewText.append(document.createTextNode(
      conversation.last_message_preview
      || (conversation.last_message_has_attachments ? "Attachment" : "No messages cached yet"),
    ));
    preview.append(previewText);
    if (Number(conversation.unread_count || 0) > 0) {
      const unread = document.createElement("span");
      unread.className = "unread-count";
      unread.textContent = Number(conversation.unread_count) > 99 ? "99+" : String(conversation.unread_count);
      preview.append(unread);
    }
    copy.append(top, preview);
    row.append(copy);
    row.addEventListener("click", () => selectConversation(conversation));
    el.conversationList.append(row);
  }
}

function contactHandle(contact) {
  return contact?.primary_handle || contact?.email || contact?.phone || "";
}

function renderPersonRow(contact, target, { closeDialog = false } = {}) {
  const row = document.createElement("button");
  row.type = "button";
  row.className = "people-row";
  row.setAttribute("role", "listitem");
  const name = contact.display_name || contactHandle(contact) || "Unknown person";
  row.append(avatarFor(name, "imessage"));

  const copy = document.createElement("span");
  copy.className = "person-copy";
  const strong = document.createElement("strong");
  strong.textContent = name;
  const handle = document.createElement("span");
  const organization = contact.organization ? `${contact.organization} · ` : "";
  handle.textContent = `${organization}${contactHandle(contact) || "No contact handle"}`;
  copy.append(strong, handle);

  const count = document.createElement("span");
  count.className = "person-thread-count";
  const threads = Number(contact.thread_count || 0);
  count.textContent = threads ? `${threads} thread${threads === 1 ? "" : "s"}` : "No thread";
  row.append(copy, count);
  row.addEventListener("click", () => {
    const match = conversationsForContact(contact)[0];
    if (match) {
      if (closeDialog) el.composeDialog.close();
      state.view = "inbox";
      state.search.query = "";
      el.globalSearch.value = "";
      selectConversation(match);
      renderView();
      return;
    }
    toast("No existing safe conversation route for this contact.", "error");
  });
  target.append(row);
}

function renderPeopleList() {
  if (state.view !== "people" || state.search.query) return;
  el.peopleList.replaceChildren();
  el.listSummary.textContent = `${state.contacts.length} people from Contacts and conversations`;
  if (!state.contacts.length) {
    const empty = document.createElement("div");
    empty.className = "pane-empty";
    empty.textContent = "No contacts loaded. Refresh Contacts from the power console if this looks incomplete.";
    el.peopleList.append(empty);
    return;
  }
  for (const contact of state.contacts) renderPersonRow(contact, el.peopleList);
}

function conversationsForContact(contact) {
  const keys = new Set(
    [
      ...(Array.isArray(contact?.contact_keys) ? contact.contact_keys : []),
      contact?.contact_key,
      contactHandle(contact),
      contact?.phone,
      contact?.phone_normalized,
      contact?.email,
    ]
      .map(normalizedHandle)
      .filter(Boolean),
  );
  return sortedConversations(state.conversations.filter((conversation) => (
    conversationParticipants(conversation).some((participant) => keys.has(normalizedHandle(participant)))
    || (Array.isArray(conversation.contact_context)
      && conversation.contact_context.some((item) => {
        const candidates = [item.contact_key, ...(item.contact_keys || []), item.primary_handle];
        return candidates.some((value) => keys.has(normalizedHandle(value)));
      }))
  )));
}

function searchConversationRows(query) {
  const needle = query.trim().toLowerCase();
  if (!needle) return [];
  return sortedConversations(state.conversations.filter((conversation) => {
    const contacts = Array.isArray(conversation.contact_context) ? conversation.contact_context : [];
    const haystack = [
      conversationName(conversation),
      conversation.display_name,
      conversation.last_message_preview,
      conversation.source_provider,
      conversation.source_service_name,
      ...conversationParticipants(conversation),
      ...contacts.flatMap((contact) => [contact.display_name, contact.organization, contact.primary_handle]),
    ].join(" ").toLowerCase();
    return haystack.includes(needle) && sourceMatches(conversation);
  }));
}

function renderSearchResults() {
  if (!state.search.query) return;
  el.searchList.replaceChildren();
  const total = state.search.conversations.length + state.search.contacts.length + state.search.messages.length;
  el.listSummary.textContent = state.search.loading
    ? "Searching local messages"
    : `${total} result${total === 1 ? "" : "s"}`;

  if (state.search.loading && !total) {
    skeletonRows(el.searchList, 5);
    return;
  }

  if (state.search.conversations.length) {
    appendSearchTitle("Conversations", state.search.conversations.length);
    for (const conversation of state.search.conversations.slice(0, 8)) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "conversation-row";
      const provider = providerKey(conversation.source_provider);
      const name = conversationName(conversation);
      row.append(avatarFor(name, provider));
      const copy = document.createElement("span");
      copy.className = "conversation-copy";
      copy.innerHTML = `<span class="conversation-topline"><span class="conversation-name"></span><time class="conversation-time"></time></span><span class="conversation-preview"><span class="conversation-preview-text"></span></span>`;
      copy.querySelector(".conversation-name").textContent = name;
      copy.querySelector(".conversation-time").textContent = timeLabel(conversation.last_message_ts);
      copy.querySelector(".conversation-preview-text").textContent = truncate(conversation.last_message_preview || providerLabel(provider), 82);
      row.append(copy);
      row.addEventListener("click", () => openSearchConversation(conversation));
      el.searchList.append(row);
    }
  }

  if (state.search.contacts.length) {
    appendSearchTitle("People", state.search.contacts.length);
    for (const contact of state.search.contacts.slice(0, 6)) renderPersonRow(contact, el.searchList);
  }

  if (state.search.messages.length) {
    appendSearchTitle("Messages", state.search.messages.length);
    for (const message of state.search.messages.slice(0, 16)) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "search-message-row";
      const copy = document.createElement("span");
      copy.className = "search-message-copy";
      const title = document.createElement("strong");
      const conversation = state.conversations.find((item) => item.conversation_id === message.conversation_id);
      title.textContent = conversation ? conversationName(conversation) : (message.title || message.display_name || "Conversation");
      const snippet = document.createElement("span");
      snippet.textContent = `${message.sender_name || message.sender_email || "Message"}: ${truncate(message.body_text, 92)}`;
      copy.append(title, snippet);
      const time = document.createElement("time");
      time.dateTime = message.message_timestamp || "";
      time.textContent = timeLabel(message.message_timestamp);
      row.append(copy, time);
      row.addEventListener("click", () => {
        const match = state.conversations.find((item) => item.conversation_id === message.conversation_id);
        if (match) openSearchConversation(match, message.provider_message_id);
      });
      el.searchList.append(row);
    }
  }

  if (!total && !state.search.loading) {
    const empty = document.createElement("div");
    empty.className = "pane-empty";
    empty.textContent = `Nothing matched “${state.search.query}”. Try a name, phrase, phone number, or email.`;
    el.searchList.append(empty);
  }
}

function appendSearchTitle(label, count) {
  const title = document.createElement("div");
  title.className = "search-section-title";
  const name = document.createElement("span");
  name.textContent = label;
  const value = document.createElement("span");
  value.textContent = String(count);
  title.append(name, value);
  el.searchList.append(title);
}

function openSearchConversation(conversation, messageId = "") {
  state.search.query = "";
  el.globalSearch.value = "";
  state.view = "inbox";
  renderView();
  selectConversation(conversation, { focusMessageId: messageId });
}

function renderThreadHeader() {
  const conversation = state.selected;
  if (!conversation) {
    el.threadEmpty.hidden = false;
    el.threadContent.hidden = true;
    el.agentContextLabel.textContent = "No chat selected";
    updateAgentButton();
    return;
  }
  el.threadEmpty.hidden = true;
  el.threadContent.hidden = false;
  const name = conversationName(conversation);
  const provider = providerKey(conversation.source_provider);
  el.threadAvatar.replaceWith(avatarFor(name, provider, { large: true }));
  el.threadAvatar = document.querySelector(".thread-header .person-avatar");
  el.threadAvatar.id = "threadAvatar";
  el.threadTitle.textContent = name;
  el.threadProvider.className = `provider-pill ${provider}`;
  el.threadProvider.textContent = providerLabel(provider);
  const participants = conversationParticipants(conversation);
  const contact = savedConversationContact(conversation);
  el.threadSubtitle.textContent = [
    contact?.organization || "",
    conversation.chat_type === "group" ? `${participants.length} participants` : participants[0] || "",
    conversation.status !== "active" ? conversation.status : "",
  ].filter(Boolean).join(" · ") || "Local conversation";
  el.agentContextLabel.textContent = state.agent.scope === "thread"
    ? `${providerLabel(provider)} · ${name}`
    : "Searches the local message cache";
  updateAgentButton();
}

function isOwnMessage(message) {
  return Boolean(message?.metadata?.is_from_me)
    || message?.sender_name === "Me"
    || ["manual_to_imessage", "email_to_imessage"].includes(String(message?.direction || ""));
}

function messageAttachments(message) {
  if (Array.isArray(message?.attachments)) return message.attachments;
  return Array.isArray(message?.metadata?.attachments) ? message.metadata.attachments : [];
}

function renderMessages({ focusMessageId = "" } = {}) {
  el.messageList.replaceChildren();
  if (!state.messages.length) {
    const empty = document.createElement("div");
    empty.className = "pane-empty";
    empty.textContent = "No messages are cached for this conversation yet.";
    el.messageList.append(empty);
    return;
  }

  let lastDate = "";
  const rows = [...state.messages].sort((a, b) => (
    Date.parse(a.message_timestamp || "") - Date.parse(b.message_timestamp || "")
  ));
  for (const message of rows) {
    const date = fullDateLabel(message.message_timestamp);
    if (date !== lastDate) {
      const divider = document.createElement("div");
      divider.className = "date-divider";
      divider.textContent = date;
      el.messageList.append(divider);
      lastDate = date;
    }

    const mine = isOwnMessage(message);
    const row = document.createElement("article");
    row.className = `message-row${mine ? " mine" : ""}`;
    row.dataset.messageId = message.provider_message_id || "";
    row.dataset.searchText = [
      message.body_text,
      message.sender_name,
      message.sender_email,
      ...messageAttachments(message).map((attachment) => attachment.filename || attachment.transfer_name),
    ].join(" ").toLowerCase();

    const stack = document.createElement("div");
    stack.className = "message-stack";
    if (!mine && message.sender_name && state.selected?.chat_type === "group") {
      const sender = document.createElement("p");
      sender.className = "message-sender";
      sender.textContent = message.sender_name;
      stack.append(sender);
    }

    const bubble = document.createElement("div");
    bubble.className = "message-bubble";
    const body = document.createElement("span");
    body.textContent = message.body_text || (messageAttachments(message).length ? "" : "Empty message");
    bubble.append(body);
    const attachments = messageAttachments(message);
    if (attachments.length) {
      const list = document.createElement("div");
      list.className = "message-attachments";
      for (const attachment of attachments) {
        const item = document.createElement("span");
        item.className = "message-attachment";
        item.append(createIcon("i-paperclip"));
        const label = document.createElement("span");
        label.textContent = basename(attachment.transfer_name || attachment.filename || attachment.mime_type);
        item.append(label);
        list.append(item);
      }
      bubble.append(list);
    }
    const meta = document.createElement("div");
    meta.className = "message-meta";
    const time = document.createElement("time");
    time.dateTime = message.message_timestamp || "";
    time.textContent = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(
      new Date(message.message_timestamp || Date.now()),
    );
    meta.append(time);
    if (!message.is_read && !mine) {
      const unread = document.createElement("span");
      unread.textContent = "Unread";
      meta.append(unread);
    }
    stack.append(bubble, meta);
    row.append(stack);
    el.messageList.append(row);
  }

  applyThreadSearch();
  const focused = focusMessageId
    ? el.messageList.querySelector(`[data-message-id="${CSS.escape(focusMessageId)}"]`)
    : null;
  (focused || el.messageList.lastElementChild)?.scrollIntoView({ block: focused ? "center" : "end" });
}

async function selectConversation(conversation, { focusMessageId = "" } = {}) {
  state.selected = conversation;
  state.messages = [];
  el.shell.classList.add("thread-open");
  renderConversationList();
  renderThreadHeader();
  el.messageList.innerHTML = `<div class="message-loading"><span></span><span></span><span></span><span></span></div>`;
  try {
    const payload = await api(
      `/penguin-connect/conversations/${encodeURIComponent(conversation.conversation_id)}/messages?limit=300`,
    );
    if (state.selected?.conversation_id !== conversation.conversation_id) return;
    state.messages = payload.messages || [];
    renderMessages({ focusMessageId });
    await markConversationRead(conversation);
  } catch (error) {
    el.messageList.innerHTML = `<div class="pane-empty"></div>`;
    el.messageList.firstElementChild.textContent = error.message;
  }
}

async function markConversationRead(conversation) {
  if (!Number(conversation?.unread_count || 0)) return;
  try {
    await api(`/penguin-connect/conversations/${encodeURIComponent(conversation.conversation_id)}/read-state`, {
      method: "POST",
      body: JSON.stringify({ unread: false }),
    });
    conversation.unread_count = 0;
    conversation.has_unread = false;
    renderConversationList();
  } catch (_error) {
    // Reading the thread still succeeds if the optional local read-state update fails.
  }
}

function applyThreadSearch() {
  const query = el.threadSearch.value.trim().toLowerCase();
  const rows = [...el.messageList.querySelectorAll(".message-row")];
  let matches = 0;
  for (const row of rows) {
    const visible = !query || row.dataset.searchText.includes(query);
    row.classList.toggle("search-hidden", !visible);
    if (visible && query) matches += 1;
  }
  el.threadSearchCount.textContent = query ? `${matches} match${matches === 1 ? "" : "es"}` : "";
}

function showThreadSearch() {
  el.threadSearchBar.hidden = false;
  el.threadSearch.focus();
}

function closeThreadSearch() {
  el.threadSearchBar.hidden = true;
  el.threadSearch.value = "";
  applyThreadSearch();
}

function resizeComposer() {
  el.messageComposer.style.height = "auto";
  el.messageComposer.style.height = `${Math.min(el.messageComposer.scrollHeight, 140)}px`;
}

function updateSendButton() {
  const hasContent = Boolean(el.messageComposer.value.trim() || state.attachments.length);
  el.sendButton.disabled = !state.selected || !hasContent;
}

function renderAttachmentPreview() {
  el.attachmentPreview.replaceChildren();
  el.attachmentPreview.hidden = !state.attachments.length;
  for (const [index, file] of state.attachments.entries()) {
    const item = document.createElement("span");
    item.className = "pending-attachment";
    item.append(createIcon("i-paperclip"));
    const name = document.createElement("span");
    name.textContent = file.name;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.title = `Remove ${file.name}`;
    remove.setAttribute("aria-label", `Remove ${file.name}`);
    remove.append(createIcon("i-close"));
    remove.addEventListener("click", () => {
      state.attachments.splice(index, 1);
      renderAttachmentPreview();
      updateSendButton();
    });
    item.append(name, remove);
    el.attachmentPreview.append(item);
  }
}

function fileAsAttachment(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error(`Could not read ${file.name}`));
    reader.onload = () => {
      const encoded = String(reader.result || "").split(",").pop() || "";
      resolve({
        filename: file.name,
        mime_type: file.type || "application/octet-stream",
        data_base64: encoded,
        size: file.size,
      });
    };
    reader.readAsDataURL(file);
  });
}

async function sendMessage() {
  if (!state.selected || el.sendButton.disabled) return;
  const conversation = state.selected;
  const text = el.messageComposer.value.trim();
  const files = [...state.attachments];
  el.sendButton.disabled = true;
  el.composerStatus.textContent = `Sending through ${providerLabel(conversation.source_provider)}…`;
  try {
    const attachments = await Promise.all(files.map(fileAsAttachment));
    await api(`/penguin-connect/conversations/${encodeURIComponent(conversation.conversation_id)}/send`, {
      method: "POST",
      body: JSON.stringify({
        sender_email: "",
        message: text,
        attachments,
      }),
    });
    el.messageComposer.value = "";
    state.attachments = [];
    renderAttachmentPreview();
    resizeComposer();
    el.composerStatus.textContent = `Sent through ${providerLabel(conversation.source_provider)}`;
    await Promise.all([
      loadConversations({ keepSelection: true }),
      selectConversation(conversation),
    ]);
  } catch (error) {
    el.composerStatus.textContent = error.message;
    toast(`Could not send: ${error.message}`, "error");
  } finally {
    updateSendButton();
  }
}

async function loadConversations({ keepSelection = true } = {}) {
  if (!state.conversations.length) skeletonRows(el.conversationList, 7);
  try {
    const payload = await api("/penguin-connect/conversations?include_whatsapp=true");
    state.conversations = payload.conversations || [];
    if (keepSelection && state.selected) {
      state.selected = state.conversations.find(
        (conversation) => conversation.conversation_id === state.selected.conversation_id,
      ) || state.selected;
    }
    renderView();
    renderThreadHeader();
  } catch (error) {
    el.conversationList.replaceChildren();
    const empty = document.createElement("div");
    empty.className = "pane-empty";
    empty.textContent = `Local bridge unavailable: ${error.message}`;
    el.conversationList.append(empty);
    el.listSummary.textContent = "Bridge unavailable";
    throw error;
  }
}

async function loadContacts(query = "") {
  if (state.view === "people" && !state.contacts.length && !query) skeletonRows(el.peopleList, 7);
  const payload = await api(
    `/penguin-connect/contacts?search=${encodeURIComponent(query)}&limit=100&source=all`,
  );
  if (!query) state.contacts = payload.contacts || [];
  return payload.contacts || [];
}

async function loadHealth() {
  try {
    const health = await api("/penguin-connect/health");
    el.connectionOrbit.className = "connection-orbit online";
    el.connectionOrbit.title = "Local bridge connected";
    el.syncStatus.innerHTML = `<span class="status-dot online"></span> Local bridge · ${health.conversations?.active || 0} active`;
  } catch (_error) {
    el.connectionOrbit.className = "connection-orbit offline";
    el.connectionOrbit.title = "Local bridge unavailable";
    el.syncStatus.innerHTML = `<span class="status-dot"></span> Bridge offline`;
  }
}

async function runSearch(query) {
  const clean = query.trim();
  state.search.query = clean;
  state.search.conversations = searchConversationRows(clean);
  state.search.contacts = [];
  state.search.messages = [];
  state.search.loading = Boolean(clean);
  const token = state.search.token + 1;
  state.search.token = token;
  renderView();
  if (!clean) return;

  try {
    const calls = [loadContacts(clean)];
    if (clean.length >= 2) {
      calls.push(api(`/penguin-connect/messages/search?query=${encodeURIComponent(clean)}&limit=50&view=all`));
    } else {
      calls.push(Promise.resolve({ messages: [] }));
    }
    const [contacts, messages] = await Promise.all(calls);
    if (state.search.token !== token) return;
    state.search.contacts = contacts;
    state.search.messages = (messages.messages || []).filter((message) => (
      state.source === "all" || providerKey(message.source_provider) === state.source
    ));
  } catch (error) {
    if (state.search.token === token) toast(`Search problem: ${error.message}`, "error");
  } finally {
    if (state.search.token === token) {
      state.search.loading = false;
      renderSearchResults();
    }
  }
}

let searchTimer = 0;

function scheduleSearch() {
  window.clearTimeout(searchTimer);
  const query = el.globalSearch.value;
  if (!query.trim()) {
    runSearch("");
    return;
  }
  state.search.query = query.trim();
  state.search.conversations = searchConversationRows(query);
  state.search.loading = true;
  renderView();
  searchTimer = window.setTimeout(() => runSearch(query), 280);
}

function setView(view) {
  state.view = view === "people" ? "people" : "inbox";
  if (state.view === "people") {
    state.source = "all";
    for (const button of el.sourceTabs.querySelectorAll("button[data-source]")) {
      const active = button.dataset.source === "all";
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    }
  }
  if (state.view === "people" && !state.contacts.length) {
    loadContacts().then(renderPeopleList).catch((error) => toast(error.message, "error"));
  }
  renderView();
}

function setSource(source) {
  state.source = ["all", "imessage", "whatsapp"].includes(source) ? source : "all";
  for (const button of el.sourceTabs.querySelectorAll("button[data-source]")) {
    const active = button.dataset.source === state.source;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  }
  if (state.search.query) runSearch(state.search.query);
  else renderView();
}

function setAgentOpen(open) {
  el.shell.classList.toggle("agent-closed", !open);
  el.toggleAgentButton.setAttribute("aria-pressed", open ? "true" : "false");
  if (open) el.agentQuestion.focus();
}

function setAgentScope(scope) {
  state.agent.scope = scope === "inbox" ? "inbox" : "thread";
  for (const button of document.querySelectorAll("[data-agent-scope]")) {
    const active = button.dataset.agentScope === state.agent.scope;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  }
  renderThreadHeader();
}

function updateAgentButton() {
  const hasQuestion = Boolean(el.agentQuestion.value.trim());
  const hasContext = state.agent.scope === "inbox" || Boolean(state.selected);
  const unavailable = state.agent.status && !state.agent.status.ask_enabled;
  el.askAgentButton.disabled = state.agent.busy || unavailable || !hasQuestion || !hasContext;
}

function agentThreadText(limit = 30) {
  return [...state.messages]
    .sort((a, b) => Date.parse(a.message_timestamp || "") - Date.parse(b.message_timestamp || ""))
    .slice(-limit)
    .map((message) => {
      const sender = isOwnMessage(message) ? "Me" : (message.sender_name || message.sender_email || "Unknown");
      const attachments = messageAttachments(message).map((item) => basename(item.transfer_name || item.filename));
      const suffix = attachments.length ? ` [attachments: ${attachments.join(", ")}]` : "";
      return `${message.message_timestamp || ""} | ${sender}: ${truncate(message.body_text, 700)}${suffix}`;
    })
    .join("\n");
}

function selectedConversationPromptContext() {
  if (!state.selected) return "No conversation selected.";
  return [
    `Conversation: ${conversationName(state.selected)}`,
    `Provider: ${providerLabel(state.selected.source_provider)}`,
    `Type: ${state.selected.chat_type || "direct"}`,
    `Participants: ${conversationParticipants(state.selected).join(", ") || "unknown"}`,
    `Latest activity: ${state.selected.last_message_ts || "unknown"}`,
  ].join("\n");
}

async function inboxAgentContext(query) {
  const payload = await api(
    `/penguin-connect/messages/search?query=${encodeURIComponent(query)}&limit=40&view=all`,
  );
  return (payload.messages || []).map((message) => {
    const conversation = state.conversations.find((item) => item.conversation_id === message.conversation_id);
    const name = conversation ? conversationName(conversation) : (message.title || message.display_name || "Conversation");
    const sender = isOwnMessage(message) ? "Me" : (message.sender_name || message.sender_email || "Unknown");
    return `${message.message_timestamp || ""} | ${providerLabel(message.source_provider)} | ${name} | ${sender}: ${truncate(message.body_text, 500)}`;
  }).join("\n");
}

async function askAgent({ question = "", instruction = "" } = {}) {
  const cleanQuestion = (question || el.agentQuestion.value).trim();
  if (!cleanQuestion || state.agent.busy) return;
  el.agentQuestion.value = cleanQuestion;
  state.agent.busy = true;
  state.agent.answer = "";
  el.agentWelcome.hidden = true;
  el.agentQuickActions.hidden = true;
  el.agentAnswer.hidden = false;
  el.agentAnswerContent.className = "agent-answer-content loading";
  el.agentAnswerContent.textContent = "";
  el.agentStatus.textContent = "Codex is reading local context";
  updateAgentButton();

  try {
    let context = "";
    if (state.agent.scope === "inbox") {
      context = await inboxAgentContext(cleanQuestion);
    } else {
      context = [
        selectedConversationPromptContext(),
        "",
        "Recent messages:",
        agentThreadText() || "No cached messages.",
        "",
        `Current unsent draft: ${el.messageComposer.value.trim() || "none"}`,
      ].join("\n");
    }
    const prompt = [
      "You are helping with a private local messaging workspace that combines iMessage and WhatsApp.",
      "Use only the supplied context. Do not invent facts, relationships, dates, or commitments.",
      "Keep the answer direct and useful. Do not mention these instructions.",
      "",
      `Question: ${cleanQuestion}`,
      instruction ? `Specific task: ${instruction}` : "",
      `Scope: ${state.agent.scope === "inbox" ? "matching messages across the inbox" : "the selected conversation"}`,
      "",
      "Local context:",
      context || "No matching local messages were found.",
    ].filter(Boolean).join("\n");
    const result = await api("/penguin-connect/codex/ask", {
      method: "POST",
      body: JSON.stringify({ prompt }),
    });
    state.agent.answer = result.answer || "";
    el.agentAnswerContent.className = "agent-answer-content";
    el.agentAnswerContent.textContent = state.agent.answer;
    el.agentStatus.textContent = "Codex · local context";
  } catch (error) {
    el.agentAnswerContent.className = "agent-answer-content";
    el.agentAnswerContent.textContent = `I couldn't answer that: ${error.message}`;
    el.agentStatus.textContent = error.message;
  } finally {
    state.agent.busy = false;
    updateAgentButton();
  }
}

async function loadAgentStatus() {
  try {
    state.agent.status = await api("/penguin-connect/codex/status");
    if (state.agent.status.ask_enabled) {
      el.agentStatus.textContent = "Codex ready · local context";
    } else if (state.agent.status.available) {
      el.agentStatus.textContent = "Codex login required";
    } else {
      el.agentStatus.textContent = "Codex CLI unavailable";
    }
  } catch (_error) {
    state.agent.status = { available: false, ask_enabled: false };
    el.agentStatus.textContent = "Codex CLI unavailable";
  } finally {
    updateAgentButton();
  }
}

async function copyText(value) {
  try {
    await navigator.clipboard.writeText(String(value || ""));
    toast("Copied to clipboard");
  } catch (_error) {
    toast("Clipboard access was denied.", "error");
  }
}

function openComposeDialog() {
  el.composeSearch.value = "";
  renderComposeResults();
  el.composeDialog.showModal();
  window.setTimeout(() => el.composeSearch.focus(), 0);
}

function renderComposeResults() {
  const query = el.composeSearch.value.trim().toLowerCase();
  el.composeResults.replaceChildren();
  const conversations = visibleConversations().filter((conversation) => (
    !query
    || [
      conversationName(conversation),
      conversation.last_message_preview,
      ...conversationParticipants(conversation),
    ].join(" ").toLowerCase().includes(query)
  )).slice(0, 8);
  const contacts = state.contacts.filter((contact) => (
    !query
    || [contact.display_name, contactHandle(contact), contact.organization].join(" ").toLowerCase().includes(query)
  )).slice(0, 8);

  if (conversations.length) {
    const title = document.createElement("div");
    title.className = "search-section-title";
    title.textContent = "Existing conversations";
    el.composeResults.append(title);
    for (const conversation of conversations) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "conversation-row";
      const name = conversationName(conversation);
      row.append(avatarFor(name, providerKey(conversation.source_provider)));
      const copy = document.createElement("span");
      copy.className = "conversation-copy";
      const top = document.createElement("span");
      top.className = "conversation-topline";
      const titleNode = document.createElement("span");
      titleNode.className = "conversation-name";
      titleNode.textContent = name;
      top.append(titleNode);
      const preview = document.createElement("span");
      preview.className = "conversation-preview";
      preview.textContent = providerLabel(conversation.source_provider);
      copy.append(top, preview);
      row.append(copy);
      row.addEventListener("click", () => {
        el.composeDialog.close();
        selectConversation(conversation);
        window.setTimeout(() => el.messageComposer.focus(), 0);
      });
      el.composeResults.append(row);
    }
  }

  if (contacts.length) {
    const title = document.createElement("div");
    title.className = "search-section-title";
    title.textContent = "People";
    el.composeResults.append(title);
    for (const contact of contacts) renderPersonRow(contact, el.composeResults, { closeDialog: true });
  }

  if (!conversations.length && !contacts.length) {
    const empty = document.createElement("div");
    empty.className = "pane-empty";
    empty.textContent = "No contact or safe existing conversation matched.";
    el.composeResults.append(empty);
  }
}

async function refreshAll() {
  el.refreshButton.disabled = true;
  el.listSummary.textContent = "Refreshing local sources";
  try {
    await Promise.all([
      loadConversations({ keepSelection: true }),
      loadContacts().then((contacts) => { state.contacts = contacts; }),
      loadHealth(),
    ]);
    if (state.selected) await selectConversation(state.selected);
    toast("Inbox refreshed");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    el.refreshButton.disabled = false;
    renderView();
  }
}

for (const button of el.navButtons) {
  button.addEventListener("click", () => setView(button.dataset.view));
}

el.sourceTabs.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-source]");
  if (button) setSource(button.dataset.source);
});

el.globalSearch.addEventListener("input", scheduleSearch);
el.globalSearch.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    el.globalSearch.value = "";
    runSearch("");
    el.globalSearch.blur();
  }
});
el.emptySearchButton.addEventListener("click", () => el.globalSearch.focus());
el.refreshButton.addEventListener("click", refreshAll);
el.threadSearchButton.addEventListener("click", showThreadSearch);
el.closeThreadSearchButton.addEventListener("click", closeThreadSearch);
el.threadSearch.addEventListener("input", applyThreadSearch);
el.threadAgentButton.addEventListener("click", () => setAgentOpen(true));
el.toggleAgentButton.addEventListener("click", () => {
  setAgentOpen(el.shell.classList.contains("agent-closed"));
});
el.closeAgentButton.addEventListener("click", () => setAgentOpen(false));

el.messageComposer.addEventListener("input", () => {
  resizeComposer();
  updateSendButton();
});
el.messageComposer.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    event.preventDefault();
    sendMessage();
  }
});
el.sendButton.addEventListener("click", sendMessage);
el.attachmentInput.addEventListener("change", () => {
  state.attachments.push(...el.attachmentInput.files);
  el.attachmentInput.value = "";
  renderAttachmentPreview();
  updateSendButton();
});

for (const button of document.querySelectorAll("[data-agent-scope]")) {
  button.addEventListener("click", () => setAgentScope(button.dataset.agentScope));
}
el.agentQuestion.addEventListener("input", updateAgentButton);
el.agentQuestion.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    event.preventDefault();
    askAgent();
  }
});
el.askAgentButton.addEventListener("click", () => askAgent());
el.agentQuickActions.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-agent-action]");
  if (!button) return;
  const action = quickAgentActions[button.dataset.agentAction];
  if (!action) return;
  el.agentQuestion.value = action.question;
  updateAgentButton();
  askAgent(action);
});
el.copyAgentAnswerButton.addEventListener("click", () => copyText(state.agent.answer));
el.useAgentAnswerButton.addEventListener("click", () => {
  if (!state.agent.answer || !state.selected) return;
  el.messageComposer.value = state.agent.answer;
  resizeComposer();
  updateSendButton();
  el.messageComposer.focus();
  toast("Agent response moved to your draft");
});

el.composeButton.addEventListener("click", openComposeDialog);
el.composeSearch.addEventListener("input", renderComposeResults);
el.composeDialog.addEventListener("click", (event) => {
  if (event.target === el.composeDialog) el.composeDialog.close();
});

document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    el.globalSearch.focus();
    el.globalSearch.select();
  }
  if (event.key === "Escape" && window.innerWidth <= 800 && el.shell.classList.contains("thread-open")) {
    el.shell.classList.remove("thread-open");
  }
});

document.querySelector(".thread-header").addEventListener("click", (event) => {
  if (window.innerWidth <= 800 && event.clientX < 115) {
    el.shell.classList.remove("thread-open");
  }
});

async function start() {
  setAgentOpen(window.innerWidth > 1180);
  setSource("all");
  renderView();
  await Promise.allSettled([
    loadConversations({ keepSelection: false }),
    loadContacts().then((contacts) => { state.contacts = contacts; renderPeopleList(); }),
    loadHealth(),
    loadAgentStatus(),
  ]);
}

start();

window.setInterval(() => {
  if (document.visibilityState !== "visible") return;
  loadConversations({ keepSelection: true }).catch(() => {});
  loadHealth();
}, 30000);
