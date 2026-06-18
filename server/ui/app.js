const state = {
  conversations: [],
  selected: null,
  messages: [],
  replyContext: null,
  senderEmail: "",
  attachments: [],
  contacts: [],
  contactSearchTimer: null,
  messageSearchResults: [],
  messageSearchTimer: null,
  focusMessageId: "",
  messageView: "all",
  conversationView: "inbox",
  conversationLabel: "",
  selectedConversationIds: new Set(),
  bulkBusy: false,
  bulkMessage: "",
  draftSaveTimer: null,
  codexMode: "reply",
};

const el = {
  statusLine: document.querySelector("#statusLine"),
  refreshButton: document.querySelector("#refreshButton"),
  conversationSearch: document.querySelector("#conversationSearch"),
  conversationFilters: document.querySelector("#conversationFilters"),
  labelFilters: document.querySelector("#labelFilters"),
  bulkActions: document.querySelector("#bulkActions"),
  bulkState: document.querySelector("#bulkState"),
  selectVisibleButton: document.querySelector("#selectVisibleButton"),
  bulkMarkReadButton: document.querySelector("#bulkMarkReadButton"),
  bulkArchiveButton: document.querySelector("#bulkArchiveButton"),
  clearSelectionButton: document.querySelector("#clearSelectionButton"),
  conversationList: document.querySelector("#conversationList"),
  contactRefreshButton: document.querySelector("#contactRefreshButton"),
  contactSearch: document.querySelector("#contactSearch"),
  contactStatus: document.querySelector("#contactStatus"),
  contactList: document.querySelector("#contactList"),
  threadProvider: document.querySelector("#threadProvider"),
  threadTitle: document.querySelector("#threadTitle"),
  syncButton: document.querySelector("#syncButton"),
  pinButton: document.querySelector("#pinButton"),
  archiveButton: document.querySelector("#archiveButton"),
  markReadButton: document.querySelector("#markReadButton"),
  markUnreadButton: document.querySelector("#markUnreadButton"),
  connectionButton: document.querySelector("#connectionButton"),
  copyThreadButton: document.querySelector("#copyThreadButton"),
  threadStatus: document.querySelector("#threadStatus"),
  threadPeopleState: document.querySelector("#threadPeopleState"),
  threadPeople: document.querySelector("#threadPeople"),
  threadTags: document.querySelector("#threadTags"),
  threadNote: document.querySelector("#threadNote"),
  saveManagementButton: document.querySelector("#saveManagementButton"),
  managementState: document.querySelector("#managementState"),
  globalMessageSearch: document.querySelector("#globalMessageSearch"),
  messageSearchStatus: document.querySelector("#messageSearchStatus"),
  messageSearchResults: document.querySelector("#messageSearchResults"),
  messageViewFilters: document.querySelector("#messageViewFilters"),
  messageFilter: document.querySelector("#messageFilter"),
  messageList: document.querySelector("#messageList"),
  senderBadge: document.querySelector("#senderBadge"),
  replyContext: document.querySelector("#replyContext"),
  replyContextText: document.querySelector("#replyContextText"),
  clearReplyContextButton: document.querySelector("#clearReplyContextButton"),
  composer: document.querySelector("#composer"),
  emojiRow: document.querySelector("#emojiRow"),
  attachmentDrop: document.querySelector("#attachmentDrop"),
  fileInput: document.querySelector("#fileInput"),
  attachmentList: document.querySelector("#attachmentList"),
  sendButton: document.querySelector("#sendButton"),
  clearButton: document.querySelector("#clearButton"),
  sendState: document.querySelector("#sendState"),
  draftState: document.querySelector("#draftState"),
  draftRecipients: document.querySelector("#draftRecipients"),
  draftRecipientChips: document.querySelector("#draftRecipientChips"),
  draftMessage: document.querySelector("#draftMessage"),
  draftCopyToggle: document.querySelector("#draftCopyToggle"),
  draftOpenToggle: document.querySelector("#draftOpenToggle"),
  stageDraftButton: document.querySelector("#stageDraftButton"),
  clearDraftButton: document.querySelector("#clearDraftButton"),
  createContactState: document.querySelector("#createContactState"),
  newContactFirst: document.querySelector("#newContactFirst"),
  newContactLast: document.querySelector("#newContactLast"),
  newContactOrganization: document.querySelector("#newContactOrganization"),
  newContactPhones: document.querySelector("#newContactPhones"),
  newContactEmails: document.querySelector("#newContactEmails"),
  createContactButton: document.querySelector("#createContactButton"),
  clearContactButton: document.querySelector("#clearContactButton"),
  codexCount: document.querySelector("#codexCount"),
  codexModes: document.querySelector("#codexModes"),
  codexQuestion: document.querySelector("#codexQuestion"),
  codexPrompt: document.querySelector("#codexPrompt"),
  buildPromptButton: document.querySelector("#buildPromptButton"),
  copyPromptButton: document.querySelector("#copyPromptButton"),
};

const emojiChoices = ["👍", "🙏", "🔥", "❤️", "😂", "👀", "✅", "🤔", "😭", "🚀"];

const messageViews = [
  { key: "all", label: "All" },
  { key: "unread", label: "Unread" },
  { key: "files", label: "Files" },
  { key: "audio", label: "Audio" },
  { key: "mine", label: "Mine" },
];

const codexModes = {
  reply: {
    label: "Reply",
    question: "What should I reply next?",
    instruction: "Suggest a concise reply, flag any ambiguity, and preserve my tone.",
  },
  summary: {
    label: "Summary",
    question: "What matters in this thread?",
    instruction: "Summarize the current state of the conversation, decisions, loose ends, and any time-sensitive details.",
  },
  followups: {
    label: "Follow-ups",
    question: "What should I do next?",
    instruction: "Extract concrete follow-up actions, unanswered questions, deadlines, and people I may need to respond to.",
  },
  contacts: {
    label: "Contacts",
    question: "What contact or group-chat cleanup would help here?",
    instruction: "Look for useful contact updates, recipient cleanup, possible new-chat recipients, and group-chat management notes.",
  },
};

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

