const state = {
  conversations: [],
  selected: null,
  messages: [],
  messagesLoading: false,
  replyContext: null,
  senderEmail: "",
  attachments: [],
  draftAttachments: [],
  draftAttachmentFolder: "",
  draftAttachmentPaths: [],
  contacts: [],
  contactSourceCounts: {},
  contactSource: "all",
  contactSearchTimer: null,
  contactNoteEditorKey: "",
  selectedContactKeys: new Set(),
  recipientLists: [],
  activeRecipientListId: "",
  threadContactMatches: {},
  threadContactToken: 0,
  messageSearchResults: [],
  messageSearchTimer: null,
  messageSearchView: "all",
  messageSearchNoteEditorId: "",
  focusMessageId: "",
  messageView: "all",
  messageNoteEditorId: "",
  mediaView: "all",
  conversationView: "inbox",
  conversationLabel: "",
  selectedConversationIds: new Set(),
  bulkBusy: false,
  bulkMessage: "",
  localRefreshBusy: false,
  draftSaveTimer: null,
  codexMode: "reply",
  codexBusy: false,
  voiceMemoRecorder: null,
  voiceMemoStream: null,
  voiceMemoChunks: [],
  voiceMemoStartedAt: 0,
  voiceMemoTimerId: 0,
  voiceMemoTarget: "",
  voiceMemoStatus: {
    reply: "",
    draft: "",
  },
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
  bulkPinButton: document.querySelector("#bulkPinButton"),
  bulkMuteButton: document.querySelector("#bulkMuteButton"),
  bulkArchiveButton: document.querySelector("#bulkArchiveButton"),
  clearSelectionButton: document.querySelector("#clearSelectionButton"),
  bulkLabelsInput: document.querySelector("#bulkLabelsInput"),
  bulkLabelButton: document.querySelector("#bulkLabelButton"),
  bulkRemoveLabelButton: document.querySelector("#bulkRemoveLabelButton"),
  bulkFollowUpAt: document.querySelector("#bulkFollowUpAt"),
  bulkSetFollowUpButton: document.querySelector("#bulkSetFollowUpButton"),
  bulkClearFollowUpButton: document.querySelector("#bulkClearFollowUpButton"),
  bulkClearDraftsButton: document.querySelector("#bulkClearDraftsButton"),
  conversationList: document.querySelector("#conversationList"),
  contactRefreshButton: document.querySelector("#contactRefreshButton"),
  contactSearch: document.querySelector("#contactSearch"),
  contactSourceFilters: document.querySelector("#contactSourceFilters"),
  contactSelectVisibleButton: document.querySelector("#contactSelectVisibleButton"),
  contactAddVisibleButton: document.querySelector("#contactAddVisibleButton"),
  contactCopyVisibleButton: document.querySelector("#contactCopyVisibleButton"),
  contactSaveVisibleButton: document.querySelector("#contactSaveVisibleButton"),
  contactClearSelectedButton: document.querySelector("#contactClearSelectedButton"),
  contactStatus: document.querySelector("#contactStatus"),
  contactList: document.querySelector("#contactList"),
  threadProvider: document.querySelector("#threadProvider"),
  threadTitle: document.querySelector("#threadTitle"),
  syncButton: document.querySelector("#syncButton"),
  pinButton: document.querySelector("#pinButton"),
  muteButton: document.querySelector("#muteButton"),
  archiveButton: document.querySelector("#archiveButton"),
  markReadButton: document.querySelector("#markReadButton"),
  markUnreadButton: document.querySelector("#markUnreadButton"),
  connectionButton: document.querySelector("#connectionButton"),
  copyThreadButton: document.querySelector("#copyThreadButton"),
  threadStatus: document.querySelector("#threadStatus"),
  threadPeopleState: document.querySelector("#threadPeopleState"),
  threadPeopleAddAllButton: document.querySelector("#threadPeopleAddAllButton"),
  threadPeopleSaveListButton: document.querySelector("#threadPeopleSaveListButton"),
  threadPeople: document.querySelector("#threadPeople"),
  threadMediaState: document.querySelector("#threadMediaState"),
  mediaFilters: document.querySelector("#mediaFilters"),
  threadMedia: document.querySelector("#threadMedia"),
  threadLocalTitle: document.querySelector("#threadLocalTitle"),
  threadFollowUpAt: document.querySelector("#threadFollowUpAt"),
  threadTags: document.querySelector("#threadTags"),
  threadNote: document.querySelector("#threadNote"),
  saveManagementButton: document.querySelector("#saveManagementButton"),
  managementState: document.querySelector("#managementState"),
  globalMessageSearch: document.querySelector("#globalMessageSearch"),
  globalMessageSearchFilters: document.querySelector("#globalMessageSearchFilters"),
  messageDateFrom: document.querySelector("#messageDateFrom"),
  messageDateTo: document.querySelector("#messageDateTo"),
  clearMessageDatesButton: document.querySelector("#clearMessageDatesButton"),
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
  voiceMemoButton: document.querySelector("#voiceMemoButton"),
  voiceMemoTimer: document.querySelector("#voiceMemoTimer"),
  voiceMemoStatus: document.querySelector("#voiceMemoStatus"),
  attachmentDrop: document.querySelector("#attachmentDrop"),
  fileInput: document.querySelector("#fileInput"),
  attachmentList: document.querySelector("#attachmentList"),
  sendButton: document.querySelector("#sendButton"),
  clearButton: document.querySelector("#clearButton"),
  sendState: document.querySelector("#sendState"),
  draftState: document.querySelector("#draftState"),
  draftRecipients: document.querySelector("#draftRecipients"),
  draftRecipientChips: document.querySelector("#draftRecipientChips"),
  recipientListName: document.querySelector("#recipientListName"),
  saveRecipientListButton: document.querySelector("#saveRecipientListButton"),
  recipientLists: document.querySelector("#recipientLists"),
  draftMessage: document.querySelector("#draftMessage"),
  draftEmojiRow: document.querySelector("#draftEmojiRow"),
  draftVoiceMemoRow: document.querySelector("#draftVoiceMemoRow"),
  draftVoiceMemoButton: document.querySelector("#draftVoiceMemoButton"),
  draftVoiceMemoTimer: document.querySelector("#draftVoiceMemoTimer"),
  draftVoiceMemoStatus: document.querySelector("#draftVoiceMemoStatus"),
  draftAttachmentDrop: document.querySelector("#draftAttachmentDrop"),
  draftFileInput: document.querySelector("#draftFileInput"),
  draftAttachmentList: document.querySelector("#draftAttachmentList"),
  draftPreview: document.querySelector("#draftPreview"),
  draftPreviewTitle: document.querySelector("#draftPreviewTitle"),
  draftPreviewText: document.querySelector("#draftPreviewText"),
  copyDraftRecipientsButton: document.querySelector("#copyDraftRecipientsButton"),
  copyDraftBodyButton: document.querySelector("#copyDraftBodyButton"),
  copyDraftPreviewButton: document.querySelector("#copyDraftPreviewButton"),
  openAddressedDraftButton: document.querySelector("#openAddressedDraftButton"),
  draftCopyToggle: document.querySelector("#draftCopyToggle"),
  draftOpenToggle: document.querySelector("#draftOpenToggle"),
  draftOpenAttachmentsToggle: document.querySelector("#draftOpenAttachmentsToggle"),
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
  codexAnswer: document.querySelector("#codexAnswer"),
  buildPromptButton: document.querySelector("#buildPromptButton"),
  copyPromptButton: document.querySelector("#copyPromptButton"),
  askCodexButton: document.querySelector("#askCodexButton"),
  copyCodexAnswerButton: document.querySelector("#copyCodexAnswerButton"),
  useCodexDraftButton: document.querySelector("#useCodexDraftButton"),
};

const emojiChoices = ["👍", "🙏", "🔥", "❤️", "😂", "👀", "✅", "🤔", "😭", "🚀"];

const messageViews = [
  { key: "all", label: "All" },
  { key: "starred", label: "Starred" },
  { key: "noted", label: "Noted" },
  { key: "unread", label: "Unread" },
  { key: "files", label: "Files" },
  { key: "audio", label: "Audio" },
  { key: "mine", label: "Mine" },
];

const contactSources = [
  { key: "all", label: "All" },
  { key: "favorites", label: "Favorites" },
  { key: "noted", label: "Noted" },
  { key: "contacts", label: "Saved" },
  { key: "participants", label: "Unsaved" },
];

const conversationViewLabels = {
  inbox: "Inbox",
  needsReply: "Needs reply",
  followup: "Follow-up",
  unread: "Unread",
  drafts: "Drafts",
  unlabeled: "Unlabeled",
  muted: "Muted",
  pinned: "Pinned",
  archived: "Archived",
  all: "All",
};

const messageSearchViews = [
  { key: "all", label: "All" },
  { key: "recent", label: "Recent" },
  { key: "current", label: "This thread" },
  { key: "unread", label: "Unread" },
  { key: "starred", label: "Starred" },
  { key: "noted", label: "Noted" },
  { key: "files", label: "Files" },
  { key: "audio", label: "Audio" },
  { key: "mine", label: "Mine" },
];