function splitValues(value) {
  return String(value || "")
    .split(/[\n,;]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function digitsOnly(value) {
  return String(value || "").replace(/\D+/g, "");
}

function contactDisplayName(contact) {
  return contact.display_name || [contact.first_name, contact.last_name].filter(Boolean).join(" ") || contact.organization || contact.primary_handle || "Contact";
}

function contactHandleText(contact) {
  return [contact.phone, contact.email].filter(Boolean).join(" · ") || contact.phone_normalized || "No handle";
}

function contactRecipientHandle(contact) {
  return contact.primary_handle || contact.phone || contact.email || contact.phone_normalized || "";
}

function contactNeedles(contact) {
  const values = [
    contact.primary_handle,
    contact.phone,
    contact.phone_normalized,
    contact.email,
    contactDisplayName(contact),
  ].filter(Boolean);
  const digitValues = values.map(digitsOnly).filter((value) => value.length >= 7);
  return [...new Set([...values.map((value) => String(value).toLowerCase()), ...digitValues])];
}

function handleType(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  if (text.includes("@")) return "email";
  if (digitsOnly(text).length >= 7) return "phone";
  return "handle";
}

function conversationParticipants(conversation = state.selected) {
  if (!conversation) return [];
  const values = Array.isArray(conversation.participants) ? conversation.participants : [];
  const sourceIdentifier = String(conversation.source_chat_identifier || "").trim();
  const candidates = [...values];
  if (sourceIdentifier && handleType(sourceIdentifier) !== "handle") {
    candidates.push(sourceIdentifier);
  }
  const seen = new Set();
  const participants = [];
  for (const value of candidates) {
    const handle = String(value || "").trim();
    const type = handleType(handle);
    const key = recipientCompareKey(handle);
    if (!handle || !type || !key || seen.has(key)) continue;
    seen.add(key);
    participants.push({ handle, type });
  }
  return participants;
}

function conversationHaystack(conversation) {
  const raw = [
    conversation.conversation_id,
    conversation.display_name,
    conversation.source_provider,
    conversation.source_chat_identifier,
    conversation.alias_email,
    conversation.last_message_sender,
    conversation.last_message_preview,
    conversation.note,
    conversation.draft_text,
    ...(conversation.labels || []),
    ...(conversation.participants || []),
  ].join(" ").toLowerCase();
  return `${raw} ${digitsOnly(raw)}`;
}

function labelsForConversation(conversation) {
  return Array.isArray(conversation?.labels) ? conversation.labels : [];
}

function conversationPreviewText(conversation) {
  const preview = String(conversation?.last_message_preview || "").trim();
  if (!preview) return "";
  const sender = String(conversation?.last_message_sender || "").trim();
  return sender ? `${sender}: ${preview}` : preview;
}

function labelKey(label) {
  return String(label || "").trim().toLowerCase();
}

function draftTextForConversation(conversation) {
  return String(conversation?.draft_text || "");
}

function conversationSortValue(conversation) {
  const raw = conversation.last_message_ts || conversation.updated_at || conversation.management_updated_at || "";
  const value = Date.parse(raw);
  return Number.isNaN(value) ? 0 : value;
}

function conversationMatchesView(conversation, view = state.conversationView) {
  if (view === "unread") return Number(conversation.unread_count || 0) > 0 && !conversation.is_archived;
  if (view === "pinned") return Boolean(conversation.is_pinned) && !conversation.is_archived;
  if (view === "archived") return Boolean(conversation.is_archived);
  if (view === "all") return true;
  return !conversation.is_archived;
}

function conversationMatchesLabel(conversation, label = state.conversationLabel) {
  const selected = labelKey(label);
  if (!selected) return true;
  return labelsForConversation(conversation).some((value) => labelKey(value) === selected);
}

function conversationViewCounts() {
  return {
    inbox: state.conversations.filter((conversation) => conversationMatchesView(conversation, "inbox")).length,
    unread: state.conversations.filter((conversation) => conversationMatchesView(conversation, "unread")).length,
    pinned: state.conversations.filter((conversation) => conversationMatchesView(conversation, "pinned")).length,
    archived: state.conversations.filter((conversation) => conversationMatchesView(conversation, "archived")).length,
    all: state.conversations.length,
  };
}

function conversationLabelCounts() {
  const counts = new Map();
  for (const conversation of state.conversations) {
    if (!conversationMatchesView(conversation)) continue;
    for (const label of labelsForConversation(conversation)) {
      const normalized = labelKey(label);
      if (!normalized) continue;
      const current = counts.get(normalized) || { label: String(label).trim(), count: 0 };
      current.count += 1;
      counts.set(normalized, current);
    }
  }
  return [...counts.values()].sort((a, b) => a.label.localeCompare(b.label));
}

function renderConversationFilters() {
  const counts = conversationViewCounts();
  for (const button of el.conversationFilters.querySelectorAll("button")) {
    const view = button.dataset.view;
    const label = view ? view.charAt(0).toUpperCase() + view.slice(1) : "View";
    button.textContent = `${label} ${counts[view] ?? 0}`;
    button.classList.toggle("active", view === state.conversationView);
    button.setAttribute("aria-pressed", view === state.conversationView ? "true" : "false");
  }
}

function renderLabelFilters() {
  const labelCounts = conversationLabelCounts();
  const activeLabel = labelKey(state.conversationLabel);
  el.labelFilters.replaceChildren();
  if (!labelCounts.length && !activeLabel) return;

  const clearButton = document.createElement("button");
  clearButton.type = "button";
  clearButton.dataset.label = "";
  clearButton.textContent = "All labels";
  clearButton.className = activeLabel ? "" : "active";
  clearButton.setAttribute("aria-pressed", activeLabel ? "false" : "true");
  el.labelFilters.append(clearButton);

  const labels = activeLabel && !labelCounts.some((item) => labelKey(item.label) === activeLabel)
    ? [{ label: state.conversationLabel, count: 0 }, ...labelCounts]
    : labelCounts;

  for (const item of labels) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.label = item.label;
    button.textContent = `#${item.label} ${item.count}`;
    const isActive = labelKey(item.label) === activeLabel;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-pressed", isActive ? "true" : "false");
    el.labelFilters.append(button);
  }
}

function visibleConversationRows() {
  const query = el.conversationSearch.value.trim().toLowerCase();
  const digitQuery = digitsOnly(query);
  return state.conversations.filter((conversation) => {
    if (!conversationMatchesView(conversation)) return false;
    if (!conversationMatchesLabel(conversation)) return false;
    const haystack = conversationHaystack(conversation);
    return !query || haystack.includes(query) || (digitQuery.length >= 3 && haystack.includes(digitQuery));
  }).sort((a, b) => {
    const pinnedDiff = Number(b.is_pinned) - Number(a.is_pinned);
    if (pinnedDiff) return pinnedDiff;
    return conversationSortValue(b) - conversationSortValue(a);
  });
}

function selectedConversations() {
  return state.conversations.filter((conversation) => state.selectedConversationIds.has(conversation.conversation_id));
}

function pruneSelectedConversations() {
  const ids = new Set(state.conversations.map((conversation) => conversation.conversation_id));
  for (const selectedId of state.selectedConversationIds) {
    if (!ids.has(selectedId)) state.selectedConversationIds.delete(selectedId);
  }
}

function renderBulkActions(rows) {
  const selectedCount = selectedConversations().length;
  const visibleCount = rows.length;
  const allVisibleSelected = visibleCount > 0 && rows.every((conversation) => state.selectedConversationIds.has(conversation.conversation_id));
  el.bulkState.textContent = state.bulkBusy ? "Updating selected" : (state.bulkMessage || `${selectedCount} selected`);
  el.selectVisibleButton.disabled = state.bulkBusy || !visibleCount || allVisibleSelected;
  el.bulkMarkReadButton.disabled = state.bulkBusy || selectedCount === 0;
  el.bulkArchiveButton.disabled = state.bulkBusy || selectedCount === 0;
  el.clearSelectionButton.disabled = state.bulkBusy || selectedCount === 0;
}

function isOwnMessage(message) {
  return message.sender_name === "Me" || message.direction === "manual_to_imessage" || message.direction === "email_to_imessage";
}

function isUnreadMessage(message) {
  return message.is_read === false || message.is_read === 0;
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

function isAudioAttachment(attachment) {
  if (!attachment || typeof attachment !== "object") return false;
  const mime = String(attachment.mime_type || "").toLowerCase();
  if (mime.startsWith("audio/")) return true;
  const name = basename(attachment.transfer_name || attachment.filename || attachment.path || "");
  return /\.(aac|aif|aiff|caf|m4a|mp3|wav)$/i.test(name);
}

function isImageAttachment(attachment) {
  if (!attachment || typeof attachment !== "object") return false;
  const mime = String(attachment.mime_type || "").toLowerCase();
  if (mime.startsWith("image/")) return true;
  const name = basename(attachment.transfer_name || attachment.filename || attachment.path || "");
  return /\.(avif|gif|heic|heif|jpe?g|png|webp)$/i.test(name);
}

function messageMatchesView(message, view = state.messageView) {
  if (view === "unread") return isUnreadMessage(message);
  if (view === "files") return attachmentRows(message).length > 0;
  if (view === "audio") return attachmentRows(message).some(isAudioAttachment);
  if (view === "mine") return isOwnMessage(message);
  return true;
}

function messageViewCounts() {
  return {
    all: state.messages.length,
    unread: state.messages.filter(isUnreadMessage).length,
    files: state.messages.filter((message) => attachmentRows(message).length > 0).length,
    audio: state.messages.filter((message) => attachmentRows(message).some(isAudioAttachment)).length,
    mine: state.messages.filter(isOwnMessage).length,
  };
}

function attachmentLocalPath(attachment) {
  if (!attachment || typeof attachment !== "object") return "";
  return attachment.filename || attachment.path || attachment.file_path || attachment.local_path || "";
}

function attachmentLabel(attachment) {
  const label = basename(attachment.transfer_name || attachment.filename || attachment.mime_type || "attachment");
  return String(attachment.mime_type || "").startsWith("audio/") ? `audio:${label}` : label;
}

function attachmentUrl(message, index) {
  if (!state.selected || !message.provider_message_id) return "";
  const conversationId = encodeURIComponent(state.selected.conversation_id);
  const messageId = encodeURIComponent(message.provider_message_id);
  return `/penguin-connect/conversations/${conversationId}/attachments/${index}?provider_message_id=${messageId}`;
}

function renderAudioAttachment(attachment, url) {
  const wrapper = document.createElement("div");
  wrapper.className = "audio-attachment";

  const link = document.createElement("a");
  link.className = "pill attachment-link";
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener";
  link.title = "Open audio attachment";
  link.textContent = attachmentLabel(attachment);

  const audio = document.createElement("audio");
  audio.controls = true;
  audio.preload = "metadata";
  audio.src = url;
  audio.setAttribute("aria-label", attachmentLabel(attachment));

  wrapper.append(link, audio);
  return wrapper;
}

function renderImageAttachment(attachment, url) {
  const wrapper = document.createElement("a");
  wrapper.className = "image-attachment attachment-link";
  wrapper.href = url;
  wrapper.target = "_blank";
  wrapper.rel = "noopener";
  wrapper.title = "Open image attachment";

  const image = document.createElement("img");
  image.src = url;
  image.alt = attachmentLabel(attachment);
  image.loading = "lazy";

  const caption = document.createElement("span");
  caption.textContent = attachmentLabel(attachment);

  wrapper.append(image, caption);
  return wrapper;
}

function messageSnippet(message, length = 180) {
  const text = trim(message.body_text || message.text || "", length);
  const attachments = attachmentRows(message).map((attachment) => basename(attachment.transfer_name || attachment.filename || attachment.mime_type || "attachment"));
  const suffix = attachments.length ? ` attachments: ${attachments.join(", ")}` : "";
  return `${text}${suffix ? ` ·${suffix}` : ""}`.trim() || "(no text)";
}

function messageSender(message) {
  return message.sender_name || message.sender_email || message.direction || "unknown";
}

function messageTime(message) {
  return formatTime(message.message_timestamp || message.timestamp);
}

function messageCopyText(message) {
  const attachments = attachmentRows(message).map((attachment) => basename(attachment.transfer_name || attachment.filename || attachment.mime_type || "attachment"));
  const parts = [
    `${messageTime(message)} | ${messageSender(message)}`,
    message.body_text || message.text || "",
  ];
  if (attachments.length) parts.push(`Attachments: ${attachments.join(", ")}`);
  return parts.filter(Boolean).join("\n");
}

function clearReplyContext() {
  state.replyContext = null;
  renderReplyContext();
  buildCodexPrompt();
}

function setReplyContext(message) {
  state.replyContext = {
    sender: messageSender(message),
    time: messageTime(message),
    snippet: messageSnippet(message, 160),
    provider_message_id: message.provider_message_id || "",
  };
  renderReplyContext();
  el.composer.focus();
  buildCodexPrompt();
}

function renderReplyContext() {
  if (!state.replyContext) {
    el.replyContext.hidden = true;
    el.replyContextText.textContent = "";
    return;
  }
  el.replyContext.hidden = false;
  el.replyContextText.textContent = `${state.replyContext.sender} · ${state.replyContext.snippet}`;
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
  pruneSelectedConversations();
  const rows = visibleConversationRows();

  renderConversationFilters();
  renderLabelFilters();
  renderBulkActions(rows);
  el.conversationList.replaceChildren();
  if (!rows.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "No conversations";
    el.conversationList.append(empty);
    return;
  }

  for (const conversation of rows) {
    const row = document.createElement("div");
    row.className = `conversation-row ${state.selected?.conversation_id === conversation.conversation_id ? "active" : ""}`;
    row.innerHTML = `
      <button class="conversation-select" type="button" title="Select conversation" aria-label="Select conversation"></button>
      <button class="conversation-item" type="button">
        <span class="conversation-title-row">
          <span class="conversation-name"></span>
          <span class="conversation-badges"></span>
        </span>
        <span class="conversation-meta"></span>
        <span class="conversation-preview"></span>
      </button>
    `;
    const selectButton = row.querySelector(".conversation-select");
    const isChecked = state.selectedConversationIds.has(conversation.conversation_id);
    selectButton.classList.toggle("active", isChecked);
    selectButton.textContent = isChecked ? "x" : "";
    selectButton.setAttribute("aria-pressed", isChecked ? "true" : "false");
    selectButton.addEventListener("click", () => {
      state.bulkMessage = "";
      if (state.selectedConversationIds.has(conversation.conversation_id)) {
        state.selectedConversationIds.delete(conversation.conversation_id);
      } else {
        state.selectedConversationIds.add(conversation.conversation_id);
      }
      renderConversations();
    });

    const mainButton = row.querySelector(".conversation-item");
    mainButton.querySelector(".conversation-name").textContent = conversation.display_name || "Conversation";
    const badges = mainButton.querySelector(".conversation-badges");
    if (conversation.unread_count) {
      const badge = document.createElement("span");
      badge.className = "badge unread-badge";
      badge.textContent = conversation.unread_count > 99 ? "99+" : String(conversation.unread_count);
      badges.append(badge);
    }
    if (conversation.is_pinned) {
      const badge = document.createElement("span");
      badge.className = "badge status-badge";
      badge.textContent = "pinned";
      badges.append(badge);
    }
    if (conversation.is_archived) {
      const badge = document.createElement("span");
      badge.className = "badge status-badge";
      badge.textContent = "archived";
      badges.append(badge);
    }
    if (draftTextForConversation(conversation).trim()) {
      const badge = document.createElement("span");
      badge.className = "badge draft-badge";
      badge.textContent = "draft";
      badges.append(badge);
    }
    for (const label of labelsForConversation(conversation).slice(0, 2)) {
      const badge = document.createElement("span");
      badge.className = "badge label-badge";
      badge.textContent = `#${label}`;
      badges.append(badge);
    }
    if (labelsForConversation(conversation).length > 2) {
      const badge = document.createElement("span");
      badge.className = "badge label-badge";
      badge.textContent = `+${labelsForConversation(conversation).length - 2}`;
      badges.append(badge);
    }
    if (conversation.status && conversation.status !== "active") {
      const badge = document.createElement("span");
      badge.className = "badge status-badge";
      badge.textContent = conversation.status;
      badges.append(badge);
    }
    if (conversation.excluded) {
      const badge = document.createElement("span");
      badge.className = "badge status-badge";
      badge.textContent = "excluded";
      badges.append(badge);
    }
    mainButton.querySelector(".conversation-meta").textContent = [
      conversation.chat_type || "chat",
      conversation.source_service_name || conversation.source_provider || "source",
      conversation.last_message_ts ? formatTime(conversation.last_message_ts) : conversation.status || "",
    ].filter(Boolean).join(" · ");
    const preview = mainButton.querySelector(".conversation-preview");
    const previewText = conversationPreviewText(conversation);
    preview.textContent = previewText;
    preview.hidden = !previewText;
    mainButton.addEventListener("click", () => {
      state.focusMessageId = "";
      selectConversation(conversation);
    });
    el.conversationList.append(row);
  }
}

function findConversationForContact(contact) {
  const needles = contactNeedles(contact);
  return state.conversations.find((conversation) => {
    const haystack = conversationHaystack(conversation);
    return needles.some((needle) => haystack.includes(needle));
  });
}

function recipientCompareKey(value) {
  const text = String(value || "").trim().toLowerCase();
  const digits = digitsOnly(text);
  return digits.length >= 7 && !text.includes("@") ? digits : text;
}

function draftRecipientValues() {
  return splitValues(el.draftRecipients.value);
}

function uniqueRecipientValues(values) {
  const seen = new Set();
  const recipients = [];
  for (const value of values) {
    const recipient = String(value || "").trim();
    const key = recipientCompareKey(recipient);
    if (!recipient || !key || seen.has(key)) continue;
    seen.add(key);
    recipients.push(recipient);
  }
  return recipients;
}

function renderDraftRecipientChips(values = uniqueRecipientValues(draftRecipientValues())) {
  el.draftRecipientChips.replaceChildren();
  if (!values.length) return;

  values.forEach((recipient, index) => {
    const chip = document.createElement("span");
    chip.className = "draft-recipient-chip";
    chip.innerHTML = `
      <span></span>
      <button type="button" title="Remove recipient" aria-label="Remove recipient">x</button>
    `;
    chip.querySelector("span").textContent = recipient;
    chip.querySelector("button").addEventListener("click", () => removeDraftRecipient(index));
    el.draftRecipientChips.append(chip);
  });
}

function setDraftRecipients(values, { focus = false } = {}) {
  const recipients = uniqueRecipientValues(values);
  el.draftRecipients.value = recipients.join(", ");
  renderDraftRecipientChips(recipients);
  if (focus) el.draftRecipients.focus();
  return recipients;
}

function removeDraftRecipient(index) {
  const recipients = uniqueRecipientValues(draftRecipientValues());
  if (index < 0 || index >= recipients.length) return;
  recipients.splice(index, 1);
  setDraftRecipients(recipients, { focus: true });
  el.draftState.textContent = recipients.length ? "Recipient removed" : "Add recipient";
}

function addDraftRecipient(value) {
  const recipient = String(value || "").trim();
  if (!recipient) return false;

  const recipients = uniqueRecipientValues(draftRecipientValues());
  const key = recipientCompareKey(recipient);
  if (new Set(recipients.map(recipientCompareKey)).has(key)) {
    el.draftState.textContent = "Recipient already added";
    el.draftRecipients.focus();
    return false;
  }

  setDraftRecipients([...recipients, recipient], { focus: true });
  el.draftState.textContent = "Recipient added";
  return true;
}

function fillContactFormFromHandle(value) {
  const handle = String(value || "").trim();
  if (!handle) return;
  clearContactForm();
  if (handleType(handle) === "email") {
    el.newContactEmails.value = handle;
  } else {
    el.newContactPhones.value = handle;
  }
  el.createContactState.textContent = "Prefilled from thread";
  el.newContactFirst.focus();
}

function searchContactHandle(value) {
  const handle = String(value || "").trim();
  if (!handle) return;
  el.contactSearch.value = handle;
  el.contactStatus.textContent = "Searching thread handle";
  loadContacts({ force: true });
}

function addParticipantToDraft(value) {
  const added = addDraftRecipient(value);
  el.threadPeopleState.textContent = added ? "Added to new chat" : "Already in new chat";
}

function addContactToDraft(contact) {
  const handle = contactRecipientHandle(contact);
  if (!handle) {
    el.contactStatus.textContent = "No phone or email on contact";
    return;
  }

  const added = addDraftRecipient(handle);
  el.contactStatus.textContent = added ? "Added contact to new chat" : "Contact already in new chat";
}

async function useContact(contact) {
  const searchValue = contact.primary_handle || contact.phone_normalized || contactDisplayName(contact);
  el.conversationSearch.value = searchValue;
  state.focusMessageId = "";
  renderConversations();
  const match = findConversationForContact(contact);
  if (match) {
    el.contactStatus.textContent = `Matched ${match.display_name || "conversation"}`;
    await selectConversation(match);
  } else {
    el.contactStatus.textContent = "No matching synced conversation";
  }
}

function renderThreadPeople() {
  el.threadPeople.replaceChildren();
  const participants = conversationParticipants();
  el.threadPeopleState.textContent = state.selected
    ? `${participants.length} participant${participants.length === 1 ? "" : "s"}`
    : "No thread";
  if (!state.selected) {
    const empty = document.createElement("div");
    empty.className = "empty-state compact-state";
    empty.textContent = "Select a conversation";
    el.threadPeople.append(empty);
    return;
  }
  if (!participants.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state compact-state";
    empty.textContent = "No participant handles";
    el.threadPeople.append(empty);
    return;
  }

  for (const participant of participants) {
    const item = document.createElement("div");
    item.className = "thread-person";
    item.innerHTML = `
      <div class="thread-person-main">
        <span class="thread-person-handle"></span>
        <span class="thread-person-type"></span>
      </div>
      <div class="thread-person-actions">
        <button type="button" data-action="search">Search</button>
        <button type="button" data-action="draft">New chat</button>
        <button type="button" data-action="contact">Create</button>
      </div>
    `;
    item.querySelector(".thread-person-handle").textContent = participant.handle;
    item.querySelector(".thread-person-type").textContent = participant.type;
    item.querySelector('[data-action="search"]').addEventListener("click", () => searchContactHandle(participant.handle));
    item.querySelector('[data-action="draft"]').addEventListener("click", () => addParticipantToDraft(participant.handle));
    item.querySelector('[data-action="contact"]').addEventListener("click", () => fillContactFormFromHandle(participant.handle));
    el.threadPeople.append(item);
  }
}

function conversationFromSearchResult(result) {
  return state.conversations.find((conversation) => conversation.conversation_id === result.conversation_id) || {
    conversation_id: result.conversation_id,
    display_name: result.display_name || "Conversation",
    source_provider: result.source_provider || result.provider || "imessage",
    source_service_name: result.source_service_name || "",
    chat_type: result.chat_type || "chat",
    participants: [],
  };
}

async function useMessageSearchResult(result) {
  state.focusMessageId = result.provider_message_id || "";
  el.conversationSearch.value = "";
  renderConversations();
  await selectConversation(conversationFromSearchResult(result));
}

function renderContacts() {
  el.contactList.replaceChildren();
  if (!state.contacts.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state compact-state";
    empty.textContent = el.contactSearch.value.trim() ? "No contacts" : "Search Contacts";
    el.contactList.append(empty);
    return;
  }

  for (const contact of state.contacts) {
    const item = document.createElement("div");
    item.className = "contact-item";
    item.innerHTML = `
      <button class="contact-main" type="button">
        <span class="contact-name"></span>
        <span class="contact-handle"></span>
        <span class="contact-meta"></span>
      </button>
      <button class="contact-add" type="button" title="Add to new chat" aria-label="Add contact to new chat">+</button>
    `;
    item.querySelector(".contact-name").textContent = contactDisplayName(contact);
    item.querySelector(".contact-handle").textContent = contactHandleText(contact);
    item.querySelector(".contact-meta").textContent = contact.organization && contact.organization !== contactDisplayName(contact)
      ? contact.organization
      : contact.handle_type || "contact";
    item.querySelector(".contact-main").addEventListener("click", () => useContact(contact));
    const addButton = item.querySelector(".contact-add");
    addButton.disabled = !contactRecipientHandle(contact);
    addButton.addEventListener("click", () => addContactToDraft(contact));
    el.contactList.append(item);
  }
}

function renderMessageSearchResults() {
  el.messageSearchResults.replaceChildren();
  const query = el.globalMessageSearch.value.trim();
  if (!query) {
    el.messageSearchResults.hidden = true;
    return;
  }
  el.messageSearchResults.hidden = false;
  if (!state.messageSearchResults.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state compact-state";
    empty.textContent = "No message matches";
    el.messageSearchResults.append(empty);
    return;
  }

  for (const result of state.messageSearchResults) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "search-result";
    button.innerHTML = `
      <span class="search-result-top"></span>
      <span class="search-result-body"></span>
    `;
    const sender = result.sender_name || result.sender_email || result.direction || "unknown";
    button.querySelector(".search-result-top").textContent = [
      result.display_name || "Conversation",
      sender,
      formatTime(result.message_timestamp || result.timestamp),
    ].filter(Boolean).join(" · ");
    button.querySelector(".search-result-body").textContent = messageSnippet(result);
    button.addEventListener("click", () => useMessageSearchResult(result));
    el.messageSearchResults.append(button);
  }
}

function renderMessageViewFilters() {
  const counts = messageViewCounts();
  el.messageViewFilters.replaceChildren();
  for (const view of messageViews) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.messageView = view.key;
    button.textContent = `${view.label} ${counts[view.key] ?? 0}`;
    const active = state.messageView === view.key;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
    el.messageViewFilters.append(button);
  }
}