const mediaViews = [
  { key: "all", label: "All" },
  { key: "images", label: "Images" },
  { key: "audio", label: "Audio" },
  { key: "files", label: "Files" },
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

function contactMatchesHandle(contact, handle) {
  const key = recipientCompareKey(handle);
  if (!contact || !key) return false;
  const values = [
    contact.primary_handle,
    contact.phone,
    contact.phone_normalized,
    contact.email,
  ].filter(Boolean);
  if (handleType(handle) === "phone") {
    const handleDigits = digitsOnly(handle);
    const handleTail = handleDigits.length > 10 ? handleDigits.slice(-10) : handleDigits;
    return values.some((value) => {
      const digits = digitsOnly(value);
      if (digits.length < 7 || handleTail.length < 7) return false;
      const digitsTail = digits.length > 10 ? digits.slice(-10) : digits;
      return digits === handleDigits || digitsTail === handleTail || digits.includes(handleDigits) || handleDigits.includes(digits);
    });
  }
  return values.some((value) => recipientCompareKey(value) === key);
}

function bestContactForHandle(handle, contacts) {
  const matches = (contacts || []).filter((contact) => contactMatchesHandle(contact, handle));
  return matches.find((contact) => contact.is_saved !== false) || matches[0] || null;
}

function threadContactKey(handle) {
  return recipientCompareKey(handle);
}

function threadContactMatch(handle) {
  return state.threadContactMatches[threadContactKey(handle)] || null;
}

function contactManagementKeyForHandle(value) {
  const text = String(value || "").trim().toLowerCase();
  if (!text) return "";
  if (text.includes("@")) return `email:${text}`;
  const digits = digitsOnly(text);
  if (digits.length >= 7) return `phone:${digits}`;
  return `handle:${text}`;
}

function resetThreadContactMatches() {
  state.threadContactMatches = {};
  state.threadContactToken += 1;
}

function handleType(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  if (text.includes("@")) return "email";
  if (digitsOnly(text).length >= 7) return "phone";
  return "handle";
}

function participantValuesForConversation(conversation) {
  const raw = conversation?.participants;
  if (Array.isArray(raw)) return raw;
  if (typeof raw !== "string") return [];
  const text = raw.trim();
  if (!text) return [];
  try {
    const parsed = JSON.parse(text);
    if (Array.isArray(parsed)) return parsed;
  } catch (_error) {
    return splitValues(text);
  }
  return [];
}

function conversationParticipants(conversation = state.selected) {
  if (!conversation) return [];
  const values = participantValuesForConversation(conversation);
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

async function loadThreadContactMatches(conversation = state.selected) {
  if (!conversation) return;
  const participants = conversationParticipants(conversation);
  const token = state.threadContactToken + 1;
  state.threadContactToken = token;
  if (!participants.length) {
    state.threadContactMatches = {};
    renderThreadPeople();
    buildCodexPrompt();
    return;
  }

  const entries = await Promise.all(participants.map(async (participant) => {
    const key = threadContactKey(participant.handle);
    if (!key) return null;
    try {
      const payload = await api(`/penguin-connect/contacts?search=${encodeURIComponent(participant.handle)}&limit=5&source=all`);
      const match = bestContactForHandle(participant.handle, payload.contacts || []);
      return match ? [key, match] : null;
    } catch (_error) {
      return null;
    }
  }));

  if (token !== state.threadContactToken || state.selected?.conversation_id !== conversation.conversation_id) return;
  const matches = {};
  for (const entry of entries) {
    if (entry) matches[entry[0]] = entry[1];
  }
  state.threadContactMatches = matches;
  renderThreadPeople();
  buildCodexPrompt();
}

function conversationHaystack(conversation) {
  const contactContext = Array.isArray(conversation.contact_context)
    ? conversation.contact_context.flatMap((contact) => [
      contact.display_name,
      contact.primary_handle,
      contact.organization,
      contact.contact_note,
    ])
    : [];
  const raw = [
    conversation.conversation_id,
    conversation.title,
    conversation.display_name,
    conversation.source_provider,
    conversation.source_chat_identifier,
    conversation.alias_email,
    conversation.last_message_sender,
    conversation.last_message_preview,
    conversation.note,
    conversation.draft_text,
    conversation.follow_up_at,
    conversation.contact_context_text,
    ...(conversation.labels || []),
    ...participantValuesForConversation(conversation),
    ...contactContext,
  ].join(" ").toLowerCase();
  return `${raw} ${digitsOnly(raw)}`;
}

function conversationDisplayName(conversation) {
  return String(conversation?.title || conversation?.display_name || conversation?.conversation_id || "Conversation").trim() || "Conversation";
}

function sourceDisplayName(conversation) {
  return String(conversation?.display_name || conversation?.conversation_id || "Conversation").trim() || "Conversation";
}

function renderThreadHeader() {
  if (!state.selected) {
    el.threadProvider.textContent = "No thread selected";
    el.threadTitle.textContent = "Select a conversation";
    return;
  }
  el.threadProvider.textContent = [
    state.selected.source_provider,
    state.selected.source_service_name,
    state.selected.chat_type,
  ].filter(Boolean).join(" · ");
  el.threadTitle.textContent = conversationDisplayName(state.selected);
}

function labelsForConversation(conversation) {
  return Array.isArray(conversation?.labels) ? conversation.labels : [];
}

function cleanBulkLabels(value) {
  const seen = new Set();
  const labels = [];
  for (const raw of splitValues(value)) {
    const label = raw.replace(/^#+/, "").replace(/\s+/g, " ").trim().slice(0, 32);
    const key = labelKey(label);
    if (!label || seen.has(key)) continue;
    seen.add(key);
    labels.push(label);
    if (labels.length >= 12) break;
  }
  return labels;
}

function mergeConversationLabels(conversation, additions) {
  const seen = new Set();
  const labels = [];
  for (const label of [...labelsForConversation(conversation), ...(additions || [])]) {
    const clean = String(label || "").replace(/^#+/, "").replace(/\s+/g, " ").trim().slice(0, 32);
    const key = labelKey(clean);
    if (!clean || seen.has(key)) continue;
    seen.add(key);
    labels.push(clean);
    if (labels.length >= 12) break;
  }
  return labels;
}

function removeConversationLabels(conversation, removals) {
  const removeKeys = new Set((removals || []).map(labelKey).filter(Boolean));
  if (!removeKeys.size) return labelsForConversation(conversation);
  return labelsForConversation(conversation).filter((label) => !removeKeys.has(labelKey(label)));
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

function conversationHasDraft(conversation) {
  return Boolean(draftTextForConversation(conversation).trim());
}

function conversationHasLabels(conversation) {
  return labelsForConversation(conversation).length > 0;
}

function conversationNeedsReply(conversation) {
  const direction = String(conversation?.last_message_direction || "").toLowerCase();
  return Boolean(conversation?.last_message_ts) && !["manual_to_imessage", "email_to_imessage"].includes(direction);
}

function followUpValue(conversation) {
  return String(conversation?.follow_up_at || "").trim();
}

function hasFollowUp(conversation) {
  return Boolean(followUpValue(conversation));
}

function followUpInputValue(conversation) {
  const value = followUpValue(conversation);
  return value.length >= 16 ? value.slice(0, 16) : value;
}

function followUpSortValue(conversation) {
  const raw = followUpValue(conversation);
  if (!raw) return Number.MAX_SAFE_INTEGER;
  const value = Date.parse(raw);
  return Number.isNaN(value) ? Number.MAX_SAFE_INTEGER : value;
}

function followUpLabel(conversation) {
  const raw = followUpValue(conversation);
  return raw ? formatTime(raw) : "";
}

function followUpStatus(conversation) {
  const raw = followUpValue(conversation);
  if (!raw) return "";
  const value = Date.parse(raw);
  if (Number.isNaN(value)) return "scheduled";
  return value <= Date.now() ? "due" : "scheduled";
}

function conversationSortValue(conversation) {
  const raw = conversation.last_message_ts || conversation.updated_at || conversation.management_updated_at || "";
  const value = Date.parse(raw);
  return Number.isNaN(value) ? 0 : value;
}

function conversationMatchesView(conversation, view = state.conversationView) {
  if (view === "unread") return Number(conversation.unread_count || 0) > 0 && !conversation.is_archived && !conversation.is_muted;
  if (view === "needsReply") return conversationNeedsReply(conversation) && !conversation.is_archived && !conversation.is_muted;
  if (view === "followup") return hasFollowUp(conversation) && !conversation.is_archived;
  if (view === "drafts") return conversationHasDraft(conversation) && !conversation.is_archived;
  if (view === "unlabeled") return !conversationHasLabels(conversation) && !conversation.is_archived;
  if (view === "muted") return Boolean(conversation.is_muted) && !conversation.is_archived;
  if (view === "pinned") return Boolean(conversation.is_pinned) && !conversation.is_archived;
  if (view === "archived") return Boolean(conversation.is_archived);
  if (view === "all") return true;
  return !conversation.is_archived && !conversation.is_muted;
}

function conversationMatchesLabel(conversation, label = state.conversationLabel) {
  const selected = labelKey(label);
  if (!selected) return true;
  return labelsForConversation(conversation).some((value) => labelKey(value) === selected);
}

function conversationViewCounts() {
  return {
    inbox: state.conversations.filter((conversation) => conversationMatchesView(conversation, "inbox")).length,
    needsReply: state.conversations.filter((conversation) => conversationMatchesView(conversation, "needsReply")).length,
    followup: state.conversations.filter((conversation) => conversationMatchesView(conversation, "followup")).length,
    unread: state.conversations.filter((conversation) => conversationMatchesView(conversation, "unread")).length,
    drafts: state.conversations.filter((conversation) => conversationMatchesView(conversation, "drafts")).length,
    unlabeled: state.conversations.filter((conversation) => conversationMatchesView(conversation, "unlabeled")).length,
    muted: state.conversations.filter((conversation) => conversationMatchesView(conversation, "muted")).length,
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
    const label = conversationViewLabels[view] || (view ? view.charAt(0).toUpperCase() + view.slice(1) : "View");
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
    if (state.conversationView === "followup") {
      const followUpDiff = followUpSortValue(a) - followUpSortValue(b);
      if (followUpDiff) return followUpDiff;
    }
    const pinnedDiff = Number(b.is_pinned) - Number(a.is_pinned);
    if (pinnedDiff) return pinnedDiff;
    return conversationSortValue(b) - conversationSortValue(a);
  });
}

function selectedConversations() {
  return state.conversations.filter((conversation) => state.selectedConversationIds.has(conversation.conversation_id));
}

function shouldBulkArchive(targets = selectedConversations()) {
  if (state.conversationView === "archived") return false;
  if (!targets.length) return true;
  return !targets.every((conversation) => conversation.is_archived);
}

function shouldBulkPin(targets = selectedConversations()) {
  if (state.conversationView === "pinned") return false;
  if (!targets.length) return true;
  return !targets.every((conversation) => conversation.is_pinned);
}

function shouldBulkMute(targets = selectedConversations()) {
  if (state.conversationView === "muted") return false;
  if (!targets.length) return true;
  return !targets.every((conversation) => conversation.is_muted);
}

function conversationHasUnread(conversation) {
  return Boolean(conversation.has_unread) || Number(conversation.unread_count || 0) > 0;
}

function shouldBulkMarkUnread(targets = selectedConversations()) {
  if (state.conversationView === "unread") return false;
  if (!targets.length) return false;
  return targets.every((conversation) => !conversationHasUnread(conversation));
}

function pruneSelectedConversations() {
  const ids = new Set(state.conversations.map((conversation) => conversation.conversation_id));
  for (const selectedId of state.selectedConversationIds) {
    if (!ids.has(selectedId)) state.selectedConversationIds.delete(selectedId);
  }
}

function renderBulkActions(rows) {
  const selectedRows = selectedConversations();
  const selectedCount = selectedRows.length;
  const visibleCount = rows.length;
  const allVisibleSelected = visibleCount > 0 && rows.every((conversation) => state.selectedConversationIds.has(conversation.conversation_id));
  const labelCount = cleanBulkLabels(el.bulkLabelsInput.value).length;
  const bulkFollowUpValue = el.bulkFollowUpAt.value.trim();
  const selectedDraftCount = selectedRows.filter(conversationHasDraft).length;
  const markUnreadIntent = shouldBulkMarkUnread(selectedRows);
  const pinIntent = shouldBulkPin();
  const muteIntent = shouldBulkMute();
  const archiveIntent = shouldBulkArchive();
  el.bulkState.textContent = state.bulkBusy ? "Updating selected" : (state.bulkMessage || `${selectedCount} selected`);
  el.selectVisibleButton.disabled = state.bulkBusy || !visibleCount || allVisibleSelected;
  el.bulkMarkReadButton.textContent = markUnreadIntent ? "Mark unread" : "Mark read";
  el.bulkMarkReadButton.title = markUnreadIntent ? "Mark selected conversations unread" : "Mark selected conversations read";
  el.bulkMarkReadButton.setAttribute("aria-label", el.bulkMarkReadButton.title);
  el.bulkMarkReadButton.disabled = state.bulkBusy || selectedCount === 0;
  el.bulkPinButton.textContent = pinIntent ? "Pin" : "Unpin";
  el.bulkPinButton.title = pinIntent ? "Pin selected conversations" : "Unpin selected conversations";
  el.bulkPinButton.setAttribute("aria-label", el.bulkPinButton.title);
  el.bulkPinButton.disabled = state.bulkBusy || selectedCount === 0;
  el.bulkMuteButton.textContent = muteIntent ? "Mute" : "Unmute";
  el.bulkMuteButton.title = muteIntent ? "Mute selected conversations" : "Unmute selected conversations";
  el.bulkMuteButton.setAttribute("aria-label", el.bulkMuteButton.title);
  el.bulkMuteButton.disabled = state.bulkBusy || selectedCount === 0;
  el.bulkArchiveButton.textContent = archiveIntent ? "Archive" : "Restore";
  el.bulkArchiveButton.title = archiveIntent ? "Archive selected conversations" : "Restore selected conversations";
  el.bulkArchiveButton.setAttribute("aria-label", el.bulkArchiveButton.title);
  el.bulkArchiveButton.disabled = state.bulkBusy || selectedCount === 0;
  el.bulkLabelButton.disabled = state.bulkBusy || selectedCount === 0 || labelCount === 0;
  el.bulkRemoveLabelButton.disabled = state.bulkBusy || selectedCount === 0 || labelCount === 0;
  el.bulkSetFollowUpButton.disabled = state.bulkBusy || selectedCount === 0 || !bulkFollowUpValue;
  el.bulkClearFollowUpButton.disabled = state.bulkBusy || selectedCount === 0;
  el.bulkClearDraftsButton.disabled = state.bulkBusy || selectedDraftCount === 0;
  el.bulkClearDraftsButton.title = selectedDraftCount
    ? `Clear ${selectedDraftCount} selected reply draft${selectedDraftCount === 1 ? "" : "s"}`
    : "Select conversations with reply drafts";
  el.bulkClearDraftsButton.setAttribute("aria-label", el.bulkClearDraftsButton.title);
  el.bulkLabelsInput.disabled = state.bulkBusy;
  el.bulkFollowUpAt.disabled = state.bulkBusy;
  el.clearSelectionButton.disabled = state.bulkBusy || selectedCount === 0;
}

function isOwnMessage(message) {
  return message.sender_name === "Me" || message.direction === "manual_to_imessage" || message.direction === "email_to_imessage";
}

function isUnreadMessage(message) {
  return message.is_read === false || message.is_read === 0;
}

function isStarredMessage(message) {
  return message.is_starred === true || message.is_starred === 1;
}

function messageNoteText(message) {
  return String(message.message_note || "").trim();
}

function hasMessageNote(message) {
  return messageNoteText(message).length > 0;
}

function messageSearchResultKey(result) {
  return `${result?.conversation_id || ""}::${result?.provider_message_id || ""}`;
}

function removeMessageSearchResultIfFiltered(result) {
  const key = messageSearchResultKey(result);
  if (key === "::") return;
  const shouldRemove = (
    (state.messageSearchView === "starred" && !isStarredMessage(result))
    || (state.messageSearchView === "noted" && !hasMessageNote(result))
  );
  if (!shouldRemove) return;
  state.messageSearchResults = state.messageSearchResults.filter((item) => messageSearchResultKey(item) !== key);
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
  if (view === "starred") return isStarredMessage(message);
  if (view === "noted") return hasMessageNote(message);
  if (view === "unread") return isUnreadMessage(message);
  if (view === "files") return attachmentRows(message).length > 0;
  if (view === "audio") return attachmentRows(message).some(isAudioAttachment);
  if (view === "mine") return isOwnMessage(message);
  return true;
}

function messageViewCounts() {
  return {
    all: state.messages.length,
    starred: state.messages.filter(isStarredMessage).length,
    noted: state.messages.filter(hasMessageNote).length,
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

function mergeMessageManagement(result) {
  const providerMessageId = result.provider_message_id || "";
  if (!providerMessageId) return;
  const hasReadState = typeof result.is_read === "boolean";
  const conversationId = result.conversation_id || state.selected?.conversation_id || "";
  state.messages = state.messages.map((item) => (
    item.provider_message_id === providerMessageId
      ? {
        ...item,
        is_starred: Boolean(result.is_starred),
        message_note: result.message_note || "",
        is_read: hasReadState ? Boolean(result.is_read) : item.is_read,
      }
      : item
  ));
  state.messageSearchResults = state.messageSearchResults.map((item) => (
    item.provider_message_id === providerMessageId
      && (!conversationId || !item.conversation_id || item.conversation_id === conversationId)
      ? {
        ...item,
        is_starred: Boolean(result.is_starred),
        message_note: result.message_note || "",
        is_read: hasReadState ? Boolean(result.is_read) : item.is_read,
      }
      : item
  ));
  const unreadCount = Number(result.unread_count);
  if (conversationId && Number.isFinite(unreadCount)) {
    updateConversationFields(conversationId, {
      unread_count: unreadCount,
      has_unread: Boolean(result.has_unread),
    });
  }
}

async function toggleMessageStar(message) {
  if (!state.selected || !message.provider_message_id) return;
  const nextStarred = !isStarredMessage(message);
  try {
    const result = await api(`/penguin-connect/conversations/${encodeURIComponent(state.selected.conversation_id)}/messages/management`, {
      method: "POST",
      body: JSON.stringify({
        provider_message_id: message.provider_message_id,
        starred: nextStarred,
      }),
    });
    mergeMessageManagement(result);
    el.sendState.textContent = result.is_starred ? "Message starred" : "Message unstarred";
    renderMessages();
    buildCodexPrompt();
  } catch (error) {
    el.sendState.textContent = error.message;
  }
}

async function toggleMessageSearchResultStar(result) {
  if (!result?.conversation_id || !result.provider_message_id) return;
  const nextStarred = !isStarredMessage(result);
  try {
    const response = await api(`/penguin-connect/conversations/${encodeURIComponent(result.conversation_id)}/messages/management`, {
      method: "POST",
      body: JSON.stringify({
        provider_message_id: result.provider_message_id,
        starred: nextStarred,
      }),
    });
    mergeMessageManagement(response);
    removeMessageSearchResultIfFiltered(response);
    el.messageSearchStatus.textContent = response.is_starred ? "Search result starred" : "Search result unstarred";
    renderMessageSearchResults();
    renderMessages();
    buildCodexPrompt();
  } catch (error) {
    el.messageSearchStatus.textContent = error.message;
  }
}

async function toggleMessageRead(message) {
  if (!state.selected || !message.provider_message_id) return;
  const nextUnread = !isUnreadMessage(message);
  try {
    const result = await api(`/penguin-connect/conversations/${encodeURIComponent(state.selected.conversation_id)}/messages/management`, {
      method: "POST",
      body: JSON.stringify({
        provider_message_id: message.provider_message_id,
        unread: nextUnread,
      }),
    });
    mergeMessageManagement(result);
    el.sendState.textContent = nextUnread ? "Message marked unread" : "Message marked read";
    renderConversations();
    renderThreadHeader();
    renderThreadControls();
    renderMessages();
    buildCodexPrompt();
  } catch (error) {
    el.sendState.textContent = error.message;
  }
}

function editMessageNote(message) {
  if (!message.provider_message_id) return;
  state.messageNoteEditorId = message.provider_message_id;
  renderMessages();
}

function editMessageSearchResultNote(result) {
  if (!result?.conversation_id || !result.provider_message_id) return;
  state.messageSearchNoteEditorId = messageSearchResultKey(result);
  renderMessageSearchResults();
}

async function saveMessageNote(message, noteValue) {
  if (!state.selected || !message.provider_message_id) return;
  try {
    const result = await api(`/penguin-connect/conversations/${encodeURIComponent(state.selected.conversation_id)}/messages/management`, {
      method: "POST",
      body: JSON.stringify({
        provider_message_id: message.provider_message_id,
        note: noteValue,
      }),
    });
    mergeMessageManagement(result);
    state.messageNoteEditorId = "";
    el.sendState.textContent = result.has_note ? "Message note saved" : "Message note cleared";
    renderMessages();
    buildCodexPrompt();
  } catch (error) {
    el.sendState.textContent = error.message;
  }
}

async function saveMessageSearchResultNote(result, noteValue) {
  if (!result?.conversation_id || !result.provider_message_id) return;
  try {
    const response = await api(`/penguin-connect/conversations/${encodeURIComponent(result.conversation_id)}/messages/management`, {
      method: "POST",
      body: JSON.stringify({
        provider_message_id: result.provider_message_id,
        note: noteValue,
      }),
    });
    mergeMessageManagement(response);
    removeMessageSearchResultIfFiltered(response);
    state.messageSearchNoteEditorId = "";
    el.messageSearchStatus.textContent = response.has_note ? "Search result note saved" : "Search result note cleared";
    renderMessageSearchResults();
    renderMessages();
    buildCodexPrompt();
  } catch (error) {
    el.messageSearchStatus.textContent = error.message;
  }
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

function renderEmojiButtons(target = "reply") {
  const row = target === "draft" ? el.draftEmojiRow : el.emojiRow;
  const textarea = target === "draft" ? el.draftMessage : el.composer;
  row.replaceChildren();
  for (const emoji of emojiChoices) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "emoji-button";
    button.textContent = emoji;
    button.title = `Insert ${emoji}`;
    button.addEventListener("click", () => {
      const start = textarea.selectionStart ?? textarea.value.length;
      const end = textarea.selectionEnd ?? textarea.value.length;
      textarea.value = `${textarea.value.slice(0, start)}${emoji}${textarea.value.slice(end)}`;
      textarea.focus();
      textarea.selectionStart = start + emoji.length;
      textarea.selectionEnd = start + emoji.length;
      if (target === "draft") {
        renderDraftPreview();
      } else {
        buildCodexPrompt();
      }
    });
    row.append(button);
  }
}

function renderAllEmojiButtons() {
  renderEmojiButtons("reply");
  renderEmojiButtons("draft");
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
    mainButton.querySelector(".conversation-name").textContent = conversationDisplayName(conversation);
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
    if (conversation.is_muted) {
      const badge = document.createElement("span");
      badge.className = "badge muted-badge";
      badge.textContent = "muted";
      badges.append(badge);
    }
    if (draftTextForConversation(conversation).trim()) {
      const badge = document.createElement("span");
      badge.className = "badge draft-badge";
      badge.textContent = "draft";
      badges.append(badge);
    }
    if (hasFollowUp(conversation)) {
      const badge = document.createElement("span");
      const status = followUpStatus(conversation);
      badge.className = `badge followup-badge ${status}`;
      badge.textContent = `follow-up ${followUpLabel(conversation)}`;
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
  return findConversationsForContact(contact, 1)[0] || null;
}

function findConversationsForContact(contact, limit = 4) {
  const needles = contactNeedles(contact);
  const matches = state.conversations.filter((conversation) => {
    const haystack = conversationHaystack(conversation);
    return needles.some((needle) => haystack.includes(needle));
  });
  matches.sort((a, b) => conversationSortValue(b) - conversationSortValue(a));
  return limit ? matches.slice(0, limit) : matches;
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

function buildMessagesDraftText(participants = uniqueRecipientValues(draftRecipientValues()), message = el.draftMessage.value) {
  if (!participants.length) return "";
  const body = String(message || "").trim().slice(0, 50000);
  return body ? `To: ${participants.join(", ")}\n\n${body}\n` : `To: ${participants.join(", ")}\n`;
}

function draftRecipientLine(values = uniqueRecipientValues(draftRecipientValues())) {
  return uniqueRecipientValues(values).join(", ");
}

function draftBodyText() {
  return String(el.draftMessage.value || "").trim().slice(0, 50000);
}

function renderDraftPreview(values = uniqueRecipientValues(draftRecipientValues()), draftText = "") {
  const recipients = uniqueRecipientValues(values);
  const body = draftBodyText();
  const draft = draftText || buildMessagesDraftText(recipients);
  const attachments = state.draftAttachments || [];
  const count = recipients.length;
  const mode = count > 1 ? "Group chat" : "Direct chat";
  el.draftPreviewTitle.textContent = count
    ? `${mode} · ${count} recipient${count === 1 ? "" : "s"}${attachments.length ? ` · ${attachments.length} file${attachments.length === 1 ? "" : "s"}` : ""}`
    : "No recipients";
  const attachmentLines = attachments.length
    ? [
      "",
      `Attachments staged separately: ${attachments.map((file) => file.name || "attachment").join(", ")}`,
      state.draftAttachmentFolder ? `Folder: ${state.draftAttachmentFolder}` : "",
    ].filter(Boolean).join("\n")
    : "";
  el.draftPreviewText.textContent = (draft ? `${draft}${attachmentLines}` : "") || "Add recipients to preview the Messages draft.";
  el.copyDraftRecipientsButton.disabled = !recipients.length;
  el.copyDraftBodyButton.disabled = !body;
  el.copyDraftPreviewButton.disabled = !draft;
  el.openAddressedDraftButton.disabled = !recipients.length;
}

async function copyDraftPreview() {
  const participants = setDraftRecipients(draftRecipientValues());
  const draft = buildMessagesDraftText(participants);
  if (!draft) {
    el.draftState.textContent = "Add recipient";
    el.draftRecipients.focus();
    return;
  }

  try {
    await copyText(draft);
    renderDraftPreview(participants, draft);
    el.draftState.textContent = "Draft copied";
  } catch (error) {
    el.draftState.textContent = error.message;
  }
}

async function copyDraftRecipients() {
  const participants = setDraftRecipients(draftRecipientValues());
  const line = draftRecipientLine(participants);
  if (!line) {
    el.draftState.textContent = "Add recipient";
    el.draftRecipients.focus();
    return;
  }

  try {
    await copyText(line);
    el.draftState.textContent = `${participants.length} recipient${participants.length === 1 ? "" : "s"} copied`;
  } catch (error) {
    el.draftState.textContent = error.message;
  }
}

async function copyDraftBody() {
  const body = draftBodyText();
  if (!body) {
    el.draftState.textContent = "Add message";
    el.draftMessage.focus();
    return;
  }

  try {
    await copyText(body);
    el.draftState.textContent = "Message body copied";
  } catch (error) {
    el.draftState.textContent = error.message;
  }
}

async function openAddressedDraft() {
  const participants = setDraftRecipients(draftRecipientValues());
  if (!participants.length) {
    el.draftState.textContent = "Add recipient";
    el.draftRecipients.focus();
    return;
  }

  el.openAddressedDraftButton.disabled = true;
  el.draftState.textContent = "Opening addressed chat";
  try {
    const attachments = await filesAsBrowserAttachments(state.draftAttachments);
    const result = await api("/penguin-connect/messages/draft", {
      method: "POST",
      body: JSON.stringify({
        participants,
        message: el.draftMessage.value,
        attachments,
        copy_to_clipboard: false,
        open_messages: false,
        open_addressed: true,
        open_attachments: el.draftOpenAttachmentsToggle.checked,
      }),
    });
    state.draftAttachmentFolder = result.attachment_folder || "";
    state.draftAttachmentPaths = result.attachment_paths || [];
    renderDraftPreview(result.participants || participants, result.draft || "");
    const actions = [
      result.opened_addressed ? "addressed chat opened" : "",
      result.opened_attachments ? "files opened" : result.attachment_count ? "files staged" : "",
    ].filter(Boolean).join(" + ");
    el.draftState.textContent = actions || "Address ready";
  } catch (error) {
    el.draftState.textContent = error.message;
  } finally {
    renderDraftPreview(participants);
  }
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
  renderDraftPreview(recipients);
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

function visibleContactRecipientHandles() {
  return uniqueRecipientValues(state.contacts.map(contactRecipientHandle));
}

function contactSelectionKey(contact) {
  const key = contact.contact_key || contact.favorite_contact_key || contact.note_contact_key || "";
  if (key) return key;
  const handle = contactRecipientHandle(contact);
  const compareKey = recipientCompareKey(handle);
  return compareKey ? `handle:${compareKey}` : "";
}

function visibleContactSelectionKeys() {
  return new Set(state.contacts.map(contactSelectionKey).filter(Boolean));
}

function isContactSelected(contact) {
  const key = contactSelectionKey(contact);
  return Boolean(key && state.selectedContactKeys.has(key));
}

function selectedContactRecipientHandles() {
  return uniqueRecipientValues(
    state.contacts
      .filter(isContactSelected)
      .map(contactRecipientHandle)
  );
}

function contactBulkRecipientHandles() {
  const selected = selectedContactRecipientHandles();
  return selected.length ? selected : visibleContactRecipientHandles();
}

function pruneSelectedContacts() {
  const visibleKeys = visibleContactSelectionKeys();
  state.selectedContactKeys = new Set(
    [...state.selectedContactKeys].filter((key) => visibleKeys.has(key))
  );
}

function contactBulkListName() {
  const query = el.contactSearch.value.trim();
  const source = contactSourceLabel();
  const selected = selectedContactRecipientHandles().length;
  const label = selected ? "Selected" : source;
  return query ? `${label}: ${query}` : `${label} contacts`;
}

function renderContactBulkActions() {
  const selectedCount = selectedContactRecipientHandles().length;
  const visibleCount = visibleContactRecipientHandles().length;
  const hasRecipients = selectedCount ? selectedCount > 0 : visibleCount > 0;
  el.contactSelectVisibleButton.disabled = visibleCount === 0;
  el.contactAddVisibleButton.disabled = !hasRecipients;
  el.contactCopyVisibleButton.disabled = !hasRecipients;
  el.contactSaveVisibleButton.disabled = !hasRecipients;
  el.contactClearSelectedButton.disabled = selectedCount === 0;
  el.contactAddVisibleButton.textContent = selectedCount ? "Add selected" : "Add visible";
  el.contactCopyVisibleButton.textContent = selectedCount ? "Copy selected" : "Copy visible";
  el.contactSaveVisibleButton.textContent = selectedCount ? "Save selected" : "Save visible";
  el.contactClearSelectedButton.textContent = selectedCount ? `Clear ${selectedCount}` : "Clear selected";
}

function toggleContactSelection(contact) {
  const key = contactSelectionKey(contact);
  if (!key || !contactRecipientHandle(contact)) {
    el.contactStatus.textContent = "No phone or email on contact";
    return;
  }
  if (state.selectedContactKeys.has(key)) {
    state.selectedContactKeys.delete(key);
  } else {
    state.selectedContactKeys.add(key);
  }
  const selectedCount = selectedContactRecipientHandles().length;
  el.contactStatus.textContent = selectedCount
    ? `${selectedCount} contact${selectedCount === 1 ? "" : "s"} selected`
    : "Contact selection cleared";
  renderContacts();
}

function selectVisibleContacts() {
  let selected = 0;
  for (const contact of state.contacts) {
    const key = contactSelectionKey(contact);
    if (!key || !contactRecipientHandle(contact)) continue;
    state.selectedContactKeys.add(key);
    selected += 1;
  }
  el.contactStatus.textContent = selected
    ? `${selected} visible contact${selected === 1 ? "" : "s"} selected`
    : "No visible handles";
  renderContacts();
}

function clearSelectedContacts() {
  state.selectedContactKeys.clear();
  el.contactStatus.textContent = "Contact selection cleared";
  renderContacts();
}

function addVisibleContactsToDraft() {
  const participants = contactBulkRecipientHandles();
  if (!participants.length) {
    el.contactStatus.textContent = "No contact handles";
    return;
  }

  const before = uniqueRecipientValues(draftRecipientValues());
  const beforeKeys = new Set(before.map(recipientCompareKey));
  const recipients = setDraftRecipients([...before, ...participants], { focus: true });
  const addedCount = recipients.filter((recipient) => !beforeKeys.has(recipientCompareKey(recipient))).length;
  const status = addedCount
    ? `${addedCount} contact${addedCount === 1 ? "" : "s"} added`
    : "Contacts already added";
  el.contactStatus.textContent = status;
  el.draftState.textContent = status;
}

async function copyVisibleContacts() {
  const participants = contactBulkRecipientHandles();
  if (!participants.length) {
    el.contactStatus.textContent = "No contact handles";
    return;
  }

  try {
    await copyText(participants.join("\n"));
    const selectedCount = selectedContactRecipientHandles().length;
    const label = selectedCount ? "selected" : "visible";
    el.contactStatus.textContent = `${participants.length} ${label} handle${participants.length === 1 ? "" : "s"} copied`;
  } catch (error) {
    el.contactStatus.textContent = error.message;
  }
}

async function saveVisibleContactsAsRecipientList() {
  const participants = contactBulkRecipientHandles();
  if (!participants.length) {
    el.contactStatus.textContent = "No contact handles";
    return;
  }

  el.contactSaveVisibleButton.disabled = true;
  const selectedCount = selectedContactRecipientHandles().length;
  el.contactStatus.textContent = selectedCount ? "Saving selected contacts" : "Saving visible contacts";
  try {
    const result = await api("/penguin-connect/recipient-lists", {
      method: "POST",
      body: JSON.stringify({
        name: contactBulkListName(),
        participants,
      }),
    });
    const saved = result.recipient_list || {};
    state.activeRecipientListId = saved.list_id || "";
    el.recipientListName.value = recipientListLabel(saved);
    setDraftRecipients(participants);
    mergeRecipientList(saved);
    renderRecipientLists();
    el.contactStatus.textContent = selectedCount ? "Selected contacts saved" : "Visible contacts saved";
    el.draftState.textContent = `${recipientListLabel(saved)} saved`;
  } catch (error) {
    el.contactStatus.textContent = error.message;
  } finally {
    renderContactBulkActions();
  }
}

function currentThreadParticipantHandles() {
  return conversationParticipants().map((participant) => participant.handle);
}

function addThreadParticipantsToDraft() {
  const participants = currentThreadParticipantHandles();
  if (!participants.length) {
    el.threadPeopleState.textContent = "No participants";
    return;
  }

  const before = uniqueRecipientValues(draftRecipientValues());
  const beforeKeys = new Set(before.map(recipientCompareKey));
  const recipients = setDraftRecipients([...before, ...participants], { focus: true });
  const addedCount = recipients.filter((recipient) => !beforeKeys.has(recipientCompareKey(recipient))).length;
  const status = addedCount
    ? `${addedCount} recipient${addedCount === 1 ? "" : "s"} added`
    : "Recipients already added";
  el.threadPeopleState.textContent = status;
  el.draftState.textContent = status;
}

function recipientListLabel(list) {
  return String(list.name || "").trim() || "Recipient list";
}

function renderRecipientLists() {
  el.recipientLists.replaceChildren();
  if (!state.recipientLists.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state compact-state";
    empty.textContent = "No saved lists";
    el.recipientLists.append(empty);
    return;
  }

  for (const list of state.recipientLists) {
    const participants = Array.isArray(list.participants) ? list.participants : [];
    const item = document.createElement("div");
    item.className = `recipient-list-item ${state.activeRecipientListId === list.list_id ? "active" : ""}`;
    item.innerHTML = `
      <button class="recipient-list-main" type="button">
        <span class="recipient-list-name"></span>
        <span class="recipient-list-meta"></span>
      </button>
      <span class="recipient-list-actions">
        <button type="button" data-action="use-list">Use</button>
        <button type="button" data-action="add-list">Add</button>
        <button type="button" data-action="delete-list">Delete</button>
      </span>
    `;
    item.querySelector(".recipient-list-name").textContent = recipientListLabel(list);
    item.querySelector(".recipient-list-meta").textContent = [
      `${participants.length} recipient${participants.length === 1 ? "" : "s"}`,
      participants.slice(0, 3).join(", "),
    ].filter(Boolean).join(" · ");
    item.querySelector(".recipient-list-main").addEventListener("click", () => useRecipientList(list));
    item.querySelector('[data-action="use-list"]').addEventListener("click", () => useRecipientList(list));
    item.querySelector('[data-action="add-list"]').addEventListener("click", () => addRecipientListToDraft(list));
    item.querySelector('[data-action="delete-list"]').addEventListener("click", () => deleteRecipientList(list));
    el.recipientLists.append(item);
  }
}

function mergeRecipientList(savedList) {
  if (!savedList?.list_id) return;
  state.recipientLists = [
    savedList,
    ...state.recipientLists.filter((list) => list.list_id !== savedList.list_id),
  ];
}

function useRecipientList(list) {
  const participants = Array.isArray(list.participants) ? list.participants : [];
  state.activeRecipientListId = list.list_id || "";
  el.recipientListName.value = recipientListLabel(list);
  setDraftRecipients(participants, { focus: true });
  el.draftState.textContent = `${recipientListLabel(list)} loaded`;
  renderRecipientLists();
}

function addRecipientListToDraft(list) {
  const participants = Array.isArray(list.participants) ? list.participants : [];
  if (!participants.length) {
    el.draftState.textContent = "List has no recipients";
    return;
  }

  const before = uniqueRecipientValues(draftRecipientValues());
  const beforeKeys = new Set(before.map(recipientCompareKey));
  const recipients = setDraftRecipients([...before, ...participants], { focus: true });
  const addedCount = recipients.filter((recipient) => !beforeKeys.has(recipientCompareKey(recipient))).length;
  state.activeRecipientListId = list.list_id || state.activeRecipientListId;
  el.recipientListName.value = recipientListLabel(list);
  el.draftState.textContent = addedCount
    ? `${addedCount} from ${recipientListLabel(list)} added`
    : `${recipientListLabel(list)} already added`;
  renderRecipientLists();
}

async function loadRecipientLists() {
  try {
    const payload = await api("/penguin-connect/recipient-lists");
    state.recipientLists = payload.recipient_lists || [];
  } catch (error) {
    state.recipientLists = [];
    el.draftState.textContent = error.message;
  }
  renderRecipientLists();
}

async function saveRecipientList() {
  const participants = setDraftRecipients(draftRecipientValues());
  if (!participants.length) {
    el.draftState.textContent = "Add recipient";
    el.draftRecipients.focus();
    return;
  }

  el.saveRecipientListButton.disabled = true;
  el.draftState.textContent = "Saving list";
  try {
    const result = await api("/penguin-connect/recipient-lists", {
      method: "POST",
      body: JSON.stringify({
        list_id: state.activeRecipientListId,
        name: el.recipientListName.value,
        participants,
      }),
    });
    const saved = result.recipient_list || {};
    state.activeRecipientListId = saved.list_id || "";
    el.recipientListName.value = recipientListLabel(saved);
    mergeRecipientList(saved);
    renderRecipientLists();
    el.draftState.textContent = "List saved";
  } catch (error) {
    el.draftState.textContent = error.message;
  } finally {
    el.saveRecipientListButton.disabled = false;
  }
}

async function saveThreadParticipantsAsRecipientList() {
  const participants = currentThreadParticipantHandles();
  if (!state.selected || !participants.length) {
    el.threadPeopleState.textContent = "No participants";
    return;
  }

  el.threadPeopleSaveListButton.disabled = true;
  el.threadPeopleState.textContent = "Saving list";
  try {
    const result = await api("/penguin-connect/recipient-lists", {
      method: "POST",
      body: JSON.stringify({
        name: conversationDisplayName(state.selected),
        participants,
      }),
    });
    const saved = result.recipient_list || {};
    state.activeRecipientListId = saved.list_id || "";
    el.recipientListName.value = recipientListLabel(saved);
    setDraftRecipients(participants);
    mergeRecipientList(saved);
    renderRecipientLists();
    el.threadPeopleState.textContent = "List saved";
    el.draftState.textContent = `${recipientListLabel(saved)} saved`;
  } catch (error) {
    el.threadPeopleState.textContent = error.message;
  } finally {
    el.threadPeopleSaveListButton.disabled = !currentThreadParticipantHandles().length;
  }
}

async function deleteRecipientList(list) {
  if (!list?.list_id) return;
  try {
    await api(`/penguin-connect/recipient-lists/${encodeURIComponent(list.list_id)}`, {
      method: "DELETE",
    });
    state.recipientLists = state.recipientLists.filter((item) => item.list_id !== list.list_id);
    if (state.activeRecipientListId === list.list_id) {
      state.activeRecipientListId = "";
      el.recipientListName.value = "";
    }
    renderRecipientLists();
    el.draftState.textContent = "List deleted";
  } catch (error) {
    el.draftState.textContent = error.message;
  }
}

function fillContactFormFromHandle(value, stateText = "Prefilled from thread") {
  const handle = String(value || "").trim();
  if (!handle) return;
  clearContactForm();
  if (handleType(handle) === "email") {
    el.newContactEmails.value = handle;
  } else {
    el.newContactPhones.value = handle;
  }
  el.createContactState.textContent = stateText;
  el.newContactFirst.focus();
}

function fillContactFormFromContact(contact) {
  const handle = contactRecipientHandle(contact);
  if (!handle) {
    el.contactStatus.textContent = "No phone or email on result";
    return;
  }
  fillContactFormFromHandle(handle, "Prefilled from search");
}

function contactHandleCandidate(value) {
  const handle = String(value || "").trim();
  const type = handleType(handle);
  return type === "phone" || type === "email" ? handle : "";
}

function messageSearchContactHandle(result) {
  const participants = participantValuesForConversation(result);
  const isMine = result.sender_name === "Me"
    || result.direction === "manual_to_imessage"
    || result.direction === "email_to_imessage"
    || Boolean(result.metadata?.is_from_me);
  const senderCandidates = isMine ? [] : [result.sender_email, result.sender_name];
  const threadCandidates = [result.source_chat_identifier, ...participants];
  const candidates = isMine ? threadCandidates : [...senderCandidates, ...threadCandidates];
  for (const candidate of candidates) {
    const handle = contactHandleCandidate(candidate);
    if (handle) return handle;
  }
  return "";
}

function fillContactFormFromMessageSearchResult(result) {
  const handle = messageSearchContactHandle(result);
  if (!handle) {
    el.messageSearchStatus.textContent = "No contact handle on result";
    return;
  }
  fillContactFormFromHandle(handle, "Prefilled from message search");
  el.messageSearchStatus.textContent = "Contact form prefilled";
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

function startContactDraft(contact) {
  const handle = contactRecipientHandle(contact);
  if (!handle) {
    el.contactStatus.textContent = "No phone or email on contact";
    return;
  }

  const added = addDraftRecipient(handle);
  const status = added ? "Started new chat draft" : "Contact already in new chat";
  el.contactStatus.textContent = status;
  el.draftState.textContent = status;
  el.draftMessage.focus();
}

async function copyContactHandle(contact) {
  const handle = contactRecipientHandle(contact);
  if (!handle) {
    el.contactStatus.textContent = "No phone or email on contact";
    return;
  }

  try {
    await copyText(handle);
    el.contactStatus.textContent = "Contact handle copied";
  } catch (error) {
    el.contactStatus.textContent = error.message;
  }
}

async function copyParticipantHandle(participant) {
  const handle = String(participant?.handle || "").trim();
  if (!handle) {
    el.threadPeopleState.textContent = "No handle";
    return;
  }

  try {
    await copyText(handle);
    el.threadPeopleState.textContent = "Participant handle copied";
  } catch (error) {
    el.threadPeopleState.textContent = error.message;
  }
}

async function useContact(contact) {
  state.focusMessageId = "";
  const match = findConversationForContact(contact);
  if (match) {
    await openContactConversation(contact, match);
  } else {
    startContactDraft(contact);
  }
}

async function openContactConversation(contact, conversation) {
  state.focusMessageId = "";
  state.conversationView = "all";
  state.conversationLabel = "";
  el.conversationSearch.value = "";
  renderConversations();
  el.contactStatus.textContent = `Opening ${conversationDisplayName(conversation)}`;
  await selectConversation(conversation);
  el.contactStatus.textContent = `Opened ${conversationDisplayName(conversation)}`;
  buildCodexPrompt();
}

function renderContactRelatedThreads(container, contact) {
  container.replaceChildren();
  const matches = findConversationsForContact(contact, 4);
  if (!matches.length) {
    container.hidden = true;
    return;
  }

  container.hidden = false;
  const label = document.createElement("span");
  label.className = "contact-related-label";
  label.textContent = matches.length === 1 ? "Thread" : "Threads";
  container.append(label);

  for (const conversation of matches) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "contact-thread-link";
    button.textContent = conversationDisplayName(conversation);
    button.title = `Open ${conversationDisplayName(conversation)}`;
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      openContactConversation(contact, conversation);
    });
    container.append(button);
  }
}

function renderThreadPeople() {
  el.threadPeople.replaceChildren();
  const participants = conversationParticipants();
  const hasParticipants = Boolean(state.selected && participants.length);
  el.threadPeopleAddAllButton.disabled = !hasParticipants;
  el.threadPeopleSaveListButton.disabled = !hasParticipants;
  const matchedCount = participants.filter((participant) => {
    const contact = threadContactMatch(participant.handle);
    return contact && contact.is_saved !== false;
  }).length;
  el.threadPeopleState.textContent = state.selected
    ? `${participants.length} participant${participants.length === 1 ? "" : "s"}${matchedCount ? ` · ${matchedCount} saved` : ""}`
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
    const contact = threadContactMatch(participant.handle);
    const managedContact = participantManagedContact(participant, contact);
    const savedContact = contact && contact.is_saved !== false;
    const favorite = isFavoriteContact(managedContact);
    const item = document.createElement("div");
    item.className = `thread-person ${savedContact ? "known-contact" : "unknown-contact"} ${favorite ? "favorite-contact" : ""}`;
    item.innerHTML = `
      <div class="thread-person-main">
        <span class="thread-person-name"></span>
        <span class="thread-person-handle"></span>
        <span class="thread-person-type"></span>
      </div>
      <div class="thread-person-actions">
        <button type="button" data-action="favorite">Star</button>
        <button type="button" data-action="search">Search</button>
        <button type="button" data-action="copy">Copy</button>
        <button type="button" data-action="draft">New chat</button>
        <button type="button" data-action="contact">Create</button>
      </div>
    `;
    item.querySelector(".thread-person-name").textContent = savedContact ? contactDisplayName(contact) : "Unknown contact";
    item.querySelector(".thread-person-handle").textContent = participant.handle;
    item.querySelector(".thread-person-type").textContent = contact
      ? `${participant.type} · ${contactHandleText(contact)}`
      : participant.type;
    const favoriteButton = item.querySelector('[data-action="favorite"]');
    favoriteButton.textContent = favorite ? "Unstar" : "Star";
    favoriteButton.classList.toggle("active", favorite);
    favoriteButton.disabled = !managedContact.contact_key;
    favoriteButton.addEventListener("click", () => toggleThreadParticipantFavorite(participant, contact));
    item.querySelector('[data-action="search"]').addEventListener("click", () => searchContactHandle(participant.handle));
    item.querySelector('[data-action="copy"]').addEventListener("click", () => copyParticipantHandle(participant));
    item.querySelector('[data-action="draft"]').addEventListener("click", () => addParticipantToDraft(participant.handle));
    const contactButton = item.querySelector('[data-action="contact"]');
    contactButton.textContent = savedContact ? "Saved" : "Create";
    contactButton.disabled = Boolean(savedContact);
    contactButton.addEventListener("click", () => fillContactFormFromHandle(participant.handle));
    el.threadPeople.append(item);
  }
}

function conversationFromSearchResult(result) {
  return state.conversations.find((conversation) => conversation.conversation_id === result.conversation_id) || {
    conversation_id: result.conversation_id,
    title: result.title || "",
    display_name: result.display_name || "Conversation",
    source_provider: result.source_provider || result.provider || "imessage",
    source_service_name: result.source_service_name || "",
    chat_type: result.chat_type || "chat",
    participants: [],
  };
}

async function refreshConversationsForSearchResult(result) {
  const conversationId = result?.conversation_id || "";
  if (!conversationId || state.conversations.some((conversation) => conversation.conversation_id === conversationId)) {
    return conversationFromSearchResult(result);
  }

  el.messageSearchStatus.textContent = "Loading imported thread";
  await loadConversations({ autoSelect: false });
  return conversationFromSearchResult(result);
}

async function useMessageSearchResult(result) {
  state.focusMessageId = result.provider_message_id || "";
  el.conversationSearch.value = "";
  renderConversations();
  const conversation = await refreshConversationsForSearchResult(result);
  await selectConversation(conversation);
}

async function replyToMessageSearchResult(result) {
  await useMessageSearchResult(result);
  setReplyContext(result);
  el.sendState.textContent = "Reply target set from search";
}

function searchResultConversationName(result) {
  const conversation = state.conversations.find((conversation) => conversation.conversation_id === result.conversation_id);
  if (conversation) return conversationDisplayName(conversation);
  return String(result.title || result.display_name || result.conversation_id || "Conversation").trim() || "Conversation";
}

function contactSourceLabel() {
  return (contactSources.find((source) => source.key === state.contactSource) || contactSources[0]).label;
}

function isFavoriteContact(contact) {
  return contact.is_favorite === true || contact.is_favorite === 1;
}

function contactNoteText(contact) {
  return String(contact.contact_note || "").trim();
}

function contactManagementKeyMatches(contact, contactKey) {
  const key = String(contactKey || "").trim();
  if (!key) return false;
  if (contact.contact_key === key || contact.favorite_contact_key === key || contact.note_contact_key === key) return true;
  return Array.isArray(contact.contact_keys) && contact.contact_keys.includes(key);
}

function contactFavoriteManagementKey(contact) {
  return (isFavoriteContact(contact) && contact.favorite_contact_key) || contact.contact_key || "";
}

function contactNoteManagementKey(contact) {
  return contact.note_contact_key || contact.contact_key || "";
}

function mergeContactManagement(result, { updatedFavorite = true, updatedNote = true } = {}) {
  const contactKey = result.contact_key || "";
  if (!contactKey) return;
  state.contacts = state.contacts.map((contact) => (
    contactManagementKeyMatches(contact, contactKey)
      ? {
        ...contact,
        contact_key: updatedNote && String(result.contact_note || "").trim()
          ? contactKey
          : (contact.contact_key || contactKey),
        is_favorite: updatedFavorite ? Boolean(result.is_favorite) : contact.is_favorite,
        favorite_contact_key: updatedFavorite
          ? (result.is_favorite ? contactKey : (contact.favorite_contact_key === contactKey ? "" : contact.favorite_contact_key || ""))
          : contact.favorite_contact_key || "",
        contact_note: updatedNote ? result.contact_note || "" : contact.contact_note || "",
        note_contact_key: updatedNote
          ? (String(result.contact_note || "").trim() ? contactKey : "")
          : contact.note_contact_key || "",
      }
      : contact
  ));
  if (updatedFavorite && state.contactSource === "favorites" && !result.is_favorite) {
    state.contacts = state.contacts.filter((contact) => !contactManagementKeyMatches(contact, contactKey));
  }
  if (updatedNote && state.contactSource === "noted" && !String(result.contact_note || "").trim()) {
    state.contacts = state.contacts.filter((contact) => !contactManagementKeyMatches(contact, contactKey));
  }
  for (const [key, contact] of Object.entries(state.threadContactMatches)) {
    if (!contactManagementKeyMatches(contact || {}, contactKey)) continue;
    state.threadContactMatches[key] = {
      ...contact,
      is_favorite: updatedFavorite ? Boolean(result.is_favorite) : contact.is_favorite,
      favorite_contact_key: updatedFavorite
        ? (result.is_favorite ? contactKey : (contact.favorite_contact_key === contactKey ? "" : contact.favorite_contact_key || ""))
        : contact.favorite_contact_key || "",
      contact_note: updatedNote ? result.contact_note || "" : contact.contact_note || "",
      note_contact_key: updatedNote
        ? (String(result.contact_note || "").trim() ? contactKey : "")
        : contact.note_contact_key || "",
    };
  }
}

async function refreshContactPanelAfterExternalManagement() {
  if (state.contactSource === "favorites" || state.contactSource === "noted") {
    await loadContacts({ force: true });
    return;
  }
  renderContacts();
}

async function toggleContactFavorite(contact) {
  const contactKey = contactFavoriteManagementKey(contact);
  if (!contactKey) {
    el.contactStatus.textContent = "No contact key";
    return;
  }
  const nextFavorite = !isFavoriteContact(contact);
  try {
    const result = await api("/penguin-connect/contacts/management", {
      method: "POST",
      body: JSON.stringify({
        contact_key: contactKey,
        favorite: nextFavorite,
      }),
    });
    mergeContactManagement(result, { updatedNote: false });
    el.contactStatus.textContent = result.is_favorite ? "Contact favorited" : "Contact unfavorited";
    renderContacts();
    buildCodexPrompt();
  } catch (error) {
    el.contactStatus.textContent = error.message;
  }
}

function participantManagedContact(participant, contact) {
  if (contact?.contact_key) return contact;
  const contactKey = contactManagementKeyForHandle(participant.handle);
  return {
    id: `participant:${contactKey}`,
    contact_key: contactKey,
    contact_keys: contactKey ? [contactKey] : [],
    display_name: participant.handle,
    primary_handle: participant.handle,
    handle_type: participant.type,
    source: "conversation",
    is_saved: false,
    is_favorite: false,
    favorite_contact_key: "",
    contact_note: "",
    note_contact_key: "",
  };
}

async function toggleThreadParticipantFavorite(participant, contact) {
  const managedContact = participantManagedContact(participant, contact);
  const contactKey = managedContact.contact_key || "";
  if (!contactKey) {
    el.threadPeopleState.textContent = "No contact key";
    return;
  }
  const nextFavorite = !isFavoriteContact(managedContact);
  try {
    const result = await api("/penguin-connect/contacts/management", {
      method: "POST",
      body: JSON.stringify({
        contact_key: contactKey,
        favorite: nextFavorite,
      }),
    });
    const key = threadContactKey(participant.handle);
    state.threadContactMatches[key] = {
      ...managedContact,
      contact_key: result.contact_key || contactKey,
      is_favorite: Boolean(result.is_favorite),
      favorite_contact_key: result.is_favorite ? result.contact_key || contactKey : "",
      contact_note: result.contact_note || managedContact.contact_note || "",
      note_contact_key: managedContact.note_contact_key || "",
    };
    mergeContactManagement(result, { updatedNote: false });
    el.threadPeopleState.textContent = result.is_favorite ? "Participant favorited" : "Participant unfavorited";
    renderThreadPeople();
    await refreshContactPanelAfterExternalManagement();
    buildCodexPrompt();
  } catch (error) {
    el.threadPeopleState.textContent = error.message;
  }
}

function editContactNote(contact) {
  const contactKey = contactNoteManagementKey(contact);
  if (!contactKey) return;
  state.contactNoteEditorKey = contactKey;
  renderContacts();
}

async function saveContactNote(contact, noteValue) {
  const contactKey = contactNoteManagementKey(contact);
  if (!contactKey) {
    el.contactStatus.textContent = "No contact key";
    return;
  }
  try {
    const result = await api("/penguin-connect/contacts/management", {
      method: "POST",
      body: JSON.stringify({
        contact_key: contactKey,
        note: noteValue,
      }),
    });
    mergeContactManagement(result, { updatedFavorite: false });
    state.contactNoteEditorKey = "";
    el.contactStatus.textContent = result.has_note ? "Contact note saved" : "Contact note cleared";
    renderContacts();
    buildCodexPrompt();
  } catch (error) {
    el.contactStatus.textContent = error.message;
  }
}

function renderContactSourceFilters() {
  el.contactSourceFilters.replaceChildren();
  for (const source of contactSources) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.contactSource = source.key;
    const count = state.contactSourceCounts[source.key];
    button.textContent = Number.isFinite(count) ? `${source.label} ${count}` : source.label;
    if (Number.isFinite(count)) {
      button.title = `${source.label}: ${count}`;
    }
    const active = state.contactSource === source.key;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
    el.contactSourceFilters.append(button);
  }
}

function renderContacts() {
  el.contactList.replaceChildren();
  renderContactBulkActions();
  if (!state.contacts.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state compact-state";
    if (state.contactSource === "favorites") {
      empty.textContent = el.contactSearch.value.trim() ? "No favorite matches" : "No favorite contacts";
    } else if (state.contactSource === "noted") {
      empty.textContent = el.contactSearch.value.trim() ? "No noted matches" : "No noted contacts";
    } else if (state.contactSource === "contacts") {
      empty.textContent = el.contactSearch.value.trim() ? "No saved matches" : "No saved contacts";
    } else if (state.contactSource === "participants") {
      empty.textContent = el.contactSearch.value.trim() ? "No unsaved participants" : "No unsaved participants";
    } else {
      empty.textContent = el.contactSearch.value.trim() ? "No contacts" : "Search Contacts";
    }
    el.contactList.append(empty);
    return;
  }

  for (const contact of state.contacts) {
    const item = document.createElement("div");
    const favorite = isFavoriteContact(contact);
    const noteText = contactNoteText(contact);
    const editingNote = state.contactNoteEditorKey === contactNoteManagementKey(contact);
    const selected = isContactSelected(contact);
    item.className = `contact-item ${favorite ? "favorite-contact" : ""} ${noteText ? "noted-contact" : ""} ${selected ? "selected-contact" : ""}`;
    item.innerHTML = `
      <button class="contact-select-toggle" type="button" title="Select contact" aria-label="Select contact"></button>
      <button class="contact-main" type="button">
        <span class="contact-name"></span>
        <span class="contact-handle"></span>
        <span class="contact-meta"></span>
      </button>
      <span class="contact-actions">
        <button class="contact-favorite" type="button" title="Favorite contact" aria-label="Favorite contact">Star</button>
        <button class="contact-note-button" type="button" title="Private contact note" aria-label="Private contact note">Note</button>
        <button class="contact-copy" type="button" title="Copy contact handle" aria-label="Copy contact handle">Copy</button>
        <button class="contact-add" type="button" title="Add to new chat" aria-label="Add contact to new chat">+</button>
        <button class="contact-create-result" type="button" title="Create contact" aria-label="Create contact from search result">Create</button>
      </span>
      <div class="contact-note" hidden><span></span></div>
      <div class="contact-note-editor" hidden>
        <textarea rows="2" maxlength="2000" placeholder="Private contact note"></textarea>
        <div class="contact-note-actions">
          <button type="button" data-action="save-contact-note">Save</button>
          <button type="button" data-action="cancel-contact-note">Cancel</button>
          <button type="button" data-action="clear-contact-note">Clear</button>
        </div>
      </div>
      <div class="contact-related" hidden></div>
    `;
    item.querySelector(".contact-name").textContent = contactDisplayName(contact);
    item.querySelector(".contact-handle").textContent = contactHandleText(contact);
    item.querySelector(".contact-meta").textContent = contact.organization && contact.organization !== contactDisplayName(contact)
      ? contact.organization
      : contact.handle_type || "contact";
    const selectButton = item.querySelector(".contact-select-toggle");
    selectButton.textContent = selected ? "x" : "";
    selectButton.classList.toggle("active", selected);
    selectButton.disabled = !contactSelectionKey(contact) || !contactRecipientHandle(contact);
    selectButton.setAttribute("aria-pressed", selected ? "true" : "false");
    selectButton.addEventListener("click", () => toggleContactSelection(contact));
    item.querySelector(".contact-main").addEventListener("click", () => useContact(contact));
    const favoriteButton = item.querySelector(".contact-favorite");
    favoriteButton.textContent = favorite ? "Unstar" : "Star";
    favoriteButton.classList.toggle("active", favorite);
    favoriteButton.disabled = !contact.contact_key;
    favoriteButton.addEventListener("click", () => toggleContactFavorite(contact));
    const noteButton = item.querySelector(".contact-note-button");
    noteButton.textContent = noteText ? "Edit" : "Note";
    noteButton.classList.toggle("active", Boolean(noteText) || Boolean(editingNote));
    noteButton.disabled = !contactNoteManagementKey(contact);
    noteButton.addEventListener("click", () => editContactNote(contact));
    const addButton = item.querySelector(".contact-add");
    addButton.disabled = !contactRecipientHandle(contact);
    addButton.addEventListener("click", () => addContactToDraft(contact));
    const copyButton = item.querySelector(".contact-copy");
    copyButton.disabled = !contactRecipientHandle(contact);
    copyButton.addEventListener("click", () => copyContactHandle(contact));
    const createButton = item.querySelector(".contact-create-result");
    createButton.hidden = contact.is_saved !== false;
    createButton.disabled = !contactRecipientHandle(contact);
    createButton.addEventListener("click", () => fillContactFormFromContact(contact));
    const noteBox = item.querySelector(".contact-note");
    if (noteText) {
      noteBox.hidden = false;
      noteBox.querySelector("span").textContent = noteText;
    }
    const noteEditor = item.querySelector(".contact-note-editor");
    const noteInput = noteEditor.querySelector("textarea");
    if (editingNote) {
      noteEditor.hidden = false;
      noteInput.value = noteText;
      noteInput.addEventListener("keydown", (event) => {
        if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
          event.preventDefault();
          saveContactNote(contact, noteInput.value);
        } else if (event.key === "Escape") {
          state.contactNoteEditorKey = "";
          renderContacts();
        }
      });
      noteEditor.querySelector('[data-action="save-contact-note"]').addEventListener("click", () => saveContactNote(contact, noteInput.value));
      noteEditor.querySelector('[data-action="cancel-contact-note"]').addEventListener("click", () => {
        state.contactNoteEditorKey = "";
        renderContacts();
      });
      noteEditor.querySelector('[data-action="clear-contact-note"]').addEventListener("click", () => saveContactNote(contact, ""));
    }
    renderContactRelatedThreads(item.querySelector(".contact-related"), contact);
    el.contactList.append(item);
  }
}

function renderMessageSearchFilters() {
  el.globalMessageSearchFilters.replaceChildren();
  for (const view of messageSearchViews) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.messageSearchView = view.key;
    button.textContent = view.label;
    const active = state.messageSearchView === view.key;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
    if (view.key === "current" && !state.selected) {
      button.disabled = true;
    }
    el.globalMessageSearchFilters.append(button);
  }
}

function renderMessageSearchResults() {
  el.messageSearchResults.replaceChildren();
  const query = el.globalMessageSearch.value.trim();
  const hasDateFilter = Boolean(el.messageDateFrom.value.trim() || el.messageDateTo.value.trim());
  if (!query && state.messageSearchView === "all" && !hasDateFilter) {
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
    const item = document.createElement("div");
    const starred = isStarredMessage(result);
    const noted = hasMessageNote(result);
    const noteText = messageNoteText(result);
    const resultKey = messageSearchResultKey(result);
    const editingNote = state.messageSearchNoteEditorId && resultKey === state.messageSearchNoteEditorId;
    item.className = ["search-result", starred ? "starred" : "", noted ? "noted" : ""].filter(Boolean).join(" ");
    item.innerHTML = `
      <button class="search-result-main" type="button">
        <span class="search-result-top"></span>
        <span class="search-result-body"></span>
      </button>
      <span class="search-result-actions">
        <button type="button" data-action="star">Star</button>
        <button type="button" data-action="note">Note</button>
        <button type="button" data-action="reply">Reply</button>
        <button type="button" data-action="copy">Copy</button>
        <button type="button" data-action="contact">Contact</button>
        <button type="button" data-action="open">Open</button>
      </span>
      <div class="search-result-note" hidden><span></span></div>
      <div class="search-result-note-editor" hidden>
        <textarea rows="2" maxlength="2000" placeholder="Private note"></textarea>
        <div class="search-result-note-actions">
          <button type="button" data-action="save-search-note">Save</button>
          <button type="button" data-action="cancel-search-note">Cancel</button>
          <button type="button" data-action="clear-search-note">Clear</button>
        </div>
      </div>
    `;
    const sender = result.sender_name || result.sender_email || result.direction || "unknown";
    const contactHandle = messageSearchContactHandle(result);
    item.querySelector(".search-result-top").textContent = [
      searchResultConversationName(result),
      sender,
      formatTime(result.message_timestamp || result.timestamp),
    ].filter(Boolean).join(" · ");
    item.querySelector(".search-result-body").textContent = messageSnippet(result);
    item.querySelector(".search-result-main").addEventListener("click", () => useMessageSearchResult(result));
    const starButton = item.querySelector('[data-action="star"]');
    starButton.textContent = starred ? "Unstar" : "Star";
    starButton.classList.toggle("active", starred);
    starButton.disabled = !result.conversation_id || !result.provider_message_id;
    starButton.addEventListener("click", () => toggleMessageSearchResultStar(result));
    const noteButton = item.querySelector('[data-action="note"]');
    noteButton.textContent = noted ? "Edit note" : "Note";
    noteButton.classList.toggle("active", noted || Boolean(editingNote));
    noteButton.disabled = !result.conversation_id || !result.provider_message_id;
    noteButton.addEventListener("click", () => editMessageSearchResultNote(result));
    const noteBox = item.querySelector(".search-result-note");
    if (noteText) {
      noteBox.hidden = false;
      noteBox.querySelector("span").textContent = noteText;
    }
    const noteEditor = item.querySelector(".search-result-note-editor");
    const noteInput = noteEditor.querySelector("textarea");
    if (editingNote) {
      noteEditor.hidden = false;
      noteInput.value = noteText;
      noteInput.addEventListener("keydown", (event) => {
        if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
          event.preventDefault();
          saveMessageSearchResultNote(result, noteInput.value);
        }
        if (event.key === "Escape") {
          state.messageSearchNoteEditorId = "";
          renderMessageSearchResults();
        }
      });
      noteEditor.querySelector('[data-action="save-search-note"]').addEventListener("click", () => saveMessageSearchResultNote(result, noteInput.value));
      noteEditor.querySelector('[data-action="cancel-search-note"]').addEventListener("click", () => {
        state.messageSearchNoteEditorId = "";
        renderMessageSearchResults();
      });
      noteEditor.querySelector('[data-action="clear-search-note"]').addEventListener("click", () => saveMessageSearchResultNote(result, ""));
    }
    item.querySelector('[data-action="reply"]').addEventListener("click", () => replyToMessageSearchResult(result));
    item.querySelector('[data-action="copy"]').addEventListener("click", async () => {
      await copyText(messageCopyText(result));
      el.sendState.textContent = "Search result copied";
    });
    const contactButton = item.querySelector('[data-action="contact"]');
    contactButton.disabled = !contactHandle;
    contactButton.addEventListener("click", () => fillContactFormFromMessageSearchResult(result));
    item.querySelector('[data-action="open"]').addEventListener("click", () => useMessageSearchResult(result));
    el.messageSearchResults.append(item);
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

function mediaTypeForAttachment(attachment) {
  if (isImageAttachment(attachment)) return "image";
  if (isAudioAttachment(attachment)) return "audio";
  return "file";
}

function threadMediaItems() {
  const items = [];
  for (const message of state.messages) {
    for (const [index, attachment] of attachmentRows(message).entries()) {
      const url = attachmentLocalPath(attachment) ? attachmentUrl(message, index) : "";
      items.push({
        attachment,
        index,
        label: attachmentLabel(attachment),
        message,
        messageId: message.provider_message_id || "",
        sender: messageSender(message),
        time: messageTime(message),
        type: mediaTypeForAttachment(attachment),
        url,
      });
    }
  }
  return items;
}

function mediaMatchesView(item, view = state.mediaView) {
  if (view === "images") return item.type === "image";
  if (view === "audio") return item.type === "audio";
  if (view === "files") return item.type === "file";
  return true;
}

function mediaViewCounts(items) {
  return {
    all: items.length,
    images: items.filter((item) => item.type === "image").length,
    audio: items.filter((item) => item.type === "audio").length,
    files: items.filter((item) => item.type === "file").length,
  };
}

function renderMediaFilters(items) {
  const counts = mediaViewCounts(items);
  el.mediaFilters.replaceChildren();
  for (const view of mediaViews) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.mediaView = view.key;
    button.textContent = `${view.label} ${counts[view.key] ?? 0}`;
    const active = state.mediaView === view.key;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
    el.mediaFilters.append(button);
  }
}

function focusMediaMessage(item) {
  if (!item.messageId) return;
  state.focusMessageId = item.messageId;
  state.messageView = "all";
  el.messageFilter.value = "";
  renderMessages();
  requestAnimationFrame(() => {
    const focused = el.messageList.querySelector(".message.focused");
    if (focused) focused.scrollIntoView({ block: "center" });
  });
}

function renderThreadMedia() {
  const items = threadMediaItems();
  renderMediaFilters(items);
  el.threadMedia.replaceChildren();
  el.threadMediaState.textContent = state.selected
    ? `${items.length} item${items.length === 1 ? "" : "s"}`
    : "No thread";
  if (!state.selected) {
    const empty = document.createElement("div");
    empty.className = "empty-state compact-state";
    empty.textContent = "Select a conversation";
    el.threadMedia.append(empty);
    return;
  }
  const visible = items.filter((item) => mediaMatchesView(item));
  if (!visible.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state compact-state";
    empty.textContent = items.length ? "No media in this view" : "No loaded media";
    el.threadMedia.append(empty);
    return;
  }

  for (const item of visible) {
    const row = document.createElement("div");
    row.className = `media-item ${item.type}`;
    row.innerHTML = `
      <div class="media-thumb"></div>
      <div class="media-copy">
        <span class="media-name"></span>
        <span class="media-meta"></span>
      </div>
      <div class="media-actions">
        <button type="button" data-action="jump">Jump</button>
      </div>
    `;
    const thumb = row.querySelector(".media-thumb");
    if (item.url && item.type === "image") {
      const image = document.createElement("img");
      image.src = item.url;
      image.alt = item.label;
      image.loading = "lazy";
      thumb.append(image);
    } else {
      thumb.textContent = item.type === "audio" ? "AUD" : "FILE";
    }
    row.querySelector(".media-name").textContent = item.label;
    row.querySelector(".media-meta").textContent = [item.sender, item.time].filter(Boolean).join(" · ");
    row.querySelector('[data-action="jump"]').addEventListener("click", () => focusMediaMessage(item));
    if (item.url) {
      const open = document.createElement("a");
      open.href = item.url;
      open.target = "_blank";
      open.rel = "noopener";
      open.textContent = "Open";
      row.querySelector(".media-actions").append(open);
    }
    el.threadMedia.append(row);
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
      message.message_note,
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
    empty.textContent = state.messagesLoading
      ? "Loading messages"
      : (query || state.messageView !== "all" ? "No matching messages" : "No loaded messages");
    el.messageList.append(empty);
    return;
  }

  for (const message of rows) {
    const item = document.createElement("article");
    const focused = state.focusMessageId && message.provider_message_id === state.focusMessageId;
    const unread = isUnreadMessage(message);
    const starred = isStarredMessage(message);
    const noted = hasMessageNote(message);
    item.className = `message ${isOwnMessage(message) ? "mine" : ""} ${unread ? "unread" : ""} ${starred ? "starred" : ""} ${noted ? "noted" : ""} ${focused ? "focused" : ""}`;
    item.dataset.messageId = message.provider_message_id || "";
    const attachments = attachmentRows(message);
    item.innerHTML = `
      <div class="message-head">
        <span></span>
        <time></time>
      </div>
      <div class="message-body"></div>
      <div class="message-note" hidden><span></span></div>
      <div class="message-note-editor" hidden>
        <textarea rows="2" maxlength="2000" placeholder="Private note"></textarea>
        <div class="message-note-actions">
          <button type="button" data-action="save-note">Save</button>
          <button type="button" data-action="cancel-note">Cancel</button>
          <button type="button" data-action="clear-note">Clear</button>
        </div>
      </div>
      <div class="message-attachments"></div>
      <div class="message-actions">
        <button type="button" data-action="star">Star</button>
        <button type="button" data-action="read-state">Mark unread</button>
        <button type="button" data-action="note">Note</button>
        <button type="button" data-action="reply">Reply</button>
        <button type="button" data-action="copy">Copy</button>
      </div>
    `;
    item.querySelector(".message-head span").textContent = messageSender(message);
    item.querySelector("time").textContent = messageTime(message);
    item.querySelector(".message-body").textContent = message.body_text || message.text || "";
    const noteText = messageNoteText(message);
    const editingNote = state.messageNoteEditorId && message.provider_message_id === state.messageNoteEditorId;
    const noteBox = item.querySelector(".message-note");
    if (noteText) {
      noteBox.hidden = false;
      noteBox.querySelector("span").textContent = noteText;
    }
    const noteEditor = item.querySelector(".message-note-editor");
    const noteInput = noteEditor.querySelector("textarea");
    if (editingNote) {
      noteEditor.hidden = false;
      noteInput.value = noteText;
      noteInput.addEventListener("keydown", (event) => {
        if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
          event.preventDefault();
          saveMessageNote(message, noteInput.value);
        }
        if (event.key === "Escape") {
          state.messageNoteEditorId = "";
          renderMessages();
        }
      });
      noteEditor.querySelector('[data-action="save-note"]').addEventListener("click", () => saveMessageNote(message, noteInput.value));
      noteEditor.querySelector('[data-action="cancel-note"]').addEventListener("click", () => {
        state.messageNoteEditorId = "";
        renderMessages();
      });
      noteEditor.querySelector('[data-action="clear-note"]').addEventListener("click", () => saveMessageNote(message, ""));
    }
    const starButton = item.querySelector('[data-action="star"]');
    starButton.textContent = starred ? "Unstar" : "Star";
    starButton.classList.toggle("active", starred);
    starButton.disabled = !message.provider_message_id;
    starButton.addEventListener("click", () => toggleMessageStar(message));
    const readButton = item.querySelector('[data-action="read-state"]');
    readButton.textContent = unread ? "Mark read" : "Mark unread";
    readButton.classList.toggle("active", unread);
    readButton.disabled = !message.provider_message_id;
    readButton.addEventListener("click", () => toggleMessageRead(message));
    const noteButton = item.querySelector('[data-action="note"]');
    noteButton.textContent = noted ? "Edit note" : "Note";
    noteButton.classList.toggle("active", noted || Boolean(editingNote));
    noteButton.disabled = !message.provider_message_id;
    noteButton.addEventListener("click", () => editMessageNote(message));
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

function renderAttachments(target = "reply") {
  const { list } = attachmentElementsFor(target);
  const files = attachmentFilesFor(target);
  list.replaceChildren();
  for (const [index, file] of files.entries()) {
    const chip = document.createElement("div");
    chip.className = "attachment-chip";
    chip.innerHTML = `<span></span><button class="remove-button" type="button" title="Remove">×</button>`;
    chip.querySelector("span").textContent = `${file.name} · ${Math.ceil(file.size / 1024)} KB`;
    chip.querySelector("button").addEventListener("click", () => {
      const nextFiles = attachmentFilesFor(target).slice();
      nextFiles.splice(index, 1);
      setAttachmentFilesFor(target, nextFiles);
      if (target === "draft") {
        state.draftAttachmentFolder = "";
        state.draftAttachmentPaths = [];
      }
      renderAttachments(target);
      if (target === "draft") {
        renderDraftPreview();
      } else {
        buildCodexPrompt();
      }
    });
    list.append(chip);
  }
}

function voiceMemoRecordingSupported() {
  return Boolean(window.MediaRecorder && navigator.mediaDevices?.getUserMedia);
}

function voiceMemoElementsFor(target = "reply") {
  return target === "draft"
    ? {
      button: el.draftVoiceMemoButton,
      timer: el.draftVoiceMemoTimer,
      status: el.draftVoiceMemoStatus,
      state: el.draftState,
    }
    : {
      button: el.voiceMemoButton,
      timer: el.voiceMemoTimer,
      status: el.voiceMemoStatus,
      state: el.sendState,
    };
}

function setVoiceMemoStatus(target, value) {
  state.voiceMemoStatus[target] = value;
}

function renderAllVoiceMemoControls() {
  renderVoiceMemoControls("reply");
  renderVoiceMemoControls("draft");
}

function voiceMemoMimeType() {
  const candidates = [
    "audio/mp4",
    "audio/aac",
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
  ];
  if (!window.MediaRecorder?.isTypeSupported) return "";
  return candidates.find((type) => window.MediaRecorder.isTypeSupported(type)) || "";
}

function voiceMemoDurationText(ms) {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

function stopVoiceMemoStream() {
  if (!state.voiceMemoStream) return;
  for (const track of state.voiceMemoStream.getTracks()) {
    track.stop();
  }
  state.voiceMemoStream = null;
}

function clearVoiceMemoTimer() {
  if (!state.voiceMemoTimerId) return;
  window.clearInterval(state.voiceMemoTimerId);
  state.voiceMemoTimerId = 0;
}

function renderVoiceMemoControls(target = "reply") {
  const controls = voiceMemoElementsFor(target);
  const supported = voiceMemoRecordingSupported();
  const activeRecording = state.voiceMemoRecorder?.state === "recording";
  const recording = activeRecording && state.voiceMemoTarget === target;
  const recordingElsewhere = activeRecording && state.voiceMemoTarget && state.voiceMemoTarget !== target;
  controls.button.disabled = !supported || recordingElsewhere;
  controls.button.textContent = recording ? "Stop" : "Record";
  controls.button.title = recording ? "Stop voice memo recording" : "Record voice memo";
  controls.button.classList.toggle("recording", recording);
  controls.timer.textContent = recording
    ? voiceMemoDurationText(Date.now() - state.voiceMemoStartedAt)
    : "00:00";
  controls.status.textContent = supported
    ? (recordingElsewhere ? "Recording elsewhere" : state.voiceMemoStatus[target] || "Mic ready")
    : "Mic unavailable";
}

function voiceMemoFileName(mimeType) {
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\..+$/, "").replace("T", "-");
  return `voice-memo-${stamp}${attachmentExtensionForType(mimeType) || ".webm"}`;
}

function makeVoiceMemoFile(blob, mimeType) {
  const fileName = voiceMemoFileName(mimeType);
  try {
    return new File([blob], fileName, { type: mimeType || blob.type || "audio/webm", lastModified: Date.now() });
  } catch (_error) {
    blob.name = fileName;
    blob.lastModified = Date.now();
    return blob;
  }
}

function finishVoiceMemoRecording(recorder) {
  const target = state.voiceMemoTarget || "reply";
  clearVoiceMemoTimer();
  stopVoiceMemoStream();
  const chunks = state.voiceMemoChunks;
  const mimeType = recorder.mimeType || chunks[0]?.type || "audio/webm";
  state.voiceMemoRecorder = null;
  state.voiceMemoChunks = [];
  state.voiceMemoStartedAt = 0;
  state.voiceMemoTarget = "";
  if (!chunks.length) {
    setVoiceMemoStatus(target, "No audio captured");
    renderAllVoiceMemoControls();
    return;
  }
  const blob = new Blob(chunks, { type: mimeType });
  addFiles([makeVoiceMemoFile(blob, mimeType)], target);
  setVoiceMemoStatus(target, "Voice memo added");
  voiceMemoElementsFor(target).state.textContent = "Voice memo attached";
  renderAllVoiceMemoControls();
}

async function startVoiceMemoRecording(target = "reply") {
  if (!voiceMemoRecordingSupported()) {
    setVoiceMemoStatus(target, "Mic unavailable");
    renderAllVoiceMemoControls();
    return;
  }
  if (state.voiceMemoRecorder?.state === "recording") {
    setVoiceMemoStatus(target, "Recording elsewhere");
    renderAllVoiceMemoControls();
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mimeType = voiceMemoMimeType();
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    state.voiceMemoStream = stream;
    state.voiceMemoRecorder = recorder;
    state.voiceMemoChunks = [];
    state.voiceMemoStartedAt = Date.now();
    state.voiceMemoTarget = target;
    setVoiceMemoStatus(target, "Recording");
    recorder.addEventListener("dataavailable", (event) => {
      if (event.data && event.data.size > 0) {
        state.voiceMemoChunks.push(event.data);
      }
    });
    recorder.addEventListener("stop", () => finishVoiceMemoRecording(recorder), { once: true });
    recorder.addEventListener("error", (event) => {
      setVoiceMemoStatus(target, event.error?.message || "Recording failed");
      state.voiceMemoRecorder = null;
      state.voiceMemoChunks = [];
      state.voiceMemoTarget = "";
      clearVoiceMemoTimer();
      stopVoiceMemoStream();
      renderAllVoiceMemoControls();
    });
    recorder.start();
    state.voiceMemoTimerId = window.setInterval(renderAllVoiceMemoControls, 500);
    renderAllVoiceMemoControls();
  } catch (error) {
    setVoiceMemoStatus(target, error.name === "NotAllowedError" ? "Mic permission denied" : error.message || "Recording failed");
    state.voiceMemoRecorder = null;
    state.voiceMemoChunks = [];
    state.voiceMemoTarget = "";
    clearVoiceMemoTimer();
    stopVoiceMemoStream();
    renderAllVoiceMemoControls();
  }
}

function stopVoiceMemoRecording() {
  const recorder = state.voiceMemoRecorder;
  if (!recorder || recorder.state === "inactive") return;
  setVoiceMemoStatus(state.voiceMemoTarget || "reply", "Saving voice memo");
  recorder.stop();
  renderAllVoiceMemoControls();
}

function toggleVoiceMemoRecording(target = "reply") {
  if (state.voiceMemoRecorder?.state === "recording" && state.voiceMemoTarget === target) {
    stopVoiceMemoRecording();
  } else {
    startVoiceMemoRecording(target);
  }
}

function renderThreadControls() {
  const selected = state.selected;
  const hasSelection = Boolean(selected);
  el.pinButton.disabled = !hasSelection;
  el.muteButton.disabled = !hasSelection;
  el.archiveButton.disabled = !hasSelection;
  el.saveManagementButton.disabled = !hasSelection;
  el.threadLocalTitle.disabled = !hasSelection;
  el.threadFollowUpAt.disabled = !hasSelection;
  el.threadTags.disabled = !hasSelection;
  el.threadNote.disabled = !hasSelection;
  el.markReadButton.disabled = !hasSelection;
  el.markUnreadButton.disabled = !hasSelection;
  el.connectionButton.disabled = !hasSelection;
  el.copyThreadButton.disabled = !hasSelection;
  el.syncButton.disabled = state.localRefreshBusy;
  el.syncButton.textContent = state.localRefreshBusy ? "Refreshing" : "Refresh";
  if (!hasSelection) {
    el.threadStatus.textContent = "No conversation selected";
    el.managementState.textContent = "No thread";
    el.pinButton.textContent = "Pin";
    el.muteButton.textContent = "Mute";
    el.archiveButton.textContent = "Archive";
    el.connectionButton.textContent = "Disconnect";
    return;
  }
  const unread = Number(selected.unread_count || 0);
  const status = selected.status || "active";
  const excluded = selected.excluded ? " · excluded" : "";
  const managed = [
    selected.is_pinned ? "pinned" : "",
    selected.is_muted ? "muted" : "",
    selected.is_archived ? "archived" : "",
    hasFollowUp(selected) ? `follow-up ${followUpLabel(selected)}` : "",
  ].filter(Boolean).join(" · ");
  const source = [selected.source_service_name || selected.source_provider || "Messages", selected.chat_type || ""]
    .filter(Boolean)
    .join(" · ");
  el.threadStatus.textContent = `${status}${excluded}${managed ? ` · ${managed}` : ""} · ${unread} unread · ${source}`;
  el.pinButton.textContent = selected.is_pinned ? "Unpin" : "Pin";
  el.muteButton.textContent = selected.is_muted ? "Unmute" : "Mute";
  el.archiveButton.textContent = selected.is_archived ? "Unarchive" : "Archive";
  el.connectionButton.textContent = status === "active" ? "Disconnect" : "Reconnect";
}

function renderManagementFields() {
  if (!state.selected) {
    el.threadLocalTitle.value = "";
    el.threadFollowUpAt.value = "";
    el.threadTags.value = "";
    el.threadNote.value = "";
    el.managementState.textContent = "No thread";
    return;
  }
  el.threadLocalTitle.value = state.selected.title || "";
  el.threadFollowUpAt.value = followUpInputValue(state.selected);
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
  renderThreadHeader();
  renderThreadControls();
  renderManagementFields();
  renderThreadPeople();
  renderThreadMedia();
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
    el.statusLine.textContent = status.ok
      ? "Messages ready · local send enabled"
      : "Messages warning";
    el.senderBadge.textContent = "Messages";
  } catch (error) {
    el.statusLine.textContent = `Messages offline · ${error.message}`;
    el.senderBadge.textContent = "Messages";
  }
}

async function loadConversations({ autoSelect = true } = {}) {
  try {
    const payload = await api("/penguin-connect/conversations");
    state.senderEmail = payload.gmail_email || state.senderEmail;
    state.conversations = payload.conversations || [];
    if (state.selected) {
      state.selected = state.conversations.find((conversation) => conversation.conversation_id === state.selected.conversation_id) || state.selected;
      renderThreadHeader();
      renderThreadControls();
      renderManagementFields();
    }
    el.senderBadge.textContent = "Messages";
    renderConversations();
    renderContacts();
    if (autoSelect && !state.selected && state.conversations.length) {
      await selectConversation(state.conversations.find((conversation) => !conversation.is_archived) || state.conversations[0]);
    }
  } catch (error) {
    el.conversationList.innerHTML = `<div class="error-state">${error.message}</div>`;
  }
}

async function loadContacts({ force = false } = {}) {
  const query = el.contactSearch.value.trim();
  const browsesUnsaved = state.contactSource === "participants";
  const browsesFavorites = state.contactSource === "favorites";
  const browsesNoted = state.contactSource === "noted";
  const browsesSaved = state.contactSource === "contacts";
  renderContactSourceFilters();

  if (!query && state.contactSource === "all") {
    el.contactStatus.textContent = "Loading contacts";
  } else if (browsesUnsaved && !query) {
    el.contactStatus.textContent = "Loading unsaved participants";
  } else if (browsesFavorites && !query) {
    el.contactStatus.textContent = "Loading favorite contacts";
  } else if (browsesNoted && !query) {
    el.contactStatus.textContent = "Loading noted contacts";
  } else if (browsesSaved && !query) {
    el.contactStatus.textContent = "Loading saved contacts";
  } else {
    el.contactStatus.textContent = "Searching";
  }
  try {
    const params = new URLSearchParams({
      search: query,
      limit: "20",
      source: state.contactSource,
    });
    const payload = await api(`/penguin-connect/contacts?${params.toString()}`);
    state.contacts = payload.contacts || [];
    state.contactSourceCounts = payload.source_counts || state.contactSourceCounts || {};
    pruneSelectedContacts();
    renderContactSourceFilters();
    const total = payload.total_contacts ?? 0;
    const counts = payload.source_counts || {};
    if (!query && state.contactSource === "all") {
      const savedCount = counts.contacts ?? total;
      const unsavedCount = counts.participants ?? payload.participant_count ?? 0;
      el.contactStatus.textContent = `${state.contacts.length} all entries · ${savedCount} saved · ${unsavedCount} unsaved`;
    } else if (state.contactSource === "participants") {
      el.contactStatus.textContent = `${state.contacts.length} unsaved participant${state.contacts.length === 1 ? "" : "s"}`;
    } else if (state.contactSource === "favorites") {
      el.contactStatus.textContent = `${state.contacts.length} favorite contact${state.contacts.length === 1 ? "" : "s"}`;
    } else if (state.contactSource === "noted") {
      el.contactStatus.textContent = `${state.contacts.length} noted contact${state.contacts.length === 1 ? "" : "s"}`;
    } else if (state.contactSource === "contacts") {
      el.contactStatus.textContent = `${state.contacts.length} saved contact${state.contacts.length === 1 ? "" : "s"} · ${total} saved`;
    } else if (query) {
      const participantCount = payload.participant_count || 0;
      const suffix = participantCount ? ` · ${participantCount} unsaved` : "";
      el.contactStatus.textContent = `${state.contacts.length} ${contactSourceLabel().toLowerCase()} match${state.contacts.length === 1 ? "" : "es"} · ${total} saved${suffix}`;
    } else {
      el.contactStatus.textContent = `${state.contacts.length} ${contactSourceLabel().toLowerCase()} contacts · ${total} saved`;
    }
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
  const dateFrom = el.messageDateFrom.value.trim();
  const dateTo = el.messageDateTo.value.trim();
  const hasDateFilter = Boolean(dateFrom || dateTo);
  const scoped = state.messageSearchView !== "all";
  state.messageSearchNoteEditorId = "";
  renderMessageSearchFilters();
  if (query.length < 2 && !scoped && !hasDateFilter) {
    state.messageSearchResults = [];
    el.messageSearchStatus.textContent = "Type 2+ chars or choose dates";
    renderMessageSearchResults();
    buildCodexPrompt();
    return;
  }
  if (state.messageSearchView === "current" && !state.selected) {
    state.messageSearchResults = [];
    el.messageSearchStatus.textContent = "Select a thread for current-thread search";
    renderMessageSearchResults();
    buildCodexPrompt();
    return;
  }

  const view = messageSearchViews.find((item) => item.key === state.messageSearchView) || messageSearchViews[0];
  el.messageSearchStatus.textContent = query
    ? "Searching local cache"
    : hasDateFilter
      ? "Loading messages in date range"
      : `Loading ${view.label.toLowerCase()} messages`;
  try {
    const params = new URLSearchParams({
      query,
      limit: "30",
      view: state.messageSearchView,
    });
    if (dateFrom) params.set("date_from", dateFrom);
    if (dateTo) params.set("date_to", dateTo);
    if (state.messageSearchView === "current" && state.selected?.conversation_id) {
      params.set("conversation_id", state.selected.conversation_id);
    }
    const payload = await api(`/penguin-connect/messages/search?${params.toString()}`);
    state.messageSearchResults = payload.messages || [];
    const rangeSuffix = hasDateFilter ? " in range" : "";
    el.messageSearchStatus.textContent = `${state.messageSearchResults.length} ${view.label.toLowerCase()} match${state.messageSearchResults.length === 1 ? "" : "es"}${rangeSuffix}`;
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
  state.messagesLoading = true;
  resetThreadContactMatches();
  clearReplyContext();
  el.composer.value = draftTextForConversation(conversation);
  renderThreadHeader();
  renderThreadControls();
  renderManagementFields();
  renderThreadPeople();
  renderThreadMedia();
  renderConversations();
  renderMessageSearchFilters();
  renderMessages();
  loadThreadContactMatches(conversation);
  if (state.messageSearchView === "current") {
    loadMessageSearch();
  }
  await loadMessages();
}

async function loadMessages() {
  if (!state.selected) return;
  state.messagesLoading = true;
  renderMessages();
  try {
    const payload = await api(`/penguin-connect/conversations/${encodeURIComponent(state.selected.conversation_id)}/messages?limit=200`);
    state.messages = payload.messages || [];
    state.messagesLoading = false;
    renderMessages();
    renderThreadMedia();
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
    state.messagesLoading = false;
    el.messageList.innerHTML = `<div class="error-state">${error.message}</div>`;
  }
}

async function refreshLocalMessages() {
  if (state.localRefreshBusy) return;
  const hadSelection = Boolean(state.selected);
  const conversationId = state.selected?.conversation_id || "";
  state.localRefreshBusy = true;
  renderThreadControls();
  el.sendState.textContent = hadSelection ? "Refreshing local Messages" : "Refreshing local conversations";
  try {
    await loadConversations({ autoSelect: !hadSelection });
    if (hadSelection && state.selected?.conversation_id === conversationId) {
      await loadMessages();
      await loadThreadContactMatches();
    }
    if (
      el.globalMessageSearch.value.trim()
      || state.messageSearchView !== "all"
      || el.messageDateFrom.value.trim()
      || el.messageDateTo.value.trim()
    ) {
      await loadMessageSearch();
    }
    await loadContacts({ force: true });
    el.sendState.textContent = state.selected ? "Local Messages refreshed" : "Local conversations refreshed";
  } catch (error) {
    el.sendState.textContent = error.message;
  } finally {
    state.localRefreshBusy = false;
    renderThreadControls();
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

async function filesAsBrowserAttachments(files) {
  const attachments = [];
  for (const file of files || []) {
    attachments.push({
      filename: file.name,
      mime_type: file.type || "application/octet-stream",
      size: file.size,
      data_base64: await readFileAsBase64(file),
    });
  }
  return attachments;
}

async function sendMessage() {
  if (!state.selected) return;
  if (state.voiceMemoRecorder?.state === "recording") {
    el.sendState.textContent = "Stop voice memo before sending";
    return;
  }
  const conversationId = state.selected.conversation_id;
  const message = el.composer.value;
  if (!message.trim() && !state.attachments.length) {
    el.sendState.textContent = "Nothing to send";
    return;
  }
  el.sendButton.disabled = true;
  el.sendState.textContent = "Sending";
  try {
    const attachments = await filesAsBrowserAttachments(state.attachments);
    await api(`/penguin-connect/conversations/${encodeURIComponent(conversationId)}/send`, {
      method: "POST",
      body: JSON.stringify({
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
    renderThreadMedia();
  } catch (error) {
    el.threadStatus.textContent = error.message;
  } finally {
    renderThreadControls();
  }
}

async function setConversationManagement(fields) {
  if (!state.selected) return;
  el.pinButton.disabled = true;
  el.muteButton.disabled = true;
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
    is_muted: Boolean(result.is_muted),
    title: result.title || "",
    note: result.note || "",
    labels: result.labels || [],
    follow_up_at: result.follow_up_at || "",
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
  const markUnreadIntent = shouldBulkMarkUnread(targets);
  state.bulkBusy = true;
  state.bulkMessage = "";
  renderConversations();
  try {
    for (const conversation of targets) {
      const result = await api(`/penguin-connect/conversations/${encodeURIComponent(conversation.conversation_id)}/read-state`, {
        method: "POST",
        body: JSON.stringify({ unread: markUnreadIntent }),
      });
      updateConversationFields(conversation.conversation_id, {
        unread_count: result.unread_count || 0,
        has_unread: Boolean(result.has_unread),
      });
      if (state.selected?.conversation_id === conversation.conversation_id) {
        state.messages = state.messages.map((message) => ({ ...message, is_read: !markUnreadIntent }));
      }
    }
    state.selectedConversationIds.clear();
    state.bulkMessage = markUnreadIntent ? `Marked ${targets.length} unread` : `Marked ${targets.length} read`;
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
  const archiveIntent = shouldBulkArchive(targets);
  const actionLabel = archiveIntent ? "Archive" : "Restore";
  if (!window.confirm(`${actionLabel} ${targets.length} selected conversation${targets.length === 1 ? "" : "s"}?`)) return;
  state.bulkBusy = true;
  state.bulkMessage = "";
  renderConversations();
  try {
    for (const conversation of targets) {
      await updateConversationManagement(conversation.conversation_id, { archived: archiveIntent });
    }
    state.selectedConversationIds.clear();
    state.bulkMessage = archiveIntent ? `Archived ${targets.length}` : `Restored ${targets.length}`;
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

async function bulkPinSelected() {
  const targets = selectedConversationSnapshot();
  if (!targets.length) return;
  const pinIntent = shouldBulkPin(targets);
  const actionLabel = pinIntent ? "Pin" : "Unpin";
  if (!window.confirm(`${actionLabel} ${targets.length} selected conversation${targets.length === 1 ? "" : "s"}?`)) return;
  state.bulkBusy = true;
  state.bulkMessage = "";
  renderConversations();
  try {
    for (const conversation of targets) {
      await updateConversationManagement(conversation.conversation_id, { pinned: pinIntent });
    }
    state.selectedConversationIds.clear();
    state.bulkMessage = pinIntent ? `Pinned ${targets.length}` : `Unpinned ${targets.length}`;
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

async function bulkMuteSelected() {
  const targets = selectedConversationSnapshot();
  if (!targets.length) return;
  const muteIntent = shouldBulkMute(targets);
  const actionLabel = muteIntent ? "Mute" : "Unmute";
  if (!window.confirm(`${actionLabel} ${targets.length} selected conversation${targets.length === 1 ? "" : "s"}?`)) return;
  state.bulkBusy = true;
  state.bulkMessage = "";
  renderConversations();
  try {
    for (const conversation of targets) {
      await updateConversationManagement(conversation.conversation_id, { muted: muteIntent });
    }
    state.selectedConversationIds.clear();
    state.bulkMessage = muteIntent ? `Muted ${targets.length}` : `Unmuted ${targets.length}`;
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

async function bulkApplyLabels() {
  const targets = selectedConversationSnapshot();
  const labels = cleanBulkLabels(el.bulkLabelsInput.value);
  if (!targets.length || !labels.length) {
    state.bulkMessage = targets.length ? "Add label text" : "Select conversations";
    renderConversations();
    return;
  }

  state.bulkBusy = true;
  state.bulkMessage = "";
  renderConversations();
  try {
    for (const conversation of targets) {
      await updateConversationManagement(conversation.conversation_id, {
        labels: mergeConversationLabels(conversation, labels),
      });
    }
    state.selectedConversationIds.clear();
    el.bulkLabelsInput.value = "";
    state.bulkMessage = `Labeled ${targets.length}`;
  } catch (error) {
    state.bulkMessage = error.message;
  } finally {
    state.bulkBusy = false;
    renderConversations();
    renderManagementFields();
    buildCodexPrompt();
  }
}

async function bulkRemoveLabels() {
  const targets = selectedConversationSnapshot();
  const labels = cleanBulkLabels(el.bulkLabelsInput.value);
  if (!targets.length || !labels.length) {
    state.bulkMessage = targets.length ? "Add label text" : "Select conversations";
    renderConversations();
    return;
  }

  state.bulkBusy = true;
  state.bulkMessage = "";
  renderConversations();
  try {
    let changedCount = 0;
    for (const conversation of targets) {
      const nextLabels = removeConversationLabels(conversation, labels);
      if (nextLabels.length !== labelsForConversation(conversation).length) changedCount += 1;
      await updateConversationManagement(conversation.conversation_id, {
        labels: nextLabels,
      });
    }
    state.selectedConversationIds.clear();
    el.bulkLabelsInput.value = "";
    state.bulkMessage = changedCount
      ? `Removed labels from ${changedCount}`
      : "No matching labels";
  } catch (error) {
    state.bulkMessage = error.message;
  } finally {
    state.bulkBusy = false;
    renderConversations();
    renderManagementFields();
    buildCodexPrompt();
  }
}

async function bulkSetFollowUp() {
  const targets = selectedConversationSnapshot();
  const followUpAt = el.bulkFollowUpAt.value.trim();
  if (!targets.length || !followUpAt) {
    state.bulkMessage = targets.length ? "Pick follow-up time" : "Select conversations";
    renderConversations();
    return;
  }

  state.bulkBusy = true;
  state.bulkMessage = "";
  renderConversations();
  try {
    for (const conversation of targets) {
      await updateConversationManagement(conversation.conversation_id, {
        follow_up_at: followUpAt,
      });
    }
    state.selectedConversationIds.clear();
    el.bulkFollowUpAt.value = "";
    state.bulkMessage = `Scheduled follow-up for ${targets.length}`;
  } catch (error) {
    state.bulkMessage = error.message;
  } finally {
    state.bulkBusy = false;
    renderConversations();
    renderManagementFields();
    buildCodexPrompt();
  }
}

async function bulkClearFollowUps() {
  const targets = selectedConversationSnapshot();
  if (!targets.length) {
    state.bulkMessage = "Select conversations";
    renderConversations();
    return;
  }

  state.bulkBusy = true;
  state.bulkMessage = "";
  renderConversations();
  try {
    let changedCount = 0;
    for (const conversation of targets) {
      if (hasFollowUp(conversation)) changedCount += 1;
      await updateConversationManagement(conversation.conversation_id, {
        follow_up_at: "",
      });
    }
    state.selectedConversationIds.clear();
    state.bulkMessage = changedCount
      ? `Cleared ${changedCount} follow-up${changedCount === 1 ? "" : "s"}`
      : "No follow-ups to clear";
  } catch (error) {
    state.bulkMessage = error.message;
  } finally {
    state.bulkBusy = false;
    renderConversations();
    renderManagementFields();
    buildCodexPrompt();
  }
}

async function bulkClearDrafts() {
  const draftTargets = selectedConversationSnapshot().filter(conversationHasDraft);
  if (!draftTargets.length) {
    state.bulkMessage = "No drafts to clear";
    renderConversations();
    return;
  }

  if (!window.confirm(`Clear ${draftTargets.length} selected reply draft${draftTargets.length === 1 ? "" : "s"}?`)) return;
  state.bulkBusy = true;
  state.bulkMessage = "";
  renderConversations();
  try {
    for (const conversation of draftTargets) {
      await updateConversationManagement(conversation.conversation_id, { draft_text: "" });
      if (state.selected?.conversation_id === conversation.conversation_id) {
        el.composer.value = "";
      }
    }
    state.selectedConversationIds.clear();
    state.bulkMessage = `Cleared ${draftTargets.length} draft${draftTargets.length === 1 ? "" : "s"}`;
  } catch (error) {
    state.bulkMessage = error.message;
  } finally {
    state.bulkBusy = false;
    renderConversations();
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
    title: el.threadLocalTitle.value,
    follow_up_at: el.threadFollowUpAt.value,
    note: el.threadNote.value,
    labels: splitValues(el.threadTags.value),
  });
}

async function toggleConnection() {
  if (!state.selected) return;
  const active = (state.selected.status || "active") === "active";
  if (active && !window.confirm("Disconnect this local Messages conversation? Cached messages for this thread will be removed.")) {
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
    const note = messageNoteText(message);
    const noteSuffix = note ? ` [private note: ${trim(note, 180)}]` : "";
    return `${formatTime(message.message_timestamp || message.timestamp)} | ${sender}: ${text}${suffix}${noteSuffix}`;
  }).join("\n");
}

function selectedConversationContext() {
  if (!state.selected) return "none";
  const labels = splitValues(el.threadTags.value).join(", ") || "none";
  const note = el.threadNote.value.trim() || "none";
  const participants = conversationParticipants().slice(0, 14).map((participant) => {
    const contact = threadContactMatch(participant.handle);
    const managedContact = participantManagedContact(participant, contact);
    const favorite = isFavoriteContact(managedContact) ? " favorite" : "";
    const note = contactNoteText(managedContact) ? ` note:${trim(contactNoteText(managedContact), 80)}` : "";
    return contact && contact.is_saved !== false
      ? `${contactDisplayName(contact)} <${participant.handle}>${favorite}${note}`
      : `${participant.handle} (unknown contact${favorite}${note})`;
  }).join(", ") || "unknown";
  return [
    `Conversation: ${conversationDisplayName(state.selected)}`,
    `Source title: ${sourceDisplayName(state.selected)}`,
    `Provider: ${[state.selected.source_provider, state.selected.source_service_name, state.selected.chat_type].filter(Boolean).join(" · ") || "imessage"}`,
    `Participants: ${participants}`,
    `Unread count: ${Number(state.selected.unread_count || 0)}`,
    `Pinned: ${Boolean(state.selected.is_pinned)}`,
    `Muted: ${Boolean(state.selected.is_muted)}`,
    `Archived: ${Boolean(state.selected.is_archived)}`,
    `Follow-up: ${hasFollowUp(state.selected) ? followUpLabel(state.selected) : "none"}`,
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
  const dateFrom = el.messageDateFrom.value.trim();
  const dateTo = el.messageDateTo.value.trim();
  const dateRange = [dateFrom || "start", dateTo || "now"].join(" to ");
  if (!query && state.messageSearchView === "all" && !dateFrom && !dateTo) return "none";
  const view = messageSearchViews.find((item) => item.key === state.messageSearchView) || messageSearchViews[0];
  const rows = state.messageSearchResults.slice(0, limit).map((result) => {
    const sender = result.sender_name || result.sender_email || result.direction || "unknown";
    const note = messageNoteText(result) ? ` | private note: ${trim(messageNoteText(result), 180)}` : "";
    return `${formatTime(result.message_timestamp || result.timestamp)} | ${searchResultConversationName(result)} | ${sender}: ${messageSnippet(result, 180)}${note}`;
  });
  return [
    `View: ${view.label}`,
    `Query: ${query || "none"}`,
    `Date range: ${dateFrom || dateTo ? dateRange : "none"}`,
    rows.length ? rows.join("\n") : "No loaded results",
  ].join("\n");
}

function contactContext(limit = 8) {
  if (!state.contacts.length) return "none";
  return state.contacts.slice(0, limit).map((contact) => {
    const organization = contact.organization ? ` | ${contact.organization}` : "";
    const favorite = isFavoriteContact(contact) ? " | favorite" : "";
    const note = contactNoteText(contact) ? ` | private note: ${trim(contactNoteText(contact), 180)}` : "";
    const source = contact.is_saved === false ? " | unsaved participant" : "";
    return `${contactDisplayName(contact)} | ${contactHandleText(contact)}${organization}${favorite}${source}${note}`;
  }).join("\n");
}

function renderCodexModes() {
  for (const button of el.codexModes.querySelectorAll("button[data-codex-mode]")) {
    const active = button.dataset.codexMode === state.codexMode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  }
}

function codexAnswerText() {
  return el.codexAnswer.value.trim();
}

function renderCodexAnswerControls() {
  const hasAnswer = Boolean(codexAnswerText());
  el.askCodexButton.disabled = state.codexBusy;
  el.copyCodexAnswerButton.disabled = state.codexBusy || !hasAnswer;
  el.useCodexDraftButton.disabled = state.codexBusy || !hasAnswer;
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
  renderCodexAnswerControls();
  return prompt;
}

async function askCodex() {
  if (state.codexBusy) return;
  const prompt = buildCodexPrompt();
  state.codexBusy = true;
  el.codexAnswer.value = "";
  el.codexAnswer.placeholder = "Asking Codex";
  el.codexCount.textContent = "Asking Codex";
  renderCodexAnswerControls();
  try {
    const result = await api("/penguin-connect/codex/ask", {
      method: "POST",
      body: JSON.stringify({ prompt }),
    });
    el.codexAnswer.value = result.answer || "";
    el.codexAnswer.placeholder = "Codex answer";
    el.codexCount.textContent = `Codex answer · ${Math.max(0, el.codexAnswer.value.length)} chars`;
  } catch (error) {
    el.codexAnswer.value = "";
    el.codexAnswer.placeholder = error.message;
    el.codexCount.textContent = error.message;
  } finally {
    state.codexBusy = false;
    renderCodexAnswerControls();
  }
}

function useCodexAnswerAsDraft() {
  const answer = codexAnswerText();
  if (!answer) return;
  el.composer.value = answer;
  scheduleDraftSave();
  buildCodexPrompt();
  el.sendState.textContent = "Codex answer moved to draft";
}

async function copyText(value) {
  const text = String(value || "");
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch (_error) {
      // Fall back for browser contexts that expose clipboard but deny writes.
    }
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.top = "-1000px";
  textarea.style.opacity = "0";
  document.body.append(textarea);
  textarea.select();
  try {
    if (!document.execCommand("copy")) {
      throw new Error("Copy failed");
    }
  } finally {
    textarea.remove();
  }
}

function attachmentFilesFor(target = "reply") {
  return target === "draft" ? state.draftAttachments : state.attachments;
}

function setAttachmentFilesFor(target = "reply", files = []) {
  if (target === "draft") {
    state.draftAttachments = files;
  } else {
    state.attachments = files;
  }
}

function attachmentElementsFor(target = "reply") {
  return target === "draft"
    ? { list: el.draftAttachmentList, state: el.draftState }
    : { list: el.attachmentList, state: el.sendState };
}

function addFiles(fileList, target = "reply") {
  const files = Array.from(fileList || []).filter(Boolean);
  const current = attachmentFilesFor(target);
  for (const file of files) {
    current.push(normalizeAttachmentFile(file));
  }
  setAttachmentFilesFor(target, current);
  if (files.length) {
    attachmentElementsFor(target).state.textContent = `${files.length} attachment${files.length === 1 ? "" : "s"} added`;
    if (target === "draft") {
      state.draftAttachmentFolder = "";
      state.draftAttachmentPaths = [];
    }
  }
  renderAttachments(target);
  if (target === "draft") {
    renderDraftPreview();
  } else {
    buildCodexPrompt();
  }
}

function attachmentExtensionForType(type) {
  const normalized = String(type || "").toLowerCase();
  if (normalized.startsWith("image/png")) return ".png";
  if (normalized.startsWith("image/jpeg")) return ".jpg";
  if (normalized.startsWith("image/gif")) return ".gif";
  if (normalized.startsWith("image/webp")) return ".webp";
  if (normalized.startsWith("image/heic")) return ".heic";
  if (normalized.startsWith("audio/mp4") || normalized.startsWith("audio/aac")) return ".m4a";
  if (normalized.startsWith("audio/mpeg")) return ".mp3";
  if (normalized.startsWith("audio/wav")) return ".wav";
  if (normalized.startsWith("audio/webm")) return ".webm";
  if (normalized.startsWith("audio/ogg")) return ".ogg";
  return "";
}

function normalizeAttachmentFile(file) {
  if (!file || file.name) return file;
  const suffix = attachmentExtensionForType(file.type);
  const name = `pasted-attachment-${Date.now()}${suffix}`;
  try {
    return new File([file], name, { type: file.type || "application/octet-stream", lastModified: file.lastModified || Date.now() });
  } catch (_error) {
    return file;
  }
}

function clipboardAttachmentFiles(event) {
  const clipboard = event.clipboardData;
  if (!clipboard) return [];
  const directFiles = Array.from(clipboard.files || []).filter((file) => file && file.size > 0);
  if (directFiles.length) return directFiles;

  return Array.from(clipboard.items || [])
    .filter((item) => item.kind === "file")
    .map((item) => item.getAsFile())
    .filter((file) => file && file.size > 0);
}

function handleAttachmentPaste(event, target = "reply") {
  const files = clipboardAttachmentFiles(event);
  if (!files.length) return;
  event.preventDefault();
  addFiles(files, target);
}

function clearDraftForm() {
  el.draftRecipients.value = "";
  el.recipientListName.value = "";
  el.draftMessage.value = "";
  el.draftState.textContent = "Idle";
  state.activeRecipientListId = "";
  state.draftAttachments = [];
  state.draftAttachmentFolder = "";
  state.draftAttachmentPaths = [];
  renderDraftRecipientChips();
  renderAttachments("draft");
  renderDraftPreview([]);
  renderRecipientLists();
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
    const attachments = await filesAsBrowserAttachments(state.draftAttachments);
    const result = await api("/penguin-connect/messages/draft", {
      method: "POST",
      body: JSON.stringify({
        participants,
        message: el.draftMessage.value,
        attachments,
        copy_to_clipboard: el.draftCopyToggle.checked,
        open_messages: el.draftOpenToggle.checked,
        open_attachments: el.draftOpenAttachmentsToggle.checked,
      }),
    });
    state.draftAttachmentFolder = result.attachment_folder || "";
    state.draftAttachmentPaths = result.attachment_paths || [];
    const actions = [
      result.copied ? "copied" : "",
      result.opened_messages ? "opened" : "",
      result.opened_attachments ? "files opened" : result.attachment_count ? "files staged" : "",
    ].filter(Boolean).join(" + ");
    renderDraftPreview(result.participants || participants, result.draft || "");
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
    state.contactSource = "all";
    renderContactSourceFilters();
    await loadContacts({ force: true });
    await loadThreadContactMatches();
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
el.contactSearch.addEventListener("input", () => {
  state.selectedContactKeys.clear();
  scheduleContactSearch();
});
el.contactSourceFilters.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-contact-source]");
  if (!button) return;
  state.contactSource = contactSources.some((source) => source.key === button.dataset.contactSource)
    ? button.dataset.contactSource
    : "all";
  state.selectedContactKeys.clear();
  loadContacts({
    force: state.contactSource === "participants"
      || state.contactSource === "favorites"
      || state.contactSource === "noted"
      || state.contactSource === "contacts"
      || el.contactSearch.value.trim().length >= 2,
  });
});
el.globalMessageSearch.addEventListener("input", scheduleMessageSearch);
el.messageDateFrom.addEventListener("input", scheduleMessageSearch);
el.messageDateTo.addEventListener("input", scheduleMessageSearch);
el.clearMessageDatesButton.addEventListener("click", () => {
  el.messageDateFrom.value = "";
  el.messageDateTo.value = "";
  loadMessageSearch();
});
el.globalMessageSearchFilters.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-message-search-view]");
  if (!button) return;
  state.messageSearchView = messageSearchViews.some((view) => view.key === button.dataset.messageSearchView)
    ? button.dataset.messageSearchView
    : "all";
  loadMessageSearch();
});
el.contactRefreshButton.addEventListener("click", async () => {
  el.contactRefreshButton.disabled = true;
  el.contactStatus.textContent = "Refreshing Contacts";
  try {
    await api("/penguin-connect/contacts/refresh", { method: "POST", body: "{}" });
    await loadContacts({ force: true });
    await loadThreadContactMatches();
  } catch (error) {
    el.contactStatus.textContent = error.message;
  } finally {
    el.contactRefreshButton.disabled = false;
  }
});
el.contactSelectVisibleButton.addEventListener("click", selectVisibleContacts);
el.contactAddVisibleButton.addEventListener("click", addVisibleContactsToDraft);
el.contactCopyVisibleButton.addEventListener("click", copyVisibleContacts);
el.contactSaveVisibleButton.addEventListener("click", saveVisibleContactsAsRecipientList);
el.contactClearSelectedButton.addEventListener("click", clearSelectedContacts);
el.messageFilter.addEventListener("input", renderMessages);
el.messageViewFilters.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-message-view]");
  if (!button) return;
  state.messageView = messageViews.some((view) => view.key === button.dataset.messageView)
    ? button.dataset.messageView
    : "all";
  renderMessages();
});
el.mediaFilters.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-media-view]");
  if (!button) return;
  state.mediaView = mediaViews.some((view) => view.key === button.dataset.mediaView)
    ? button.dataset.mediaView
    : "all";
  renderThreadMedia();
});
el.draftRecipients.addEventListener("input", () => {
  const recipients = uniqueRecipientValues(draftRecipientValues());
  renderDraftRecipientChips(recipients);
  renderDraftPreview(recipients);
});
el.draftRecipients.addEventListener("blur", (event) => {
  if (event.relatedTarget && el.draftRecipientChips.contains(event.relatedTarget)) return;
  setDraftRecipients(draftRecipientValues());
});
el.draftMessage.addEventListener("input", () => renderDraftPreview());
el.sendButton.addEventListener("click", sendMessage);
el.voiceMemoButton.addEventListener("click", () => toggleVoiceMemoRecording("reply"));
el.draftVoiceMemoButton.addEventListener("click", () => toggleVoiceMemoRecording("draft"));
el.pinButton.addEventListener("click", () => setConversationManagement({ pinned: !Boolean(state.selected?.is_pinned) }));
el.muteButton.addEventListener("click", () => setConversationManagement({ muted: !Boolean(state.selected?.is_muted) }));
el.archiveButton.addEventListener("click", () => setConversationManagement({ archived: !Boolean(state.selected?.is_archived) }));
el.saveManagementButton.addEventListener("click", saveConversationManagement);
el.threadLocalTitle.addEventListener("input", () => {
  el.managementState.textContent = state.selected ? "Unsaved" : "No thread";
  buildCodexPrompt();
});
el.threadFollowUpAt.addEventListener("input", () => {
  el.managementState.textContent = state.selected ? "Unsaved" : "No thread";
  buildCodexPrompt();
});
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
  refreshLocalMessages();
});
el.copyThreadButton.addEventListener("click", async () => {
  await copyText(threadText(40));
  el.sendState.textContent = "Thread copied";
});
el.fileInput.addEventListener("change", (event) => {
  addFiles(event.target.files);
  event.target.value = "";
});
el.draftFileInput.addEventListener("change", (event) => {
  addFiles(event.target.files, "draft");
  event.target.value = "";
});
el.threadPeopleAddAllButton.addEventListener("click", addThreadParticipantsToDraft);
el.threadPeopleSaveListButton.addEventListener("click", saveThreadParticipantsAsRecipientList);
el.stageDraftButton.addEventListener("click", stageDraft);
el.copyDraftRecipientsButton.addEventListener("click", copyDraftRecipients);
el.copyDraftBodyButton.addEventListener("click", copyDraftBody);
el.copyDraftPreviewButton.addEventListener("click", copyDraftPreview);
el.openAddressedDraftButton.addEventListener("click", openAddressedDraft);
el.saveRecipientListButton.addEventListener("click", saveRecipientList);
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
el.draftAttachmentDrop.addEventListener("dragover", (event) => {
  event.preventDefault();
  el.draftAttachmentDrop.classList.add("dragging");
});
el.draftAttachmentDrop.addEventListener("dragleave", () => el.draftAttachmentDrop.classList.remove("dragging"));
el.draftAttachmentDrop.addEventListener("drop", (event) => {
  event.preventDefault();
  el.draftAttachmentDrop.classList.remove("dragging");
  addFiles(event.dataTransfer.files, "draft");
});
el.composer.addEventListener("paste", handleAttachmentPaste);
el.attachmentDrop.addEventListener("paste", handleAttachmentPaste);
el.draftMessage.addEventListener("paste", (event) => handleAttachmentPaste(event, "draft"));
el.draftAttachmentDrop.addEventListener("paste", (event) => handleAttachmentPaste(event, "draft"));
el.composer.addEventListener("input", () => {
  scheduleDraftSave();
  buildCodexPrompt();
});
el.buildPromptButton.addEventListener("click", buildCodexPrompt);
el.copyPromptButton.addEventListener("click", async () => {
  await copyText(buildCodexPrompt());
  el.sendState.textContent = "Codex prompt copied";
});
el.askCodexButton.addEventListener("click", askCodex);
el.copyCodexAnswerButton.addEventListener("click", async () => {
  const answer = codexAnswerText();
  if (!answer) return;
  await copyText(answer);
  el.sendState.textContent = "Codex answer copied";
});
el.useCodexDraftButton.addEventListener("click", useCodexAnswerAsDraft);
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
el.bulkLabelsInput.addEventListener("input", renderConversations);
el.bulkLabelButton.addEventListener("click", bulkApplyLabels);
el.bulkRemoveLabelButton.addEventListener("click", bulkRemoveLabels);
el.bulkFollowUpAt.addEventListener("input", renderConversations);
el.bulkSetFollowUpButton.addEventListener("click", bulkSetFollowUp);
el.bulkClearFollowUpButton.addEventListener("click", bulkClearFollowUps);
el.bulkClearDraftsButton.addEventListener("click", bulkClearDrafts);
el.bulkMarkReadButton.addEventListener("click", bulkMarkSelectedRead);
el.bulkPinButton.addEventListener("click", bulkPinSelected);
el.bulkMuteButton.addEventListener("click", bulkMuteSelected);
el.bulkArchiveButton.addEventListener("click", bulkArchiveSelected);

renderAllEmojiButtons();
renderAllVoiceMemoControls();
renderMessages();
renderContacts();
renderContactSourceFilters();
renderDraftRecipientChips();
renderDraftPreview();
renderRecipientLists();
renderMessageSearchFilters();
renderMessageSearchResults();
renderThreadControls();
renderThreadPeople();
renderThreadMedia();
renderCodexModes();
renderCodexAnswerControls();
loadStatus();
loadConversations();
loadContacts();
loadRecipientLists();