function renderMessages() {
  const query = el.messageFilter.value.trim().toLowerCase();
  renderMessageViewFilters();
  const rows = [...state.messages].reverse().filter((message) => {
    const haystack = [
      message.sender_name,
      message.sender_email,
      message.body_text,
      JSON.stringify(attachmentRows(message)),
    ].join(" ").toLowerCase();
    return messageMatchesView(message) && (!query || haystack.includes(query));
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
    empty.textContent = query || state.messageView !== "all" ? "No matching messages" : "No loaded messages";
    el.messageList.append(empty);
    return;
  }

  for (const message of rows) {
    const item = document.createElement("article");
    const focused = state.focusMessageId && message.provider_message_id === state.focusMessageId;
    const unread = isUnreadMessage(message);
    item.className = `message ${isOwnMessage(message) ? "mine" : ""} ${unread ? "unread" : ""} ${focused ? "focused" : ""}`;
    item.dataset.messageId = message.provider_message_id || "";
    const attachments = attachmentRows(message);
    item.innerHTML = `
      <div class="message-head">
        <span></span>
        <time></time>
      </div>
      <div class="message-body"></div>
      <div class="message-attachments"></div>
      <div class="message-actions">
        <button type="button" data-action="reply">Reply</button>
        <button type="button" data-action="copy">Copy</button>
      </div>
    `;
    item.querySelector(".message-head span").textContent = messageSender(message);
    item.querySelector("time").textContent = messageTime(message);
    item.querySelector(".message-body").textContent = message.body_text || message.text || "";
    item.querySelector('[data-action="reply"]').addEventListener("click", () => setReplyContext(message));
    item.querySelector('[data-action="copy"]').addEventListener("click", async () => {
      await copyText(messageCopyText(message));
      el.sendState.textContent = "Message copied";
    });
    const attachmentBox = item.querySelector(".message-attachments");
    for (const [index, attachment] of attachments.entries()) {
      const url = attachmentLocalPath(attachment) ? attachmentUrl(message, index) : "";
      if (url && isAudioAttachment(attachment)) {
        attachmentBox.append(renderAudioAttachment(attachment, url));
        continue;
      }
      if (url && isImageAttachment(attachment)) {
        attachmentBox.append(renderImageAttachment(attachment, url));
        continue;
      }
      const pill = document.createElement(url ? "a" : "span");
      pill.className = `pill${url ? " attachment-link" : ""}`;
      if (url) {
        pill.href = url;
        pill.target = "_blank";
        pill.rel = "noopener";
        pill.title = "Open attachment";
      }
      pill.textContent = attachmentLabel(attachment);
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

function renderThreadControls() {
  const selected = state.selected;
  const hasSelection = Boolean(selected);
  el.pinButton.disabled = !hasSelection;
  el.archiveButton.disabled = !hasSelection;
  el.saveManagementButton.disabled = !hasSelection;
  el.threadTags.disabled = !hasSelection;
  el.threadNote.disabled = !hasSelection;
  el.markReadButton.disabled = !hasSelection;
  el.markUnreadButton.disabled = !hasSelection;
  el.connectionButton.disabled = !hasSelection;
  el.copyThreadButton.disabled = !hasSelection;
  el.syncButton.disabled = false;
  if (!hasSelection) {
    el.threadStatus.textContent = "No conversation selected";
    el.managementState.textContent = "No thread";
    el.pinButton.textContent = "Pin";
    el.archiveButton.textContent = "Archive";
    el.connectionButton.textContent = "Disconnect";
    return;
  }
  const unread = Number(selected.unread_count || 0);
  const status = selected.status || "active";
  const excluded = selected.excluded ? " · excluded" : "";
  const managed = [
    selected.is_pinned ? "pinned" : "",
    selected.is_archived ? "archived" : "",
  ].filter(Boolean).join(" · ");
  el.threadStatus.textContent = `${status}${excluded}${managed ? ` · ${managed}` : ""} · ${unread} unread · ${selected.alias_email || "no alias"}`;
  el.pinButton.textContent = selected.is_pinned ? "Unpin" : "Pin";
  el.archiveButton.textContent = selected.is_archived ? "Unarchive" : "Archive";
  el.connectionButton.textContent = status === "active" ? "Disconnect" : "Reconnect";
}

function renderManagementFields() {
  if (!state.selected) {
    el.threadTags.value = "";
    el.threadNote.value = "";
    el.managementState.textContent = "No thread";
    return;
  }
  el.threadTags.value = labelsForConversation(state.selected).join(", ");
  el.threadNote.value = state.selected.note || "";
  el.managementState.textContent = "Saved";
}

function syncSelectedConversation(fields) {
  if (!state.selected) return;
  Object.assign(state.selected, fields);
  state.conversations = state.conversations.map((conversation) => (
    conversation.conversation_id === state.selected.conversation_id
      ? { ...conversation, ...fields }
      : conversation
  ));
  renderConversations();
  renderThreadControls();
  renderManagementFields();
  renderThreadPeople();
  buildCodexPrompt();
}

function updateConversationFields(conversationId, fields) {
  state.conversations = state.conversations.map((conversation) => (
    conversation.conversation_id === conversationId
      ? { ...conversation, ...fields }
      : conversation
  ));
  if (state.selected?.conversation_id === conversationId) {
    Object.assign(state.selected, fields);
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
    if (state.selected) {
      state.selected = state.conversations.find((conversation) => conversation.conversation_id === state.selected.conversation_id) || state.selected;
    }
    el.senderBadge.textContent = state.senderEmail || "No sender";
    renderConversations();
    if (!state.selected && state.conversations.length) {
      await selectConversation(state.conversations.find((conversation) => !conversation.is_archived) || state.conversations[0]);
    }
  } catch (error) {
    el.conversationList.innerHTML = `<div class="error-state">${error.message}</div>`;
  }
}

async function loadContacts({ force = false } = {}) {
  const query = el.contactSearch.value.trim();
  if (!force && query.length < 2) {
    state.contacts = [];
    el.contactStatus.textContent = "Type 2+ chars to search cached Contacts";
    renderContacts();
    buildCodexPrompt();
    return;
  }

  el.contactStatus.textContent = "Searching";
  try {
    const payload = await api(`/penguin-connect/contacts?search=${encodeURIComponent(query)}&limit=20`);
    state.contacts = payload.contacts || [];
    const total = payload.total_contacts ?? 0;
    el.contactStatus.textContent = query
      ? `${state.contacts.length} match${state.contacts.length === 1 ? "" : "es"} · ${total} cached`
      : `${state.contacts.length} contacts · ${total} cached`;
    renderContacts();
    buildCodexPrompt();
  } catch (error) {
    state.contacts = [];
    el.contactStatus.textContent = error.message;
    renderContacts();
    buildCodexPrompt();
  }
}

function scheduleContactSearch() {
  clearTimeout(state.contactSearchTimer);
  state.contactSearchTimer = setTimeout(() => loadContacts(), 180);
}

async function loadMessageSearch() {
  const query = el.globalMessageSearch.value.trim();
  if (query.length < 2) {
    state.messageSearchResults = [];
    el.messageSearchStatus.textContent = "Type 2+ chars to search local cache";
    renderMessageSearchResults();
    buildCodexPrompt();
    return;
  }

  el.messageSearchStatus.textContent = "Searching local cache";
  try {
    const payload = await api(`/penguin-connect/messages/search?query=${encodeURIComponent(query)}&limit=30`);
    state.messageSearchResults = payload.messages || [];
    el.messageSearchStatus.textContent = `${state.messageSearchResults.length} message match${state.messageSearchResults.length === 1 ? "" : "es"}`;
    renderMessageSearchResults();
    buildCodexPrompt();
  } catch (error) {
    state.messageSearchResults = [];
    el.messageSearchStatus.textContent = error.message;
    renderMessageSearchResults();
    buildCodexPrompt();
  }
}

function scheduleMessageSearch() {
  clearTimeout(state.messageSearchTimer);
  state.messageSearchTimer = setTimeout(() => loadMessageSearch(), 180);
}

async function selectConversation(conversation) {
  state.selected = conversation;
  state.messages = [];
  clearReplyContext();
  el.composer.value = draftTextForConversation(conversation);
  el.threadProvider.textContent = [conversation.source_provider, conversation.source_service_name, conversation.chat_type].filter(Boolean).join(" · ");
  el.threadTitle.textContent = conversation.display_name || conversation.conversation_id;
  renderThreadControls();
  renderManagementFields();
  renderThreadPeople();
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
      const focused = el.messageList.querySelector(".message.focused");
      if (focused) {
        focused.scrollIntoView({ block: "center" });
      } else {
        el.messageList.scrollTop = el.messageList.scrollHeight;
      }
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
  const conversationId = state.selected.conversation_id;
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
    await api(`/penguin-connect/conversations/${encodeURIComponent(conversationId)}/send`, {
      method: "POST",
      body: JSON.stringify({
        sender_email: state.senderEmail,
        message,
        attachments,
      }),
    });
    el.composer.value = "";
    clearReplyContext();
    state.attachments = [];
    renderAttachments();
    clearTimeout(state.draftSaveTimer);
    await saveLocalDraft(conversationId, "", { silent: true });
    el.sendState.textContent = "Sent";
    await loadMessages();
  } catch (error) {
    el.sendState.textContent = error.message;
  } finally {
    el.sendButton.disabled = false;
  }
}

async function setReadState(unread) {
  if (!state.selected) return;
  const label = unread ? "Marking unread" : "Marking read";
  el.threadStatus.textContent = label;
  el.markReadButton.disabled = true;
  el.markUnreadButton.disabled = true;
  try {
    const result = await api(`/penguin-connect/conversations/${encodeURIComponent(state.selected.conversation_id)}/read-state`, {
      method: "POST",
      body: JSON.stringify({ unread }),
    });
    syncSelectedConversation({
      unread_count: result.unread_count || 0,
      has_unread: Boolean(result.has_unread),
    });
    state.messages = state.messages.map((message) => ({ ...message, is_read: !unread }));
    renderMessages();
  } catch (error) {
    el.threadStatus.textContent = error.message;
  } finally {
    renderThreadControls();
  }
}

async function setConversationManagement(fields) {
  if (!state.selected) return;
  el.pinButton.disabled = true;
  el.archiveButton.disabled = true;
  el.saveManagementButton.disabled = true;
  el.threadStatus.textContent = "Updating thread";
  try {
    const result = await api(`/penguin-connect/conversations/${encodeURIComponent(state.selected.conversation_id)}/management`, {
      method: "POST",
      body: JSON.stringify(fields),
    });
    syncSelectedConversation(conversationManagementUpdates(result, fields));
  } catch (error) {
    el.threadStatus.textContent = error.message;
  } finally {
    renderThreadControls();
  }
}

function conversationManagementUpdates(result, fields = {}) {
  const updates = {
    is_pinned: Boolean(result.is_pinned),
    is_archived: Boolean(result.is_archived),
    note: result.note || "",
    labels: result.labels || [],
    management_updated_at: result.management_updated_at || "",
  };
  if (Object.prototype.hasOwnProperty.call(fields, "draft_text")) {
    updates.draft_text = result.draft_text || "";
  }
  return updates;
}

async function updateConversationManagement(conversationId, fields) {
  const result = await api(`/penguin-connect/conversations/${encodeURIComponent(conversationId)}/management`, {
    method: "POST",
    body: JSON.stringify(fields),
  });
  const updates = conversationManagementUpdates(result, fields);
  updateConversationFields(conversationId, updates);
  return updates;
}

function selectedConversationSnapshot() {
  return selectedConversations().map((conversation) => ({ ...conversation }));
}

async function bulkMarkSelectedRead() {
  const targets = selectedConversationSnapshot();
  if (!targets.length) return;
  state.bulkBusy = true;
  state.bulkMessage = "";
  renderConversations();
  try {
    for (const conversation of targets) {
      const result = await api(`/penguin-connect/conversations/${encodeURIComponent(conversation.conversation_id)}/read-state`, {
        method: "POST",
        body: JSON.stringify({ unread: false }),
      });
      updateConversationFields(conversation.conversation_id, {
        unread_count: result.unread_count || 0,
        has_unread: Boolean(result.has_unread),
      });
      if (state.selected?.conversation_id === conversation.conversation_id) {
        state.messages = state.messages.map((message) => ({ ...message, is_read: true }));
      }
    }
    state.selectedConversationIds.clear();
    state.bulkMessage = `Marked ${targets.length} read`;
  } catch (error) {
    state.bulkMessage = error.message;
  } finally {
    state.bulkBusy = false;
    renderConversations();
    renderThreadControls();
    renderMessages();
  }
}

async function bulkArchiveSelected() {
  const targets = selectedConversationSnapshot();
  if (!targets.length) return;
  if (!window.confirm(`Archive ${targets.length} selected conversation${targets.length === 1 ? "" : "s"}?`)) return;
  state.bulkBusy = true;
  state.bulkMessage = "";
  renderConversations();
  try {
    for (const conversation of targets) {
      await updateConversationManagement(conversation.conversation_id, { archived: true });
    }
    state.selectedConversationIds.clear();
    state.bulkMessage = `Archived ${targets.length}`;
  } catch (error) {
    state.bulkMessage = error.message;
  } finally {
    state.bulkBusy = false;
    renderConversations();
    renderThreadControls();
    renderManagementFields();
    buildCodexPrompt();
  }
}

async function saveLocalDraft(conversationId, draftText, { silent = false } = {}) {
  if (!conversationId) return;
  try {
    const result = await api(`/penguin-connect/conversations/${encodeURIComponent(conversationId)}/management`, {
      method: "POST",
      body: JSON.stringify({ draft_text: draftText }),
    });
    const serverDraft = result.draft_text || "";
    const current = state.conversations.find((conversation) => conversation.conversation_id === conversationId);
    if (current && draftTextForConversation(current) !== draftText) {
      return;
    }
    updateConversationFields(conversationId, {
      draft_text: serverDraft,
      management_updated_at: result.management_updated_at || "",
    });
    renderConversations();
    if (state.selected?.conversation_id === conversationId) {
      buildCodexPrompt();
      if (!silent && el.composer.value === draftText) {
        el.sendState.textContent = serverDraft.trim() ? "Draft saved" : "";
      }
    }
  } catch (error) {
    if (!silent && state.selected?.conversation_id === conversationId) {
      el.sendState.textContent = `Draft not saved · ${error.message}`;
    }
  }
}

function scheduleDraftSave() {
  if (!state.selected) return;
  const conversationId = state.selected.conversation_id;
  const draftText = el.composer.value;
  updateConversationFields(conversationId, { draft_text: draftText });
  renderConversations();
  clearTimeout(state.draftSaveTimer);
  state.draftSaveTimer = setTimeout(() => saveLocalDraft(conversationId, draftText), 450);
}

async function saveConversationManagement() {
  await setConversationManagement({
    note: el.threadNote.value,
    labels: splitValues(el.threadTags.value),
  });
}

async function toggleConnection() {
  if (!state.selected) return;
  const active = (state.selected.status || "active") === "active";
  if (active && !window.confirm("Disconnect this local bridge conversation? Cached messages for this bridge thread will be removed.")) {
    return;
  }
  el.connectionButton.disabled = true;
  el.threadStatus.textContent = active ? "Disconnecting" : "Reconnecting";
  const path = active ? "disconnect" : "reconnect";
  try {
    const result = await api(`/penguin-connect/conversations/${encodeURIComponent(state.selected.conversation_id)}/${path}`, {
      method: "POST",
      body: "{}",
    });
    syncSelectedConversation({
      status: result.status || (active ? "disconnected" : "active"),
      alias_email: result.alias_email || (active ? "" : state.selected.alias_email),
      unread_count: active ? 0 : state.selected.unread_count || 0,
      has_unread: active ? false : Boolean(state.selected.unread_count),
    });
    if (active) {
      state.messages = [];
      renderMessages();
    }
  } catch (error) {
    el.threadStatus.textContent = error.message;
  } finally {
    renderThreadControls();
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

function selectedConversationContext() {
  if (!state.selected) return "none";
  const labels = splitValues(el.threadTags.value).join(", ") || "none";
  const note = el.threadNote.value.trim() || "none";
  const participants = Array.isArray(state.selected.participants) && state.selected.participants.length
    ? state.selected.participants.slice(0, 14).join(", ")
    : "unknown";
  return [
    `Conversation: ${state.selected.display_name || state.selected.conversation_id}`,
    `Provider: ${[state.selected.source_provider, state.selected.source_service_name, state.selected.chat_type].filter(Boolean).join(" · ") || "imessage"}`,
    `Participants: ${participants}`,
    `Unread count: ${Number(state.selected.unread_count || 0)}`,
    `Pinned: ${Boolean(state.selected.is_pinned)}`,
    `Archived: ${Boolean(state.selected.is_archived)}`,
    `Thread tags: ${labels}`,
    `Private note: ${note}`,
    `Latest preview: ${conversationPreviewText(state.selected) || "none"}`,
  ].join("\n");
}

function codexReplyTargetText() {
  return state.replyContext
    ? `${state.replyContext.sender} at ${state.replyContext.time}: ${state.replyContext.snippet}`
    : "none";
}

function plannedAttachmentText() {
  return state.attachments.map((file) => `${file.name} (${file.type || "file"}, ${file.size} bytes)`).join(", ") || "none";
}

function messageSearchContext(limit = 8) {
  const query = el.globalMessageSearch.value.trim();
  if (!query) return "none";
  const rows = state.messageSearchResults.slice(0, limit).map((result) => {
    const sender = result.sender_name || result.sender_email || result.direction || "unknown";
    return `${formatTime(result.message_timestamp || result.timestamp)} | ${result.display_name || result.conversation_id || "Conversation"} | ${sender}: ${messageSnippet(result, 180)}`;
  });
  return [
    `Query: ${query}`,
    rows.length ? rows.join("\n") : "No loaded results",
  ].join("\n");
}

function contactContext(limit = 8) {
  if (!state.contacts.length) return "none";
  return state.contacts.slice(0, limit).map((contact) => {
    const organization = contact.organization ? ` | ${contact.organization}` : "";
    return `${contactDisplayName(contact)} | ${contactHandleText(contact)}${organization}`;
  }).join("\n");
}

function renderCodexModes() {
  for (const button of el.codexModes.querySelectorAll("button[data-codex-mode]")) {
    const active = button.dataset.codexMode === state.codexMode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  }
}

function codexModeConfig() {
  return codexModes[state.codexMode] || codexModes.reply;
}

function buildCodexPrompt() {
  const mode = codexModeConfig();
  const question = el.codexQuestion.value.trim() || mode.question;
  const draft = el.composer.value.trim() || "(no draft yet)";
  const prompt = [
    "Help me work through this local iMessage conversation.",
    "",
    `Mode: ${mode.label}`,
    `Question: ${question}`,
    `Task: ${mode.instruction}`,
    "",
    "Conversation state:",
    selectedConversationContext(),
    "",
    `Reply target: ${codexReplyTargetText()}`,
    `Attachments I plan to send: ${plannedAttachmentText()}`,
    "",
    "Loaded contact context:",
    contactContext(),
    "",
    "Current message search context:",
    messageSearchContext(),
    "",
    "Recent messages:",
    threadText() || "none",
    "",
    "My draft:",
    draft,
    "",
    "Answer with only the useful output for this mode. Do not invent missing context.",
  ].join("\n");
  el.codexPrompt.value = prompt;
  el.codexCount.textContent = `${mode.label} · ${Math.min(state.messages.length, 18)} msgs`;
  renderCodexModes();
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

function clearDraftForm() {
  el.draftRecipients.value = "";
  el.draftMessage.value = "";
  el.draftState.textContent = "Idle";
  renderDraftRecipientChips();
}

async function stageDraft() {
  const participants = setDraftRecipients(draftRecipientValues());
  if (!participants.length) {
    el.draftState.textContent = "Add recipient";
    return;
  }

  el.stageDraftButton.disabled = true;
  el.draftState.textContent = "Staging";
  try {
    const result = await api("/penguin-connect/messages/draft", {
      method: "POST",
      body: JSON.stringify({
        participants,
        message: el.draftMessage.value,
        copy_to_clipboard: el.draftCopyToggle.checked,
        open_messages: el.draftOpenToggle.checked,
      }),
    });
    const actions = [
      result.copied ? "copied" : "",
      result.opened_messages ? "opened" : "",
    ].filter(Boolean).join(" + ");
    el.draftState.textContent = actions ? `Draft ${actions}` : "Draft ready";
  } catch (error) {
    el.draftState.textContent = error.message;
  } finally {
    el.stageDraftButton.disabled = false;
  }
}

function clearContactForm() {
  el.newContactFirst.value = "";
  el.newContactLast.value = "";
  el.newContactOrganization.value = "";
  el.newContactPhones.value = "";
  el.newContactEmails.value = "";
  el.createContactState.textContent = "Idle";
}

async function createContact() {
  const phones = splitValues(el.newContactPhones.value);
  const emails = splitValues(el.newContactEmails.value);
  const firstName = el.newContactFirst.value.trim();
  const lastName = el.newContactLast.value.trim();
  const organization = el.newContactOrganization.value.trim();
  if (!firstName && !lastName && !organization && !phones.length && !emails.length) {
    el.createContactState.textContent = "Add details";
    return;
  }

  el.createContactButton.disabled = true;
  el.createContactState.textContent = "Creating";
  try {
    await api("/penguin-connect/contacts", {
      method: "POST",
      body: JSON.stringify({
        first_name: firstName,
        last_name: lastName,
        organization,
        phones,
        emails,
        refresh_after: true,
      }),
    });
    const searchValue = [firstName, lastName].filter(Boolean).join(" ") || organization || phones[0] || emails[0] || "";
    if (searchValue) el.contactSearch.value = searchValue;
    await loadContacts({ force: true });
    el.createContactState.textContent = "Created";
  } catch (error) {
    el.createContactState.textContent = error.message;
  } finally {
    el.createContactButton.disabled = false;
  }
}

el.refreshButton.addEventListener("click", () => {
  loadStatus();
  loadConversations();
  loadContacts();
});
el.conversationSearch.addEventListener("input", renderConversations);
el.contactSearch.addEventListener("input", scheduleContactSearch);
el.globalMessageSearch.addEventListener("input", scheduleMessageSearch);
el.contactRefreshButton.addEventListener("click", async () => {
  el.contactRefreshButton.disabled = true;
  el.contactStatus.textContent = "Refreshing Contacts";
  try {
    await api("/penguin-connect/contacts/refresh", { method: "POST", body: "{}" });
    await loadContacts({ force: true });
  } catch (error) {
    el.contactStatus.textContent = error.message;
  } finally {
    el.contactRefreshButton.disabled = false;
  }
});
el.messageFilter.addEventListener("input", renderMessages);
el.messageViewFilters.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-message-view]");
  if (!button) return;
  state.messageView = messageViews.some((view) => view.key === button.dataset.messageView)
    ? button.dataset.messageView
    : "all";
  renderMessages();
});
el.draftRecipients.addEventListener("input", () => renderDraftRecipientChips());
el.draftRecipients.addEventListener("blur", (event) => {
  if (event.relatedTarget && el.draftRecipientChips.contains(event.relatedTarget)) return;
  setDraftRecipients(draftRecipientValues());
});
el.sendButton.addEventListener("click", sendMessage);
el.pinButton.addEventListener("click", () => setConversationManagement({ pinned: !Boolean(state.selected?.is_pinned) }));
el.archiveButton.addEventListener("click", () => setConversationManagement({ archived: !Boolean(state.selected?.is_archived) }));
el.saveManagementButton.addEventListener("click", saveConversationManagement);
el.threadTags.addEventListener("input", () => {
  el.managementState.textContent = state.selected ? "Unsaved" : "No thread";
  buildCodexPrompt();
});
el.threadNote.addEventListener("input", () => {
  el.managementState.textContent = state.selected ? "Unsaved" : "No thread";
  buildCodexPrompt();
});
el.markReadButton.addEventListener("click", () => setReadState(false));
el.markUnreadButton.addEventListener("click", () => setReadState(true));
el.connectionButton.addEventListener("click", toggleConnection);
el.clearReplyContextButton.addEventListener("click", clearReplyContext);
el.clearButton.addEventListener("click", () => {
  el.composer.value = "";
  clearReplyContext();
  state.attachments = [];
  renderAttachments();
  scheduleDraftSave();
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
el.stageDraftButton.addEventListener("click", stageDraft);
el.clearDraftButton.addEventListener("click", clearDraftForm);
el.createContactButton.addEventListener("click", createContact);
el.clearContactButton.addEventListener("click", clearContactForm);
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
el.composer.addEventListener("input", () => {
  scheduleDraftSave();
  buildCodexPrompt();
});
el.buildPromptButton.addEventListener("click", buildCodexPrompt);
el.copyPromptButton.addEventListener("click", async () => {
  await copyText(buildCodexPrompt());
  el.sendState.textContent = "Codex prompt copied";
});
el.codexQuestion.addEventListener("input", buildCodexPrompt);
el.codexModes.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-codex-mode]");
  if (!button) return;
  state.codexMode = codexModes[button.dataset.codexMode] ? button.dataset.codexMode : "reply";
  buildCodexPrompt();
});
el.conversationFilters.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-view]");
  if (!button) return;
  state.conversationView = button.dataset.view || "inbox";
  renderConversations();
});
el.labelFilters.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-label]");
  if (!button) return;
  state.conversationLabel = button.dataset.label || "";
  renderConversations();
});
el.selectVisibleButton.addEventListener("click", () => {
  for (const conversation of visibleConversationRows()) {
    state.selectedConversationIds.add(conversation.conversation_id);
  }
  state.bulkMessage = "";
  renderConversations();
});
el.clearSelectionButton.addEventListener("click", () => {
  state.selectedConversationIds.clear();
  state.bulkMessage = "";
  renderConversations();
});
el.bulkMarkReadButton.addEventListener("click", bulkMarkSelectedRead);
el.bulkArchiveButton.addEventListener("click", bulkArchiveSelected);

renderEmojiButtons();
renderMessages();
renderContacts();
renderDraftRecipientChips();
renderMessageSearchResults();
renderThreadControls();
renderThreadPeople();
renderCodexModes();
loadStatus();
loadConversations();
