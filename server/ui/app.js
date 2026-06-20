const newChatDraftStorageKey = "penguin-connect:new-chat-draft:v1";

function autoRefreshIntervalFromUrl() {
  const value = Number(new URLSearchParams(window.location.search).get("auto_refresh_ms"));
  return Number.isFinite(value) && value >= 1000 && value <= 60000 ? value : 10000;
}

const autoRefreshIntervalMs = autoRefreshIntervalFromUrl();
const activityStatusHoldMs = 45000;

const state = {
  conversations: [],
  selected: null,
  messages: [],
  messagesLoading: false,
  messageLimit: 200,
  messageLimitStep: 200,
  messageLimitMax: 1000,
  replyContext: null,
  threadActionMessage: "",
  attachments: [],
  draftAttachments: [],
  draftMediaAttachments: [],
  draftAttachmentFolder: "",
  draftAttachmentPaths: [],
  contacts: [],
  contactSourceCounts: {},
  contactSource: "all",
  contactSort: "default",
  contactLimit: 20,
  contactLimitStep: 20,
  contactLimitMax: 100,
  contactsLoading: false,
  contactLoadToken: 0,
  contactSearchTimer: null,
  draftRecipientSuggestTimer: null,
  draftRecipientSuggestToken: 0,
  draftRecipientSuggestionQuery: "",
  draftRecipientSuggestions: [],
  draftRecipientContactCache: [],
  draftThreadResolveTimer: null,
  draftThreadResolveToken: 0,
  draftThreadResolveKey: "",
  draftThreadResolving: false,
  draftThreadMatch: null,
  activeContactKey: "",
  activeContact: null,
  activeContactMessageKey: "",
  activeContactMessages: [],
  activeContactMessagesLoading: false,
  activeContactMessagesBulkBusy: false,
  activeContactMessagesToken: 0,
  activeContactMessagesError: "",
  activeContactMessageNoteEditorId: "",
  activeContactMessagesLimit: 3,
  activeContactMessagesLimitStep: 5,
  activeContactMessagesLimitMax: 25,
  contactNoteEditorKey: "",
  selectedContactKeys: new Set(),
  recipientLists: [],
  activeRecipientListId: "",
  threadContactMatches: {},
  threadContactToken: 0,
  messageSearchResults: [],
  messageSearchTimer: null,
  messageSearchView: "all",
  messageSearchLimit: 30,
  messageSearchLimitStep: 30,
  messageSearchLimitMax: 100,
  messageSearchLoading: false,
  messageSearchBulkBusy: false,
  messageSearchToken: 0,
  messageSearchNoteEditorId: "",
  focusMessageId: "",
  messageView: "all",
  messageNoteEditorId: "",
  messageBulkBusy: false,
  mediaView: "all",
  conversationView: "inbox",
  conversationSort: "recent",
  conversationLabel: "",
  conversationActivitySnapshot: new Map(),
  activityStatusUntil: 0,
  selectedConversationIds: new Set(),
  bulkBusy: false,
  bulkMessage: "",
  localRefreshBusy: false,
  autoRefreshBusy: false,
  autoRefreshTimerId: 0,
  draftSaveTimer: null,
  replyMediaAttachments: [],
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

const attachmentPreviewUrls = new WeakMap();

const el = {
  statusLine: document.querySelector("#statusLine"),
  refreshButton: document.querySelector("#refreshButton"),
  conversationSearch: document.querySelector("#conversationSearch"),
  conversationFilters: document.querySelector("#conversationFilters"),
  conversationSort: document.querySelector("#conversationSort"),
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
  bulkAddPeopleButton: document.querySelector("#bulkAddPeopleButton"),
  bulkCopyPeopleButton: document.querySelector("#bulkCopyPeopleButton"),
  bulkSavePeopleButton: document.querySelector("#bulkSavePeopleButton"),
  bulkCreatePeopleButton: document.querySelector("#bulkCreatePeopleButton"),
  bulkClearDraftsButton: document.querySelector("#bulkClearDraftsButton"),
  conversationList: document.querySelector("#conversationList"),
  contactRefreshButton: document.querySelector("#contactRefreshButton"),
  contactSearch: document.querySelector("#contactSearch"),
  contactSourceFilters: document.querySelector("#contactSourceFilters"),
  contactSort: document.querySelector("#contactSort"),
  contactSelectVisibleButton: document.querySelector("#contactSelectVisibleButton"),
  contactAddVisibleButton: document.querySelector("#contactAddVisibleButton"),
  contactCopyVisibleButton: document.querySelector("#contactCopyVisibleButton"),
  contactSaveVisibleButton: document.querySelector("#contactSaveVisibleButton"),
  contactFavoriteSelectedButton: document.querySelector("#contactFavoriteSelectedButton"),
  contactUnfavoriteSelectedButton: document.querySelector("#contactUnfavoriteSelectedButton"),
  contactCreateVisibleButton: document.querySelector("#contactCreateVisibleButton"),
  contactClearSelectedButton: document.querySelector("#contactClearSelectedButton"),
  contactStatus: document.querySelector("#contactStatus"),
  contactInspector: document.querySelector("#contactInspector"),
  contactMoreBar: document.querySelector("#contactMoreBar"),
  contactCount: document.querySelector("#contactCount"),
  loadMoreContactsButton: document.querySelector("#loadMoreContactsButton"),
  contactList: document.querySelector("#contactList"),
  threadProvider: document.querySelector("#threadProvider"),
  threadTitle: document.querySelector("#threadTitle"),
  syncButton: document.querySelector("#syncButton"),
  openMessagesButton: document.querySelector("#openMessagesButton"),
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
  threadPeopleCopyAllButton: document.querySelector("#threadPeopleCopyAllButton"),
  threadPeopleSaveListButton: document.querySelector("#threadPeopleSaveListButton"),
  threadPeopleCreateAllButton: document.querySelector("#threadPeopleCreateAllButton"),
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
  messageSearchMoreBar: document.querySelector("#messageSearchMoreBar"),
  messageSearchCount: document.querySelector("#messageSearchCount"),
  starSearchLoadedButton: document.querySelector("#starSearchLoadedButton"),
  markSearchReadButton: document.querySelector("#markSearchReadButton"),
  markSearchUnreadButton: document.querySelector("#markSearchUnreadButton"),
  addSearchSendersButton: document.querySelector("#addSearchSendersButton"),
  addSearchParticipantsButton: document.querySelector("#addSearchParticipantsButton"),
  saveSearchSendersButton: document.querySelector("#saveSearchSendersButton"),
  saveSearchParticipantsButton: document.querySelector("#saveSearchParticipantsButton"),
  createSearchSendersButton: document.querySelector("#createSearchSendersButton"),
  createSearchParticipantsButton: document.querySelector("#createSearchParticipantsButton"),
  loadMoreSearchButton: document.querySelector("#loadMoreSearchButton"),
  messageDateFrom: document.querySelector("#messageDateFrom"),
  messageDateTo: document.querySelector("#messageDateTo"),
  clearMessageDatesButton: document.querySelector("#clearMessageDatesButton"),
  messageSearchStatus: document.querySelector("#messageSearchStatus"),
  messageSearchResults: document.querySelector("#messageSearchResults"),
  messageViewFilters: document.querySelector("#messageViewFilters"),
  loadedMessageCount: document.querySelector("#loadedMessageCount"),
  copyVisibleMessagesButton: document.querySelector("#copyVisibleMessagesButton"),
  starVisibleMessagesButton: document.querySelector("#starVisibleMessagesButton"),
  markVisibleMessagesReadButton: document.querySelector("#markVisibleMessagesReadButton"),
  markVisibleMessagesUnreadButton: document.querySelector("#markVisibleMessagesUnreadButton"),
  loadMoreMessagesButton: document.querySelector("#loadMoreMessagesButton"),
  messageFilter: document.querySelector("#messageFilter"),
  messageList: document.querySelector("#messageList"),
  senderBadge: document.querySelector("#senderBadge"),
  replyContext: document.querySelector("#replyContext"),
  replyContextText: document.querySelector("#replyContextText"),
  replyQuoteToggle: document.querySelector("#replyQuoteToggle"),
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
  draftRecipientSuggestions: document.querySelector("#draftRecipientSuggestions"),
  draftRecipientChips: document.querySelector("#draftRecipientChips"),
  recipientListName: document.querySelector("#recipientListName"),
  saveRecipientListButton: document.querySelector("#saveRecipientListButton"),
  recipientLists: document.querySelector("#recipientLists"),
  draftMessage: document.querySelector("#draftMessage"),
  draftEmojiRow: document.querySelector("#draftEmojiRow"),
  draftThreadMatch: document.querySelector("#draftThreadMatch"),
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
  draftCreateUnknownButton: document.querySelector("#draftCreateUnknownButton"),
  openAddressedDraftButton: document.querySelector("#openAddressedDraftButton"),
  draftCopyToggle: document.querySelector("#draftCopyToggle"),
  draftOpenToggle: document.querySelector("#draftOpenToggle"),
  draftOpenAttachmentsToggle: document.querySelector("#draftOpenAttachmentsToggle"),
  sendDraftButton: document.querySelector("#sendDraftButton"),
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
  useCodexNewChatButton: document.querySelector("#useCodexNewChatButton"),
};

function savedNewChatDraft() {
  try {
    const raw = window.localStorage?.getItem(newChatDraftStorageKey);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch (_error) {
    return null;
  }
}

function saveNewChatDraft() {
  try {
    const snapshot = {
      recipients: el.draftRecipients.value,
      recipientListName: el.recipientListName.value,
      message: el.draftMessage.value,
      copyToClipboard: Boolean(el.draftCopyToggle.checked),
      openAddressed: Boolean(el.draftOpenToggle.checked),
      openAttachments: Boolean(el.draftOpenAttachmentsToggle.checked),
      draftMediaAttachments: state.draftMediaAttachments.map((item) => ({
        path: item.path,
        label: item.label,
        type: item.type,
      })),
      activeRecipientListId: state.activeRecipientListId || "",
      updatedAt: new Date().toISOString(),
    };
    window.localStorage?.setItem(newChatDraftStorageKey, JSON.stringify(snapshot));
  } catch (_error) {
    // Local storage can be unavailable in private or restricted browser contexts.
  }
}

function clearSavedNewChatDraft() {
  try {
    window.localStorage?.removeItem(newChatDraftStorageKey);
  } catch (_error) {
    // Ignore storage cleanup failures; the form itself still clears.
  }
}

function restoreNewChatDraft() {
  const snapshot = savedNewChatDraft();
  if (!snapshot) return false;
  el.draftRecipients.value = typeof snapshot.recipients === "string" ? snapshot.recipients : "";
  el.recipientListName.value = typeof snapshot.recipientListName === "string" ? snapshot.recipientListName : "";
  el.draftMessage.value = typeof snapshot.message === "string" ? snapshot.message : "";
  el.draftCopyToggle.checked = typeof snapshot.copyToClipboard === "boolean" ? snapshot.copyToClipboard : el.draftCopyToggle.checked;
  el.draftOpenToggle.checked = typeof snapshot.openAddressed === "boolean" ? snapshot.openAddressed : el.draftOpenToggle.checked;
  el.draftOpenAttachmentsToggle.checked = typeof snapshot.openAttachments === "boolean" ? snapshot.openAttachments : el.draftOpenAttachmentsToggle.checked;
  state.draftMediaAttachments = Array.isArray(snapshot.draftMediaAttachments)
    ? snapshot.draftMediaAttachments.map((item) => ({
      path: typeof item?.path === "string" ? item.path : "",
      label: typeof item?.label === "string" ? item.label : "",
      type: typeof item?.type === "string" ? item.type : "file",
    })).filter((item) => item.path)
    : [];
  state.activeRecipientListId = typeof snapshot.activeRecipientListId === "string" ? snapshot.activeRecipientListId : "";
  return Boolean(
    el.draftRecipients.value.trim()
      || el.recipientListName.value.trim()
      || el.draftMessage.value.trim()
      || state.draftMediaAttachments.length
      || state.activeRecipientListId
  );
}

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

const contactSortLabels = {
  default: "Default",
  name: "Name",
  favorite: "Favorites first",
  noted: "Notes first",
  saved: "Saved first",
  unsaved: "Unsaved first",
  recent: "Recently imported",
};

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

const conversationSortLabels = {
  recent: "Recent",
  priority: "Priority",
  unread: "Unread",
  followup: "Follow-up",
  name: "A-Z",
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

function highlightTerms(value) {
  const terms = String(value || "")
    .toLowerCase()
    .split(/\s+/)
    .map((item) => item.trim())
    .filter((item) => item.length >= 2);
  return [...new Set(terms)].slice(0, 8);
}

function appendHighlightedText(target, value, terms = []) {
  const text = String(value || "");
  target.replaceChildren();
  if (!text) return;
  const cleanTerms = terms
    .map((term) => String(term || "").toLowerCase())
    .filter(Boolean)
    .sort((a, b) => b.length - a.length);
  if (!cleanTerms.length) {
    target.append(document.createTextNode(text));
    return;
  }

  const lowerText = text.toLowerCase();
  let cursor = 0;
  while (cursor < text.length) {
    let matchIndex = -1;
    let matchTerm = "";
    for (const term of cleanTerms) {
      const index = lowerText.indexOf(term, cursor);
      if (index === -1) continue;
      if (
        matchIndex === -1
        || index < matchIndex
        || (index === matchIndex && term.length > matchTerm.length)
      ) {
        matchIndex = index;
        matchTerm = term;
      }
    }
    if (matchIndex === -1) {
      target.append(document.createTextNode(text.slice(cursor)));
      return;
    }
    if (matchIndex > cursor) {
      target.append(document.createTextNode(text.slice(cursor, matchIndex)));
    }
    const mark = document.createElement("mark");
    mark.className = "search-highlight";
    mark.textContent = text.slice(matchIndex, matchIndex + matchTerm.length);
    target.append(mark);
    cursor = matchIndex + matchTerm.length;
  }
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

function contactNameSortValue(contact) {
  return contactDisplayName(contact).toLowerCase();
}

function contactHandleSortValue(contact) {
  return String(contactRecipientHandle(contact) || contactHandleText(contact) || "").toLowerCase();
}

function contactImportedSortValue(contact) {
  const value = Date.parse(contact.imported_at || "");
  return Number.isNaN(value) ? 0 : value;
}

function compareContactName(a, b) {
  return contactNameSortValue(a).localeCompare(contactNameSortValue(b))
    || contactHandleSortValue(a).localeCompare(contactHandleSortValue(b));
}

function compareContactFavorite(a, b) {
  return Number(isFavoriteContact(b)) - Number(isFavoriteContact(a)) || compareContactName(a, b);
}

function compareContactNoted(a, b) {
  return Number(Boolean(contactNoteText(b))) - Number(Boolean(contactNoteText(a))) || compareContactName(a, b);
}

function compareContactSaved(a, b) {
  return Number(Boolean(b.is_saved)) - Number(Boolean(a.is_saved)) || compareContactName(a, b);
}

function compareContactUnsaved(a, b) {
  return Number(!b.is_saved) - Number(!a.is_saved) || compareContactName(a, b);
}

function compareContactRecent(a, b) {
  return contactImportedSortValue(b) - contactImportedSortValue(a) || compareContactName(a, b);
}

function compareContacts(a, b) {
  if (state.contactSort === "name") return compareContactName(a, b);
  if (state.contactSort === "favorite") return compareContactFavorite(a, b);
  if (state.contactSort === "noted") return compareContactNoted(a, b);
  if (state.contactSort === "saved") return compareContactSaved(a, b);
  if (state.contactSort === "unsaved") return compareContactUnsaved(a, b);
  if (state.contactSort === "recent") return compareContactRecent(a, b);
  return 0;
}

function visibleContacts() {
  return state.contacts
    .map((contact, index) => ({ contact, index }))
    .sort((a, b) => compareContacts(a.contact, b.contact) || a.index - b.index)
    .map((item) => item.contact);
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
    refreshDraftRecipientChips();
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
  refreshDraftRecipientChips();
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

function conversationContactContextItems(conversation) {
  return Array.isArray(conversation?.contact_context)
    ? conversation.contact_context.filter((contact) => contact && typeof contact === "object")
    : [];
}

function conversationContactContextText(conversation) {
  const items = conversationContactContextItems(conversation).slice(0, 3).map((contact) => {
    const name = String(contact.display_name || contact.primary_handle || contact.handle || "").trim();
    const handle = String(contact.primary_handle || contact.handle || "").trim();
    const organization = String(contact.organization || "").trim();
    const note = String(contact.contact_note || "").trim();
    return [
      name,
      handle && handle !== name ? handle : "",
      organization && organization !== name ? organization : "",
      note ? `note: ${trim(note, 72)}` : "",
    ].filter(Boolean).join(" · ");
  }).filter(Boolean);
  if (!items.length) return "";
  const extra = conversationContactContextItems(conversation).length - items.length;
  return `${items.join(" / ")}${extra > 0 ? ` / +${extra} more` : ""}`;
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

function pinnedSortDiff(a, b) {
  return Number(b.is_pinned) - Number(a.is_pinned);
}

function unreadSortValue(conversation) {
  return Number(conversation.unread_count || 0);
}

function dueFollowUpSortValue(conversation) {
  return followUpStatus(conversation) === "due" ? 1 : 0;
}

function conversationNameSortValue(conversation) {
  return conversationDisplayName(conversation).toLowerCase();
}

function compareConversationRecent(a, b) {
  return conversationSortValue(b) - conversationSortValue(a)
    || conversationNameSortValue(a).localeCompare(conversationNameSortValue(b));
}

function compareConversationFollowUp(a, b) {
  return pinnedSortDiff(a, b) || followUpSortValue(a) - followUpSortValue(b) || compareConversationRecent(a, b);
}

function compareConversationPriority(a, b) {
  return pinnedSortDiff(a, b)
    || Number(conversationNeedsReply(b)) - Number(conversationNeedsReply(a))
    || unreadSortValue(b) - unreadSortValue(a)
    || dueFollowUpSortValue(b) - dueFollowUpSortValue(a)
    || Number(hasFollowUp(b)) - Number(hasFollowUp(a))
    || compareConversationRecent(a, b);
}

function compareConversationUnread(a, b) {
  return pinnedSortDiff(a, b)
    || unreadSortValue(b) - unreadSortValue(a)
    || Number(conversationNeedsReply(b)) - Number(conversationNeedsReply(a))
    || compareConversationRecent(a, b);
}

function compareConversationName(a, b) {
  return pinnedSortDiff(a, b)
    || conversationNameSortValue(a).localeCompare(conversationNameSortValue(b))
    || compareConversationRecent(a, b);
}

function compareConversations(a, b) {
  if (state.conversationSort === "priority") return compareConversationPriority(a, b);
  if (state.conversationSort === "unread") return compareConversationUnread(a, b);
  if (state.conversationSort === "followup") return compareConversationFollowUp(a, b);
  if (state.conversationSort === "name") return compareConversationName(a, b);
  return pinnedSortDiff(a, b) || compareConversationRecent(a, b);
}

function conversationActivitySignature(conversation) {
  return [
    conversation.last_message_provider_id || "",
    conversation.last_message_ts || "",
    Number(conversation.unread_count || 0),
  ].join("|");
}

function updateConversationActivitySnapshot(conversations = state.conversations) {
  state.conversationActivitySnapshot = new Map(
    conversations
      .filter((conversation) => conversation?.conversation_id)
      .map((conversation) => [conversation.conversation_id, conversationActivitySignature(conversation)])
  );
}

function newConversationActivity(conversations) {
  if (!state.conversationActivitySnapshot.size) return [];
  return conversations.filter((conversation) => {
    if (!conversation?.conversation_id || !conversationNeedsReply(conversation)) return false;
    const current = conversationActivitySignature(conversation);
    const previous = state.conversationActivitySnapshot.get(conversation.conversation_id);
    return !previous || previous !== current;
  });
}

function announceNewConversationActivity(conversations) {
  if (!conversations.length) return;
  const first = conversations[0];
  const label = conversationDisplayName(first);
  state.activityStatusUntil = Date.now() + activityStatusHoldMs;
  el.statusLine.textContent = conversations.length === 1
    ? `New message · ${label}`
    : `${conversations.length} conversations updated · ${label}`;
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
  const knownSort = Object.prototype.hasOwnProperty.call(conversationSortLabels, state.conversationSort);
  if (!knownSort) state.conversationSort = "recent";
  el.conversationSort.value = state.conversationSort;
  el.conversationSort.title = `Sort conversations by ${conversationSortLabels[state.conversationSort]}`;
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
    if (state.conversationSort === "recent" && state.conversationView === "followup") {
      const followUpDiff = followUpSortValue(a) - followUpSortValue(b);
      return pinnedSortDiff(a, b) || followUpDiff || compareConversationRecent(a, b);
    }
    return compareConversations(a, b);
  });
}

function selectedVisibleConversationIndex(rows = visibleConversationRows()) {
  if (!state.selected) return -1;
  return rows.findIndex((conversation) => conversation.conversation_id === state.selected.conversation_id);
}

function scrollSelectedConversationIntoView() {
  if (!state.selected) return;
  requestAnimationFrame(() => {
    const row = [...el.conversationList.querySelectorAll("[data-conversation-id]")]
      .find((item) => item.dataset.conversationId === state.selected.conversation_id);
    row?.scrollIntoView({ block: "nearest" });
  });
}

async function navigateVisibleConversation(direction) {
  const rows = visibleConversationRows();
  if (!rows.length) return false;
  const currentIndex = selectedVisibleConversationIndex(rows);
  let nextIndex = 0;
  if (currentIndex < 0) {
    nextIndex = direction < 0 ? rows.length - 1 : 0;
  } else {
    nextIndex = (currentIndex + direction + rows.length) % rows.length;
  }
  state.focusMessageId = "";
  await selectConversation(rows[nextIndex]);
  return true;
}

function isShortcutEditableTarget(target) {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  return ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

function focusControl(control) {
  if (!control) return;
  control.focus();
  if (typeof control.select === "function") {
    control.select();
  }
}

function handleGlobalShortcuts(event) {
  if (event.defaultPrevented || event.isComposing || isShortcutEditableTarget(event.target)) return;
  if (event.altKey || event.ctrlKey || event.metaKey) return;
  const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
  if (key === "ArrowDown" || key === "j") {
    event.preventDefault();
    navigateVisibleConversation(1);
    return;
  }
  if (key === "ArrowUp" || key === "k") {
    event.preventDefault();
    navigateVisibleConversation(-1);
    return;
  }
  if (key === "/") {
    event.preventDefault();
    focusControl(el.conversationSearch);
    return;
  }
  if (key === "f") {
    event.preventDefault();
    focusControl(el.globalMessageSearch);
    return;
  }
  if (key === "c") {
    event.preventDefault();
    focusControl(el.contactSearch);
    return;
  }
  if (key === "n") {
    event.preventDefault();
    focusControl(el.draftRecipients);
    return;
  }
  if (key === "r" && state.selected) {
    event.preventDefault();
    focusControl(el.composer);
  }
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
  const selectedPeopleCount = selectedConversationParticipantHandles(selectedRows).length;
  const selectedCreatablePeopleCount = selectedConversationCreatablePeople(selectedRows).length;
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
  el.bulkAddPeopleButton.disabled = state.bulkBusy || selectedPeopleCount === 0;
  el.bulkAddPeopleButton.textContent = selectedPeopleCount ? `Add ${selectedPeopleCount} people` : "Add people";
  el.bulkCopyPeopleButton.disabled = state.bulkBusy || selectedPeopleCount === 0;
  el.bulkCopyPeopleButton.textContent = selectedPeopleCount ? `Copy ${selectedPeopleCount} people` : "Copy people";
  el.bulkSavePeopleButton.disabled = state.bulkBusy || selectedPeopleCount === 0;
  el.bulkSavePeopleButton.textContent = selectedPeopleCount ? `Save ${selectedPeopleCount} people` : "Save people";
  el.bulkCreatePeopleButton.disabled = state.bulkBusy || selectedCreatablePeopleCount === 0;
  el.bulkCreatePeopleButton.textContent = selectedCreatablePeopleCount ? `Create ${selectedCreatablePeopleCount}` : "Create people";
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
    || (state.messageSearchView === "unread" && !isUnreadMessage(result))
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

function attachmentLikeFromFile(file) {
  return {
    transfer_name: file?.name || "",
    filename: file?.name || "",
    mime_type: file?.type || "",
  };
}

function attachmentFileKind(file) {
  const attachment = attachmentLikeFromFile(file);
  if (isAudioAttachment(attachment)) return "audio";
  if (isImageAttachment(attachment)) return "image";
  return "file";
}

function attachmentFileKindLabel(file) {
  const kind = attachmentFileKind(file);
  if (kind === "audio") {
    return /^voice[-_\s]?memo/i.test(file?.name || "") ? "Voice memo" : "Audio";
  }
  if (kind === "image") return "Image";
  return "File";
}

function attachmentSizeLabel(file) {
  const size = Number(file?.size || 0);
  if (!Number.isFinite(size) || size <= 0) return "";
  if (size >= 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.ceil(size / 1024))} KB`;
}

function attachmentFileLabel(file) {
  return [
    attachmentFileKindLabel(file),
    basename(file?.name || "attachment"),
    attachmentSizeLabel(file),
  ].filter(Boolean).join(" · ");
}

function attachmentPreviewUrl(file) {
  if (!file || typeof URL === "undefined" || !URL.createObjectURL) return "";
  const current = attachmentPreviewUrls.get(file);
  if (current) return current;
  const url = URL.createObjectURL(file);
  attachmentPreviewUrls.set(file, url);
  return url;
}

function revokeAttachmentPreview(file) {
  const url = file ? attachmentPreviewUrls.get(file) : "";
  if (!url) return;
  if (typeof URL !== "undefined" && URL.revokeObjectURL) {
    URL.revokeObjectURL(url);
  }
  attachmentPreviewUrls.delete(file);
}

function revokeAttachmentPreviews(files) {
  for (const file of files || []) {
    revokeAttachmentPreview(file);
  }
}

function attachmentUrl(message, index, conversationId = "") {
  const resolvedConversationId = conversationId || state.selected?.conversation_id || message?.conversation_id || "";
  if (!resolvedConversationId || !message.provider_message_id) return "";
  const encodedConversationId = encodeURIComponent(resolvedConversationId);
  const messageId = encodeURIComponent(message.provider_message_id);
  return `/penguin-connect/conversations/${encodedConversationId}/attachments/${index}?provider_message_id=${messageId}`;
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

function attachmentKindLabel(attachment) {
  if (isAudioAttachment(attachment)) return "Audio";
  if (isImageAttachment(attachment)) return "Image";
  return "File";
}

function renderCompactAttachmentChips(message, { conversationId = "", limit = 4, terms = [] } = {}) {
  const attachments = attachmentRows(message);
  if (!attachments.length) return null;

  const wrapper = document.createElement("div");
  wrapper.className = "compact-attachments";
  for (const [index, attachment] of attachments.slice(0, limit).entries()) {
    const url = attachmentLocalPath(attachment) ? attachmentUrl(message, index, conversationId) : "";
    const chip = document.createElement(url ? "a" : "span");
    chip.className = [
      "compact-attachment",
      isAudioAttachment(attachment) ? "audio" : "",
      isImageAttachment(attachment) ? "image" : "",
    ].filter(Boolean).join(" ");
    if (url) {
      chip.href = url;
      chip.target = "_blank";
      chip.rel = "noopener";
      chip.title = "Open attachment";
    }

    const kind = document.createElement("span");
    kind.className = "compact-attachment-kind";
    kind.textContent = attachmentKindLabel(attachment);
    const label = document.createElement("span");
    label.className = "compact-attachment-label";
    appendHighlightedText(label, attachmentLabel(attachment), terms);
    chip.append(kind, label);
    wrapper.append(chip);
  }

  if (attachments.length > limit) {
    const more = document.createElement("span");
    more.className = "compact-attachment more";
    more.textContent = `+${attachments.length - limit}`;
    wrapper.append(more);
  }

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

function messageDraftText(message) {
  const body = String(message.body_text || message.text || "").trim();
  return body || messageCopyText(message);
}

function useMessageAsNewChatDraft(message) {
  const draft = messageDraftText(message);
  if (!draft) return;
  el.draftMessage.value = draft;
  renderDraftPreview();
  saveNewChatDraft();
  el.draftState.textContent = "Message moved to new chat draft";
  el.draftMessage.focus();
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
  el.replyQuoteToggle.checked = true;
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
  state.activeContactMessages = state.activeContactMessages.map((item) => (
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

async function toggleMessageSearchResultRead(result) {
  if (!result?.conversation_id || !result.provider_message_id) return;
  const nextUnread = !isUnreadMessage(result);
  try {
    const response = await api(`/penguin-connect/conversations/${encodeURIComponent(result.conversation_id)}/messages/management`, {
      method: "POST",
      body: JSON.stringify({
        provider_message_id: result.provider_message_id,
        unread: nextUnread,
      }),
    });
    mergeMessageManagement(response);
    el.messageSearchStatus.textContent = nextUnread ? "Search result marked unread" : "Search result marked read";
    renderMessageSearchResults();
    renderConversations();
    renderThreadHeader();
    renderThreadControls();
    renderMessages();
    buildCodexPrompt();
  } catch (error) {
    el.messageSearchStatus.textContent = error.message;
  }
}

function messageSearchManageableResults() {
  const seen = new Set();
  const results = [];
  for (const result of state.messageSearchResults) {
    if (!result?.conversation_id || !result.provider_message_id) continue;
    const key = messageSearchResultKey(result);
    if (!key || key === "::" || seen.has(key)) continue;
    seen.add(key);
    results.push(result);
  }
  return results;
}

async function bulkUpdateMessageSearchResults(results, payloadForResult, { starting, empty, complete }) {
  const targets = results.filter((result) => result?.conversation_id && result.provider_message_id);
  if (!targets.length) {
    el.messageSearchStatus.textContent = empty;
    renderMessageSearchMoreControls();
    return;
  }

  state.messageSearchBulkBusy = true;
  renderMessageSearchMoreControls();
  el.messageSearchStatus.textContent = starting;
  let updated = 0;
  const failures = [];
  for (const result of targets) {
    try {
      const response = await api(`/penguin-connect/conversations/${encodeURIComponent(result.conversation_id)}/messages/management`, {
        method: "POST",
        body: JSON.stringify({
          provider_message_id: result.provider_message_id,
          ...payloadForResult(result),
        }),
      });
      mergeMessageManagement(response);
      removeMessageSearchResultIfFiltered(response);
      updated += 1;
      el.messageSearchStatus.textContent = `Updated ${updated}/${targets.length}`;
    } catch (error) {
      failures.push(error.message);
    }
  }

  state.messageSearchBulkBusy = false;
  if (failures.length) {
    el.messageSearchStatus.textContent = `Updated ${updated}; ${failures.length} failed`;
  } else {
    el.messageSearchStatus.textContent = complete(updated);
  }
  renderMessageSearchResults();
  renderConversations();
  renderThreadHeader();
  renderThreadControls();
  renderMessages();
  renderContactInspector();
  buildCodexPrompt();
}

async function starLoadedMessageSearchResults() {
  const targets = messageSearchManageableResults().filter((result) => !isStarredMessage(result));
  await bulkUpdateMessageSearchResults(targets, () => ({ starred: true }), {
    starting: `Starring ${targets.length} loaded result${targets.length === 1 ? "" : "s"}`,
    empty: "Loaded search results already starred",
    complete: (updated) => `Starred ${updated} loaded result${updated === 1 ? "" : "s"}`,
  });
}

async function markLoadedMessageSearchResultsRead() {
  const targets = messageSearchManageableResults().filter(isUnreadMessage);
  await bulkUpdateMessageSearchResults(targets, () => ({ unread: false }), {
    starting: `Marking ${targets.length} loaded result${targets.length === 1 ? "" : "s"} read`,
    empty: "Loaded search results already read",
    complete: (updated) => `Marked ${updated} loaded result${updated === 1 ? "" : "s"} read`,
  });
}

async function markLoadedMessageSearchResultsUnread() {
  const targets = messageSearchManageableResults().filter((result) => !isUnreadMessage(result));
  await bulkUpdateMessageSearchResults(targets, () => ({ unread: true }), {
    starting: `Marking ${targets.length} loaded result${targets.length === 1 ? "" : "s"} unread`,
    empty: "Loaded search results already unread",
    complete: (updated) => `Marked ${updated} loaded result${updated === 1 ? "" : "s"} unread`,
  });
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

function loadedMessageMatchesFilter(message, query = el.messageFilter.value.trim().toLowerCase()) {
  const haystack = [
    message.sender_name,
    message.sender_email,
    message.body_text,
    message.message_note,
    JSON.stringify(attachmentRows(message)),
  ].join(" ").toLowerCase();
  return messageMatchesView(message) && (!query || haystack.includes(query));
}

function visibleLoadedMessages() {
  const query = el.messageFilter.value.trim().toLowerCase();
  return [...state.messages].reverse().filter((message) => loadedMessageMatchesFilter(message, query));
}

function manageableLoadedMessages(rows = visibleLoadedMessages()) {
  const seen = new Set();
  const messages = [];
  for (const message of rows) {
    const providerMessageId = message?.provider_message_id || "";
    if (!providerMessageId || seen.has(providerMessageId)) continue;
    seen.add(providerMessageId);
    messages.push(message);
  }
  return messages;
}

async function bulkUpdateVisibleLoadedMessages(messages, payloadForMessage, { starting, empty, complete }) {
  if (!state.selected) return;
  const targets = manageableLoadedMessages(messages);
  if (!targets.length) {
    el.sendState.textContent = empty;
    renderMessageHistoryControls();
    return;
  }

  state.messageBulkBusy = true;
  renderMessageHistoryControls();
  el.sendState.textContent = starting;
  let updated = 0;
  const failures = [];
  for (const message of targets) {
    try {
      const result = await api(`/penguin-connect/conversations/${encodeURIComponent(state.selected.conversation_id)}/messages/management`, {
        method: "POST",
        body: JSON.stringify({
          provider_message_id: message.provider_message_id,
          ...payloadForMessage(message),
        }),
      });
      mergeMessageManagement(result);
      updated += 1;
      el.sendState.textContent = `Updated ${updated}/${targets.length}`;
    } catch (error) {
      failures.push(error.message);
    }
  }

  state.messageBulkBusy = false;
  el.sendState.textContent = failures.length
    ? `Updated ${updated}; ${failures.length} failed`
    : complete(updated);
  renderConversations();
  renderThreadHeader();
  renderThreadControls();
  renderMessages();
  renderMessageSearchResults();
  renderContactInspector();
  buildCodexPrompt();
}

async function starVisibleLoadedMessages() {
  const targets = manageableLoadedMessages().filter((message) => !isStarredMessage(message));
  await bulkUpdateVisibleLoadedMessages(targets, () => ({ starred: true }), {
    starting: `Starring ${targets.length} visible message${targets.length === 1 ? "" : "s"}`,
    empty: "Visible messages already starred",
    complete: (updated) => `Starred ${updated} visible message${updated === 1 ? "" : "s"}`,
  });
}

async function markVisibleLoadedMessagesRead() {
  const targets = manageableLoadedMessages().filter(isUnreadMessage);
  await bulkUpdateVisibleLoadedMessages(targets, () => ({ unread: false }), {
    starting: `Marking ${targets.length} visible message${targets.length === 1 ? "" : "s"} read`,
    empty: "Visible messages already read",
    complete: (updated) => `Marked ${updated} visible message${updated === 1 ? "" : "s"} read`,
  });
}

async function markVisibleLoadedMessagesUnread() {
  const targets = manageableLoadedMessages().filter((message) => !isUnreadMessage(message));
  await bulkUpdateVisibleLoadedMessages(targets, () => ({ unread: true }), {
    starting: `Marking ${targets.length} visible message${targets.length === 1 ? "" : "s"} unread`,
    empty: "Visible messages already unread",
    complete: (updated) => `Marked ${updated} visible message${updated === 1 ? "" : "s"} unread`,
  });
}

async function copyVisibleLoadedMessages() {
  const rows = visibleLoadedMessages();
  if (!state.selected || !rows.length) {
    el.sendState.textContent = "No visible messages to copy";
    renderMessageHistoryControls();
    return;
  }

  try {
    await copyText(rows.map(messageCopyText).filter(Boolean).join("\n\n"));
    el.sendState.textContent = `Copied ${rows.length} visible message${rows.length === 1 ? "" : "s"}`;
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

function replyContextQuoteText(context = state.replyContext) {
  if (!context) return "";
  const sender = context.sender || "sender";
  const time = context.time ? ` at ${context.time}` : "";
  const snippet = String(context.snippet || "").trim();
  return [`Re: ${sender}${time}`, snippet ? `> ${snippet}` : ""].filter(Boolean).join("\n");
}

function outgoingReplyText(message) {
  const body = String(message || "");
  if (!state.replyContext || !el.replyQuoteToggle.checked) return body;
  const quote = replyContextQuoteText();
  return body.trim() ? `${quote}\n\n${body}` : quote;
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
    button.setAttribute("aria-label", `Insert emoji ${emoji}`);
    button.addEventListener("click", () => {
      const start = textarea.selectionStart ?? textarea.value.length;
      const end = textarea.selectionEnd ?? textarea.value.length;
      textarea.value = `${textarea.value.slice(0, start)}${emoji}${textarea.value.slice(end)}`;
      textarea.focus();
      textarea.selectionStart = start + emoji.length;
      textarea.selectionEnd = start + emoji.length;
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
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
  const terms = highlightTerms(el.conversationSearch.value.trim());

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
    row.dataset.conversationId = conversation.conversation_id;
    row.innerHTML = `
      <button class="conversation-select" type="button" title="Select conversation" aria-label="Select conversation"></button>
      <button class="conversation-item" type="button">
        <span class="conversation-title-row">
          <span class="conversation-name"></span>
          <span class="conversation-badges"></span>
        </span>
        <span class="conversation-meta"></span>
        <span class="conversation-contact-context"></span>
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
    appendHighlightedText(mainButton.querySelector(".conversation-name"), conversationDisplayName(conversation), terms);
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
    const metaText = [
      conversation.chat_type || "chat",
      conversation.source_service_name || conversation.source_provider || "source",
      conversation.last_message_ts ? formatTime(conversation.last_message_ts) : conversation.status || "",
    ].filter(Boolean).join(" · ");
    appendHighlightedText(mainButton.querySelector(".conversation-meta"), metaText, terms);
    const contactContext = mainButton.querySelector(".conversation-contact-context");
    const contactContextText = conversationContactContextText(conversation);
    appendHighlightedText(contactContext, contactContextText, terms);
    contactContext.hidden = !contactContextText;
    const preview = mainButton.querySelector(".conversation-preview");
    const previewText = conversationPreviewText(conversation);
    appendHighlightedText(preview, previewText, terms);
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

function draftRecipientMatchKey(values = uniqueRecipientValues(draftRecipientValues())) {
  return uniqueRecipientValues(values).map(recipientCompareKey).filter(Boolean).join("|");
}

function draftThreadMatchRows() {
  const match = state.draftThreadMatch || {};
  if (match.matched_conversation) return [match.matched_conversation];
  return Array.isArray(match.matches) ? match.matches : [];
}

function draftThreadMatchLabel(match) {
  const participants = Array.isArray(match.participants) ? match.participants : [];
  return [
    match.display_name || "Messages thread",
    participants.length ? `${participants.length} recipient${participants.length === 1 ? "" : "s"}` : "",
  ].filter(Boolean).join(" · ");
}

function renderDraftThreadMatch() {
  el.draftThreadMatch.replaceChildren();
  const recipients = uniqueRecipientValues(draftRecipientValues());
  if (!recipients.length && !state.draftThreadResolving && !state.draftThreadMatch) {
    el.draftThreadMatch.hidden = true;
    return;
  }

  el.draftThreadMatch.hidden = false;
  el.draftThreadMatch.className = "draft-thread-match";
  const status = document.createElement("span");
  status.className = "draft-thread-match-status";
  const actions = document.createElement("span");
  actions.className = "draft-thread-match-actions";

  if (state.draftThreadResolving) {
    el.draftThreadMatch.classList.add("resolving");
    status.textContent = "Checking existing thread";
  } else if (state.draftThreadMatch?.match_state === "exact") {
    const match = state.draftThreadMatch.matched_conversation;
    el.draftThreadMatch.classList.add("exact");
    status.textContent = `Existing thread: ${draftThreadMatchLabel(match)}`;
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "Open thread";
    button.addEventListener("click", () => openDraftMatchedThread(match));
    actions.append(button);
  } else if (state.draftThreadMatch?.match_state === "multiple") {
    el.draftThreadMatch.classList.add("multiple");
    status.textContent = "Multiple matching threads";
    draftThreadMatchRows().slice(0, 3).forEach((match, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = index === 0 ? "Open first" : `Open ${index + 1}`;
      button.title = draftThreadMatchLabel(match);
      button.addEventListener("click", () => openDraftMatchedThread(match));
      actions.append(button);
    });
  } else if (state.draftThreadMatch?.match_state === "error") {
    el.draftThreadMatch.classList.add("error");
    status.textContent = state.draftThreadMatch.error || "Thread check failed";
  } else {
    el.draftThreadMatch.classList.add("none");
    status.textContent = "No exact local thread";
  }

  el.draftThreadMatch.append(status);
  if (actions.childElementCount) el.draftThreadMatch.append(actions);
}

function clearDraftThreadMatch() {
  clearTimeout(state.draftThreadResolveTimer);
  state.draftThreadResolveTimer = null;
  state.draftThreadResolveToken += 1;
  state.draftThreadResolveKey = "";
  state.draftThreadResolving = false;
  state.draftThreadMatch = null;
  renderDraftThreadMatch();
}

function scheduleDraftThreadResolve(values = uniqueRecipientValues(draftRecipientValues())) {
  const recipients = uniqueRecipientValues(values);
  clearTimeout(state.draftThreadResolveTimer);
  state.draftThreadResolveTimer = null;
  if (!recipients.length) {
    clearDraftThreadMatch();
    return;
  }

  const key = draftRecipientMatchKey(recipients);
  if (key && key === state.draftThreadResolveKey && state.draftThreadMatch && !state.draftThreadResolving) {
    renderDraftThreadMatch();
    return;
  }

  state.draftThreadResolveKey = key;
  state.draftThreadResolving = true;
  state.draftThreadMatch = null;
  const token = state.draftThreadResolveToken + 1;
  state.draftThreadResolveToken = token;
  renderDraftThreadMatch();
  state.draftThreadResolveTimer = setTimeout(() => resolveDraftThread(recipients, key, token), 220);
}

async function resolveDraftThread(recipients, key, token) {
  try {
    const result = await api("/penguin-connect/messages/resolve-draft", {
      method: "POST",
      body: JSON.stringify({ participants: recipients }),
    });
    if (token !== state.draftThreadResolveToken || key !== draftRecipientMatchKey()) return;
    state.draftThreadResolving = false;
    state.draftThreadMatch = result;
    renderDraftThreadMatch();
  } catch (error) {
    if (token !== state.draftThreadResolveToken || key !== draftRecipientMatchKey()) return;
    state.draftThreadResolving = false;
    state.draftThreadMatch = {
      match_state: "error",
      error: error.message,
      participants: recipients,
    };
    renderDraftThreadMatch();
  }
}

async function openDraftMatchedThread(match) {
  const conversationId = match?.conversation_id || "";
  if (!conversationId) {
    el.draftState.textContent = "No matched thread";
    return;
  }

  el.draftState.textContent = "Opening existing thread";
  let conversation = state.conversations.find((item) => item.conversation_id === conversationId);
  if (!conversation) {
    await loadConversations({ autoSelect: false });
    conversation = state.conversations.find((item) => item.conversation_id === conversationId);
  }
  if (!conversation) {
    el.draftState.textContent = "Matched thread not loaded";
    return;
  }
  await selectConversation(conversation);
  el.draftState.textContent = `Opened ${conversationDisplayName(conversation)}`;
}

function draftRecipientContactCandidates() {
  return [
    ...state.draftRecipientContactCache,
    ...state.draftRecipientSuggestions,
    ...state.contacts,
    ...Object.values(state.threadContactMatches || {}),
    ...(state.selected?.contact_context || []),
    ...state.conversations.flatMap((conversation) => conversationContactContextItems(conversation)),
  ].filter((contact) => contact && typeof contact === "object");
}

function draftRecipientSearchText() {
  const parts = String(el.draftRecipients.value || "").split(/[\n,;]+/);
  return String(parts[parts.length - 1] || "").trim();
}

function clearDraftRecipientSuggestions() {
  clearTimeout(state.draftRecipientSuggestTimer);
  state.draftRecipientSuggestTimer = null;
  state.draftRecipientSuggestToken += 1;
  state.draftRecipientSuggestionQuery = "";
  state.draftRecipientSuggestions = [];
  renderDraftRecipientSuggestions();
}

function cacheDraftRecipientContact(contact) {
  const handle = contactRecipientHandle(contact);
  const key = recipientCompareKey(handle || contactDisplayName(contact));
  if (!key) return;
  state.draftRecipientContactCache = [
    contact,
    ...state.draftRecipientContactCache.filter((item) => (
      recipientCompareKey(contactRecipientHandle(item) || contactDisplayName(item)) !== key
    )),
  ].slice(0, 20);
}

function renderDraftRecipientSuggestions() {
  el.draftRecipientSuggestions.replaceChildren();
  const query = state.draftRecipientSuggestionQuery;
  const suggestions = state.draftRecipientSuggestions;
  el.draftRecipientSuggestions.hidden = !query || query.length < 2;
  if (el.draftRecipientSuggestions.hidden) return;

  if (!suggestions.length) {
    const empty = document.createElement("div");
    empty.className = "draft-recipient-suggestion empty-state compact-state";
    empty.textContent = "No contact matches";
    el.draftRecipientSuggestions.append(empty);
    return;
  }

  const existingKeys = new Set(uniqueRecipientValues(draftRecipientValues()).map(recipientCompareKey));
  for (const contact of suggestions) {
    const handle = contactRecipientHandle(contact);
    const key = recipientCompareKey(handle);
    const button = document.createElement("button");
    button.className = "draft-recipient-suggestion";
    button.type = "button";
    button.disabled = !handle || existingKeys.has(key);
    button.innerHTML = `
      <span class="draft-recipient-suggestion-main">
        <span class="draft-recipient-suggestion-name"></span>
        <span class="draft-recipient-suggestion-handle"></span>
      </span>
      <span class="draft-recipient-suggestion-source"></span>
    `;
    button.querySelector(".draft-recipient-suggestion-name").textContent = contactDisplayName(contact);
    button.querySelector(".draft-recipient-suggestion-handle").textContent = handle || contactHandleText(contact);
    button.querySelector(".draft-recipient-suggestion-source").textContent = contact.is_saved === false ? "unsaved" : "contact";
    button.addEventListener("click", () => addDraftRecipientFromSuggestion(contact));
    el.draftRecipientSuggestions.append(button);
  }
}

async function loadDraftRecipientSuggestions(query) {
  const token = state.draftRecipientSuggestToken + 1;
  state.draftRecipientSuggestToken = token;
  state.draftRecipientSuggestionQuery = query;
  if (query.length < 2) {
    state.draftRecipientSuggestions = [];
    renderDraftRecipientSuggestions();
    return;
  }

  try {
    const params = new URLSearchParams({
      search: query,
      limit: "6",
      source: "all",
    });
    const payload = await api(`/penguin-connect/contacts?${params.toString()}`);
    if (token !== state.draftRecipientSuggestToken) return;
    state.draftRecipientSuggestions = (payload.contacts || []).filter(contactRecipientHandle).slice(0, 6);
    renderDraftRecipientSuggestions();
    refreshDraftRecipientChips();
  } catch (_error) {
    if (token !== state.draftRecipientSuggestToken) return;
    state.draftRecipientSuggestions = [];
    renderDraftRecipientSuggestions();
  }
}

function scheduleDraftRecipientSuggestions() {
  const query = draftRecipientSearchText();
  clearTimeout(state.draftRecipientSuggestTimer);
  state.draftRecipientSuggestTimer = setTimeout(() => loadDraftRecipientSuggestions(query), 180);
}

function draftRecipientContact(recipient) {
  return bestContactForHandle(recipient, draftRecipientContactCandidates());
}

function draftRecipientDisplay(recipient) {
  const contact = draftRecipientContact(recipient);
  const handle = String(recipient || "").trim();
  if (!contact) {
    return {
      known: false,
      label: handle,
      detail: handleType(handle) || "recipient",
      title: handle,
    };
  }

  const label = contactDisplayName(contact);
  const contactHandle = contactRecipientHandle(contact) || handle;
  const organization = String(contact.organization || "").trim();
  const note = contactNoteText(contact);
  return {
    known: contact.is_saved !== false,
    label,
    detail: [contactHandle !== label ? contactHandle : "", organization].filter(Boolean).join(" · "),
    title: [label, contactHandle, organization, note ? `note: ${note}` : ""].filter(Boolean).join(" · "),
  };
}

function draftUnknownRecipientHandles() {
  return uniqueRecipientValues(draftRecipientValues()).filter((recipient) => {
    if (!draftRecipientCanCreateContact(recipient)) return false;
    const contact = draftRecipientContact(recipient);
    return !contact || contact.is_saved === false;
  });
}

function draftRecipientCanCreateContact(recipient) {
  const type = handleType(recipient);
  return type === "phone" || type === "email";
}

function refreshDraftRecipientChips() {
  renderDraftRecipientChips(uniqueRecipientValues(draftRecipientValues()));
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

function draftAttachmentLabels() {
  return [
    ...state.draftAttachments.map((file) => file.name || "attachment"),
    ...state.draftMediaAttachments.map((item) => item.label || basename(item.path) || "media"),
  ];
}

function draftExistingAttachmentPaths() {
  return state.draftMediaAttachments.map((item) => item.path).filter(Boolean);
}

function renderDraftPreview(values = uniqueRecipientValues(draftRecipientValues()), draftText = "") {
  const recipients = uniqueRecipientValues(values);
  const body = draftBodyText();
  const draft = draftText || buildMessagesDraftText(recipients);
  const attachments = draftAttachmentLabels();
  const count = recipients.length;
  const mode = count > 1 ? "Group chat" : "Direct chat";
  el.draftPreviewTitle.textContent = count
    ? `${mode} · ${count} recipient${count === 1 ? "" : "s"}${attachments.length ? ` · ${attachments.length} file${attachments.length === 1 ? "" : "s"}` : ""}`
    : body && attachments.length
      ? "No recipients · message + files ready"
      : body
        ? "No recipients · message ready"
        : attachments.length
          ? "No recipients · files ready"
          : "No recipients";
  const attachmentSummary = attachments.length
    ? [
      `Attachments staged separately: ${attachments.join(", ")}`,
      state.draftAttachmentFolder ? `Folder: ${state.draftAttachmentFolder}` : "",
    ].filter(Boolean).join("\n")
    : "";
  const previewText = draft
    ? [draft.trimEnd(), attachmentSummary].filter(Boolean).join("\n")
    : [body ? `Message:\n\n${body}` : "", attachmentSummary].filter(Boolean).join("\n\n");
  el.draftPreviewText.textContent = previewText || "Add recipients to preview the Messages draft.";
  el.copyDraftRecipientsButton.disabled = !recipients.length;
  el.copyDraftBodyButton.disabled = !body;
  el.copyDraftPreviewButton.disabled = !draft;
  el.openAddressedDraftButton.disabled = !recipients.length;
  const unknownCount = draftUnknownRecipientHandles().length;
  el.draftCreateUnknownButton.disabled = unknownCount === 0;
  el.draftCreateUnknownButton.textContent = unknownCount
    ? `Create ${unknownCount} unknown`
    : "Create unknown";
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

async function createUnknownDraftRecipients() {
  const recipients = draftUnknownRecipientHandles();
  if (!recipients.length) {
    el.draftState.textContent = "No unknown phone/email recipients";
    renderDraftPreview();
    return;
  }

  el.draftCreateUnknownButton.disabled = true;
  el.draftState.textContent = `Creating ${recipients.length} contact${recipients.length === 1 ? "" : "s"}`;
  let created = 0;
  const failures = [];
  for (const recipient of recipients) {
    try {
      await createDraftRecipientContactRecord(recipient);
      created += 1;
      el.draftState.textContent = `Created ${created}/${recipients.length}`;
    } catch (error) {
      failures.push(error.message);
    }
  }

  try {
    if (created) {
      await api("/penguin-connect/contacts/refresh", { method: "POST", body: "{}" });
    }
    await loadContacts({ force: true });
    await loadThreadContactMatches();
    refreshDraftRecipientChips();
  } catch (error) {
    failures.push(error.message);
  } finally {
    const failedCount = failures.length;
    el.draftState.textContent = failedCount
      ? `Created ${created}; ${failedCount} failed`
      : `Created ${created} contact${created === 1 ? "" : "s"}`;
    renderDraftPreview();
  }
}

async function createDraftRecipientContactRecord(recipient) {
  const value = String(recipient || "").trim();
  if (!draftRecipientCanCreateContact(value)) {
    throw new Error("Recipient needs phone or email");
  }
  await api("/penguin-connect/contacts", {
    method: "POST",
    body: JSON.stringify(contactCreatePayloadFromHandle(value)),
  });
  cacheDraftRecipientContact(draftCreatedContactFromHandle(value));
}

async function refreshAfterDraftRecipientContactCreate({ created = false } = {}) {
  if (created) {
    await api("/penguin-connect/contacts/refresh", { method: "POST", body: "{}" });
  }
  await loadContacts({ force: true });
  await loadThreadContactMatches();
  refreshDraftRecipientChips();
  renderDraftPreview();
}

async function createDraftRecipientContact(recipient, button = null) {
  const value = String(recipient || "").trim();
  if (!draftRecipientCanCreateContact(value)) {
    el.draftState.textContent = "Use a phone or email to create contact";
    return false;
  }
  const contact = draftRecipientContact(value);
  if (contact && contact.is_saved !== false) {
    el.draftState.textContent = "Recipient already saved";
    refreshDraftRecipientChips();
    renderDraftPreview();
    return false;
  }

  if (button) button.disabled = true;
  el.draftState.textContent = "Creating contact";
  try {
    await createDraftRecipientContactRecord(value);
    await refreshAfterDraftRecipientContactCreate({ created: true });
    el.draftState.textContent = "Contact created";
    return true;
  } catch (error) {
    el.draftState.textContent = error.message;
    refreshDraftRecipientChips();
    renderDraftPreview();
    return false;
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
    const attachmentPaths = draftExistingAttachmentPaths();
    const result = await api("/penguin-connect/messages/draft", {
      method: "POST",
      body: JSON.stringify({
        participants,
        message: el.draftMessage.value,
        attachment_paths: attachmentPaths,
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
    const display = draftRecipientDisplay(recipient);
    const chip = document.createElement("span");
    chip.className = `draft-recipient-chip ${display.known ? "known-recipient" : "unknown-recipient"}`;
    chip.title = display.title;
    chip.innerHTML = `
      <span class="draft-recipient-chip-main">
        <span class="draft-recipient-chip-label"></span>
        <span class="draft-recipient-chip-detail"></span>
      </span>
      <button class="draft-recipient-contact-button" type="button" title="Create contact" aria-label="Create contact from recipient">+</button>
      <button type="button" title="Remove recipient" aria-label="Remove recipient">x</button>
    `;
    chip.querySelector(".draft-recipient-chip-label").textContent = display.label;
    const detail = chip.querySelector(".draft-recipient-chip-detail");
    detail.textContent = display.detail;
    detail.hidden = !display.detail;
    const contactButton = chip.querySelector(".draft-recipient-contact-button");
    const canCreate = draftRecipientCanCreateContact(recipient);
    contactButton.hidden = display.known || !canCreate;
    contactButton.disabled = !canCreate;
    contactButton.addEventListener("click", () => createDraftRecipientContact(recipient, contactButton));
    chip.querySelector('button[aria-label="Remove recipient"]').addEventListener("click", () => removeDraftRecipient(index));
    el.draftRecipientChips.append(chip);
  });
}

function setDraftRecipients(values, { focus = false } = {}) {
  const recipients = uniqueRecipientValues(values);
  el.draftRecipients.value = recipients.join(", ");
  renderDraftRecipientChips(recipients);
  renderDraftPreview(recipients);
  scheduleDraftThreadResolve(recipients);
  saveNewChatDraft();
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

function addDraftRecipientFromSuggestion(contact) {
  const handle = contactRecipientHandle(contact);
  if (!handle) {
    el.draftState.textContent = "No phone or email on contact";
    return;
  }
  cacheDraftRecipientContact(contact);
  const parts = String(el.draftRecipients.value || "").split(/[\n,;]+/);
  const existing = uniqueRecipientValues(parts.slice(0, -1));
  const existingKeys = new Set(existing.map(recipientCompareKey));
  const alreadyAdded = existingKeys.has(recipientCompareKey(handle));
  const recipients = alreadyAdded ? existing : uniqueRecipientValues([...existing, handle]);
  setDraftRecipients(recipients, { focus: true });
  clearDraftRecipientSuggestions();
  el.draftState.textContent = alreadyAdded ? "Recipient already added" : `${contactDisplayName(contact)} added`;
}

function visibleContactRecipientHandles() {
  return uniqueRecipientValues(visibleContacts().map(contactRecipientHandle));
}

function contactSelectionKey(contact) {
  const key = contact.contact_key || contact.favorite_contact_key || contact.note_contact_key || "";
  if (key) return key;
  const handle = contactRecipientHandle(contact);
  const compareKey = recipientCompareKey(handle);
  return compareKey ? `handle:${compareKey}` : "";
}

function contactDetailKey(contact) {
  return contactSelectionKey(contact)
    || recipientCompareKey(contactRecipientHandle(contact))
    || recipientCompareKey(contactDisplayName(contact));
}

function isActiveContact(contact) {
  const key = contactDetailKey(contact);
  return Boolean(key && key === state.activeContactKey);
}

function activeContact() {
  if (!state.activeContactKey) return null;
  return state.contacts.find(isActiveContact) || state.activeContact || null;
}

function resetActiveContactMessages() {
  state.activeContactMessageKey = "";
  state.activeContactMessages = [];
  state.activeContactMessagesLoading = false;
  state.activeContactMessagesBulkBusy = false;
  state.activeContactMessagesError = "";
  state.activeContactMessageNoteEditorId = "";
  state.activeContactMessagesLimit = 3;
}

function setActiveContact(contact, { rerenderList = true } = {}) {
  const key = contactDetailKey(contact);
  if (!key) return;
  if (state.activeContactKey !== key) {
    resetActiveContactMessages();
  }
  state.activeContactKey = key;
  state.activeContact = contact;
  renderContactInspector();
  if (rerenderList) renderContacts();
  buildCodexPrompt();
}

function clearActiveContact() {
  state.activeContactKey = "";
  state.activeContact = null;
  resetActiveContactMessages();
  renderContactInspector();
  renderContacts();
  buildCodexPrompt();
}

function visibleContactSelectionKeys() {
  return new Set(visibleContacts().map(contactSelectionKey).filter(Boolean));
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

function selectedContacts() {
  return state.contacts.filter(isContactSelected);
}

function contactBulkRecipientHandles() {
  const selected = selectedContactRecipientHandles();
  return selected.length ? selected : visibleContactRecipientHandles();
}

function contactBulkCreatableContacts() {
  const selected = selectedContacts();
  const contacts = selected.length ? selected : visibleContacts();
  return contacts.filter((contact) => contact.is_saved === false && contactRecipientHandle(contact));
}

function contactBulkManageableContacts() {
  const selected = selectedContacts();
  const contacts = selected.length ? selected : visibleContacts();
  const seen = new Set();
  const manageable = [];
  for (const contact of contacts) {
    const contactKey = contactFavoriteManagementKey(contact);
    if (!contactKey || seen.has(contactKey)) continue;
    seen.add(contactKey);
    manageable.push(contact);
  }
  return manageable;
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
  const creatableCount = contactBulkCreatableContacts().length;
  const manageableContacts = contactBulkManageableContacts();
  const favoriteCount = manageableContacts.filter(isFavoriteContact).length;
  const unfavoriteCount = manageableContacts.length - favoriteCount;
  el.contactSelectVisibleButton.disabled = visibleCount === 0;
  el.contactAddVisibleButton.disabled = !hasRecipients;
  el.contactCopyVisibleButton.disabled = !hasRecipients;
  el.contactSaveVisibleButton.disabled = !hasRecipients;
  el.contactFavoriteSelectedButton.disabled = unfavoriteCount === 0;
  el.contactUnfavoriteSelectedButton.disabled = favoriteCount === 0;
  el.contactCreateVisibleButton.disabled = !creatableCount;
  el.contactClearSelectedButton.disabled = selectedCount === 0;
  el.contactAddVisibleButton.textContent = selectedCount ? "Add selected" : "Add visible";
  el.contactCopyVisibleButton.textContent = selectedCount ? "Copy selected" : "Copy visible";
  el.contactSaveVisibleButton.textContent = selectedCount ? "Save selected" : "Save visible";
  el.contactFavoriteSelectedButton.textContent = unfavoriteCount ? `Star ${unfavoriteCount}` : "All starred";
  el.contactUnfavoriteSelectedButton.textContent = favoriteCount ? `Unstar ${favoriteCount}` : "None starred";
  el.contactCreateVisibleButton.textContent = selectedCount
    ? `Create ${creatableCount || "unknown"}`
    : "Create unknown";
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

function contactNamePartsForCreate(contact) {
  const handle = contactRecipientHandle(contact);
  const display = contactDisplayName(contact);
  const key = recipientCompareKey(display);
  const handleKey = recipientCompareKey(handle);
  if (!display || key === handleKey || handleType(display) === "phone" || handleType(display) === "email") {
    return { firstName: "", lastName: "" };
  }
  const parts = display.split(/\s+/).filter(Boolean);
  return {
    firstName: parts.shift() || display,
    lastName: parts.join(" "),
  };
}

function contactNamePartsFromDisplay(displayName, handle) {
  const value = String(handle || "").trim();
  return contactNamePartsForCreate({
    display_name: String(displayName || "").trim(),
    primary_handle: value,
    phone: handleType(value) === "email" ? "" : value,
    email: handleType(value) === "email" ? value : "",
  });
}

function contactCreatePayload(contact) {
  const handle = contactRecipientHandle(contact);
  const name = contactNamePartsForCreate(contact);
  return {
    first_name: name.firstName,
    last_name: name.lastName,
    organization: "",
    phones: handleType(handle) === "email" ? [] : [handle],
    emails: handleType(handle) === "email" ? [handle] : [],
    refresh_after: false,
  };
}

function contactCreatePayloadFromHandle(handle) {
  const value = String(handle || "").trim();
  return contactCreatePayload({
    display_name: value,
    primary_handle: value,
    phone: handleType(value) === "email" ? "" : value,
    email: handleType(value) === "email" ? value : "",
  });
}

function draftCreatedContactFromHandle(handle) {
  const value = String(handle || "").trim();
  return {
    display_name: value,
    primary_handle: value,
    phone: handleType(value) === "email" ? "" : value,
    email: handleType(value) === "email" ? value : "",
    source: "contacts",
    is_saved: true,
  };
}

function quickCreateStatus(target, message) {
  if (target === "thread") {
    el.threadPeopleState.textContent = message;
  } else if (target === "message-search") {
    el.messageSearchStatus.textContent = message;
    el.contactStatus.textContent = message;
  } else if (target === "message") {
    el.sendState.textContent = message;
    el.contactStatus.textContent = message;
  } else {
    el.contactStatus.textContent = message;
  }
  el.createContactState.textContent = message;
}

async function quickCreateContact(contact, target = "contact") {
  const handle = contactRecipientHandle(contact);
  if (!handle) {
    quickCreateStatus(target, "No phone or email on contact");
    return;
  }

  quickCreateStatus(target, "Creating contact");
  try {
    await api("/penguin-connect/contacts", {
      method: "POST",
      body: JSON.stringify(contactCreatePayload(contact)),
    });
    cacheDraftRecipientContact(draftCreatedContactFromHandle(handle));
    await api("/penguin-connect/contacts/refresh", { method: "POST", body: "{}" });
    await loadContacts({ force: true });
    await loadThreadContactMatches();
    refreshDraftRecipientChips();
    renderContactInspector();
    renderThreadPeople();
    quickCreateStatus(target, "Contact created");
    return true;
  } catch (error) {
    quickCreateStatus(target, error.message);
    return false;
  }
}

function showThreadParticipantContact(participant, contact) {
  const managedContact = participantManagedContact(participant, contact);
  const key = contactDetailKey(managedContact);
  if (!key) {
    el.threadPeopleState.textContent = "No contact detail";
    return;
  }
  setActiveContact(managedContact);
  el.threadPeopleState.textContent = "Contact detail opened";
  el.contactStatus.textContent = `Inspecting ${contactDisplayName(managedContact)}`;
}

async function createVisibleUnknownContacts() {
  const contacts = contactBulkCreatableContacts();
  if (!contacts.length) {
    el.contactStatus.textContent = "No unknown contacts visible";
    return;
  }

  el.contactCreateVisibleButton.disabled = true;
  el.contactStatus.textContent = `Creating ${contacts.length} contact${contacts.length === 1 ? "" : "s"}`;
  let created = 0;
  const failures = [];
  for (const contact of contacts) {
    try {
      await api("/penguin-connect/contacts", {
        method: "POST",
        body: JSON.stringify(contactCreatePayload(contact)),
      });
      created += 1;
      el.contactStatus.textContent = `Created ${created}/${contacts.length}`;
    } catch (error) {
      failures.push(error.message);
    }
  }

  try {
    if (created) {
      await api("/penguin-connect/contacts/refresh", { method: "POST", body: "{}" });
    }
    state.selectedContactKeys.clear();
    await loadContacts({ force: true });
    await loadThreadContactMatches();
  } catch (error) {
    failures.push(error.message);
  } finally {
    const failedCount = failures.length;
    el.contactStatus.textContent = failedCount
      ? `Created ${created}; ${failedCount} failed`
      : `Created ${created} contact${created === 1 ? "" : "s"}`;
    renderContactBulkActions();
  }
}

function currentThreadParticipantHandles() {
  return conversationParticipants().map((participant) => participant.handle);
}

function unknownThreadParticipants() {
  return conversationParticipants().filter((participant) => {
    const contact = threadContactMatch(participant.handle);
    return !contact || contact.is_saved === false;
  });
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

async function copyThreadParticipants() {
  const participants = currentThreadParticipantHandles();
  if (!participants.length) {
    el.threadPeopleState.textContent = "No participants";
    return;
  }

  try {
    await copyText(participants.join("\n"));
    el.threadPeopleState.textContent = `Copied ${participants.length} participant${participants.length === 1 ? "" : "s"}`;
  } catch (error) {
    el.threadPeopleState.textContent = error.message;
  }
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
  saveNewChatDraft();
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
    saveNewChatDraft();
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

async function createUnknownThreadParticipants() {
  const participants = unknownThreadParticipants();
  if (!participants.length) {
    el.threadPeopleState.textContent = state.selected ? "No unknown participants" : "No thread";
    return;
  }

  el.threadPeopleCreateAllButton.disabled = true;
  el.threadPeopleState.textContent = `Creating ${participants.length} contact${participants.length === 1 ? "" : "s"}`;
  let created = 0;
  const failures = [];
  for (const participant of participants) {
    try {
      await api("/penguin-connect/contacts", {
        method: "POST",
        body: JSON.stringify(contactCreatePayloadFromHandle(participant.handle)),
      });
      created += 1;
      el.threadPeopleState.textContent = `Created ${created}/${participants.length}`;
    } catch (error) {
      failures.push(error.message);
    }
  }

  try {
    if (created) {
      await api("/penguin-connect/contacts/refresh", { method: "POST", body: "{}" });
    }
    await loadContacts({ force: true });
    await loadThreadContactMatches();
  } catch (error) {
    failures.push(error.message);
  } finally {
    const failedCount = failures.length;
    renderThreadPeople();
    el.threadPeopleState.textContent = failedCount
      ? `Created ${created}; ${failedCount} failed`
      : `Created ${created} contact${created === 1 ? "" : "s"}`;
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
      saveNewChatDraft();
    }
    renderRecipientLists();
    el.draftState.textContent = "List deleted";
  } catch (error) {
    el.draftState.textContent = error.message;
  }
}

function fillContactNameFromDisplay(displayName, handle) {
  const name = contactNamePartsFromDisplay(displayName, handle);
  el.newContactFirst.value = name.firstName;
  el.newContactLast.value = name.lastName;
}

function fillContactFormFromHandle(value, stateText = "Prefilled from thread", displayName = "") {
  const handle = String(value || "").trim();
  if (!handle) return;
  clearContactForm();
  if (handleType(handle) === "email") {
    el.newContactEmails.value = handle;
  } else {
    el.newContactPhones.value = handle;
  }
  fillContactNameFromDisplay(displayName, handle);
  el.createContactState.textContent = stateText;
  el.newContactFirst.focus();
}

function fillContactFormFromContact(contact) {
  const handle = contactRecipientHandle(contact);
  if (!handle) {
    el.contactStatus.textContent = "No phone or email on result";
    return;
  }
  fillContactFormFromHandle(handle, "Prefilled from search", contactDisplayName(contact));
}

async function searchMessagesForContact(contact) {
  const query = contactRecipientHandle(contact) || contactDisplayName(contact);
  if (!query) {
    el.contactStatus.textContent = "No contact search value";
    return;
  }

  el.globalMessageSearch.value = query;
  el.messageDateFrom.value = "";
  el.messageDateTo.value = "";
  state.messageSearchView = "all";
  resetMessageSearchLimit();
  renderMessageSearchFilters();
  el.contactStatus.textContent = `Searching Messages for ${contactDisplayName(contact)}`;
  el.messageSearchStatus.textContent = `Searching Messages for ${contactDisplayName(contact)}`;
  el.globalMessageSearch.focus();
  await loadMessageSearch();
}

async function searchMessagesForParticipant(participant, contact) {
  const handle = String(participant?.handle || "").trim();
  if (!handle) {
    el.threadPeopleState.textContent = "No participant search value";
    return;
  }

  const managedContact = participantManagedContact(
    { ...participant, handle },
    contact || threadContactMatch(handle)
  );
  const label = contactDisplayName(managedContact);
  el.threadPeopleState.textContent = `Searching Messages for ${label}`;
  await searchMessagesForContact(managedContact);
  el.threadPeopleState.textContent = `Searching Messages for ${label}`;
}

async function searchMessagesForMessageContact(handle, displayName = "", { target = "message" } = {}) {
  const value = String(handle || "").trim();
  const statusEl = messageContactStatusTarget(target);
  if (!value) {
    statusEl.textContent = "No contact handle on message";
    return;
  }

  const contact = messageContactFromHandle(value, displayName);
  const label = contactDisplayName(contact);
  statusEl.textContent = `Searching Messages for ${label}`;
  await searchMessagesForContact(contact);
  statusEl.textContent = `Searching Messages for ${label}`;
}

async function searchMessagesForSearchResultContact(result) {
  await searchMessagesForMessageContact(
    messageSearchContactHandle(result),
    messageSearchContactDisplayName(result),
    { target: "message-search" }
  );
}

async function searchMessagesForContactRecentMessage(result) {
  await searchMessagesForMessageContact(
    messageSearchContactHandle(result),
    messageSearchContactDisplayName(result),
    { target: "contact" }
  );
}

async function searchMessagesForLoadedMessageContact(message) {
  await searchMessagesForMessageContact(
    messageContactHandle(message),
    messageContactDisplayName(message),
    { target: "message" }
  );
}

function filterConversationsForMessageContact(handle, displayName = "", { target = "message" } = {}) {
  const value = String(handle || "").trim();
  const statusEl = messageContactStatusTarget(target);
  if (!value) {
    statusEl.textContent = "No contact handle on message";
    return;
  }

  const contact = messageContactFromHandle(value, displayName);
  filterConversationsForContact(contact);
  statusEl.textContent = el.contactStatus.textContent;
}

function filterConversationsForSearchResultContact(result) {
  filterConversationsForMessageContact(
    messageSearchContactHandle(result),
    messageSearchContactDisplayName(result),
    { target: "message-search" }
  );
}

function filterConversationsForContactRecentMessage(result) {
  filterConversationsForMessageContact(
    messageSearchContactHandle(result),
    messageSearchContactDisplayName(result),
    { target: "contact" }
  );
}

function filterConversationsForLoadedMessageContact(message) {
  filterConversationsForMessageContact(
    messageContactHandle(message),
    messageContactDisplayName(message),
    { target: "message" }
  );
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

function messageContactHandle(message) {
  const participants = conversationParticipants().map((participant) => participant.handle);
  const isMine = isOwnMessage(message);
  const senderCandidates = isMine ? [] : [message.sender_email, message.sender_name];
  const threadCandidates = [state.selected?.source_chat_identifier, ...participants];
  const candidates = isMine ? threadCandidates : [...senderCandidates, ...threadCandidates];
  for (const candidate of candidates) {
    const handle = contactHandleCandidate(candidate);
    if (handle) return handle;
  }
  return "";
}

function messageSearchContactDisplayName(result) {
  const isMine = result.sender_name === "Me"
    || result.direction === "manual_to_imessage"
    || result.direction === "email_to_imessage"
    || Boolean(result.metadata?.is_from_me);
  return isMine ? "" : String(result.sender_name || "").trim();
}

function messageContactDisplayName(message) {
  return isOwnMessage(message) ? "" : String(message.sender_name || "").trim();
}

function messageContactFromHandle(handle, displayName = "") {
  const value = String(handle || "").trim();
  const type = handleType(value);
  const contactKey = contactManagementKeyForHandle(value);
  return {
    id: `message-contact:${contactKey || recipientCompareKey(value)}`,
    display_name: String(displayName || "").trim() || value,
    primary_handle: value,
    phone: type === "email" ? "" : value,
    phone_normalized: type === "phone" ? digitsOnly(value) : "",
    email: type === "email" ? value : "",
    handle_type: type || "handle",
    source: "messages",
    is_saved: false,
    contact_key: contactKey,
    contact_keys: contactKey ? [contactKey] : [],
    favorite_contact_key: "",
    contact_note: "",
    note_contact_key: "",
  };
}

async function lookupContactForMessageHandle(handle, { useCache = true } = {}) {
  const cachedContacts = [
    ...state.contacts,
    ...Object.values(state.threadContactMatches).filter(Boolean),
    ...(state.selected?.contact_context || []),
  ];
  const cached = useCache ? bestContactForHandle(handle, cachedContacts) : null;
  if (cached && cached.is_saved !== false) return cached;

  const payload = await api(`/penguin-connect/contacts?search=${encodeURIComponent(handle)}&limit=5&source=all`);
  return bestContactForHandle(handle, payload.contacts || []) || cached;
}

function messageContactStatusTarget(target) {
  if (target === "message-search") return el.messageSearchStatus;
  if (target === "message") return el.sendState;
  return el.contactStatus;
}

async function useMessageContactHandle(handle, displayName = "", { target = "message", fallbackState = "Prefilled from message" } = {}) {
  const value = String(handle || "").trim();
  const statusEl = messageContactStatusTarget(target);
  if (!value) {
    statusEl.textContent = "No contact handle on message";
    return;
  }

  const fallbackContact = messageContactFromHandle(value, displayName);
  statusEl.textContent = "Checking contact";
  el.contactStatus.textContent = "Checking contact";

  let contact = null;
  try {
    contact = await lookupContactForMessageHandle(value);
  } catch (error) {
    fillContactFormFromHandle(value, fallbackState, displayName);
    statusEl.textContent = `Contact lookup failed; form prefilled · ${error.message}`;
    el.contactStatus.textContent = "Contact form prefilled";
    return;
  }

  if (contact && contact.is_saved !== false) {
    setActiveContact(contact);
    statusEl.textContent = "Contact detail opened";
    el.contactStatus.textContent = "Contact detail opened";
    await loadContactInspectorMessages(contact);
    return;
  }

  const created = await quickCreateContact(contact || fallbackContact, target);
  if (!created) return;

  let savedContact = null;
  try {
    savedContact = await lookupContactForMessageHandle(value, { useCache: false });
  } catch (_error) {
    savedContact = null;
  }
  const createdFallbackContact = {
    ...fallbackContact,
    ...draftCreatedContactFromHandle(value),
    display_name: fallbackContact.display_name,
  };
  const active = savedContact && savedContact.is_saved !== false
    ? savedContact
    : createdFallbackContact;
  setActiveContact(active);
  statusEl.textContent = "Contact created";
  el.contactStatus.textContent = "Contact created";
  await loadContactInspectorMessages(active);
}

async function useMessageSearchResultContact(result) {
  const handle = messageSearchContactHandle(result);
  if (!handle) {
    el.messageSearchStatus.textContent = "No contact handle on result";
    return;
  }
  await useMessageContactHandle(handle, messageSearchContactDisplayName(result), {
    target: "message-search",
    fallbackState: "Prefilled from message search",
  });
}

async function useLoadedMessageContact(message) {
  const handle = messageContactHandle(message);
  if (!handle) {
    el.sendState.textContent = "No contact handle on message";
    return;
  }
  await useMessageContactHandle(handle, messageContactDisplayName(message), {
    target: "message",
    fallbackState: "Prefilled from message",
  });
}

function addMessageContactHandleToDraft(handle, { target = "message", missing = "No contact handle on message" } = {}) {
  const statusEl = messageContactStatusTarget(target);
  const value = String(handle || "").trim();
  if (!value) {
    statusEl.textContent = missing;
    return false;
  }

  const added = addDraftRecipient(value);
  const status = added ? "Added sender to new chat" : "Sender already in new chat";
  statusEl.textContent = status;
  el.draftState.textContent = status;
  el.draftMessage.focus();
  return added;
}

function addMessageSearchResultContactToDraft(result) {
  const handle = messageSearchContactHandle(result);
  return addMessageContactHandleToDraft(handle, {
    target: "message-search",
    missing: "No contact handle on result",
  });
}

function messageSearchContactHandles({ onlyNew = false } = {}) {
  const handles = uniqueRecipientValues(state.messageSearchResults.map(messageSearchContactHandle).filter(Boolean));
  if (!onlyNew) return handles;
  const existingKeys = new Set(uniqueRecipientValues(draftRecipientValues()).map(recipientCompareKey));
  return handles.filter((handle) => !existingKeys.has(recipientCompareKey(handle)));
}

function messageSearchParticipantHandlesForResult(result) {
  const candidates = [...participantValuesForConversation(result)];
  const sourceIdentifier = String(result?.source_chat_identifier || "").trim();
  if (sourceIdentifier && handleType(sourceIdentifier) !== "handle") {
    candidates.push(sourceIdentifier);
  }
  const senderHandle = messageSearchContactHandle(result);
  if (senderHandle) candidates.push(senderHandle);
  return uniqueRecipientValues(candidates.map(contactHandleCandidate).filter(Boolean));
}

function messageSearchParticipantHandles({ onlyNew = false } = {}) {
  const handles = uniqueRecipientValues(state.messageSearchResults.flatMap(messageSearchParticipantHandlesForResult));
  if (!onlyNew) return handles;
  const existingKeys = new Set(uniqueRecipientValues(draftRecipientValues()).map(recipientCompareKey));
  return handles.filter((handle) => !existingKeys.has(recipientCompareKey(handle)));
}

function messageSearchContactCandidates() {
  return [
    ...state.draftRecipientContactCache,
    ...state.contacts,
    ...Object.values(state.threadContactMatches || {}),
    ...(state.selected?.contact_context || []),
  ].filter((contact) => contact && typeof contact === "object");
}

function messageSearchCreatableContactItemsFromResults({ handlesForResult, displayNameForHandle, fallbackNameForHandle }) {
  const seen = new Set();
  const candidates = messageSearchContactCandidates();
  const items = [];
  for (const result of state.messageSearchResults) {
    for (const handle of handlesForResult(result)) {
      const key = recipientCompareKey(handle);
      if (!handle || !key || seen.has(key)) continue;
      seen.add(key);
      const saved = bestContactForHandle(handle, candidates);
      if (saved && saved.is_saved !== false) continue;
      items.push({
        handle,
        contact: messageContactFromHandle(
          handle,
          displayNameForHandle(result, handle) || fallbackNameForHandle(result, handle),
        ),
      });
    }
  }
  return items;
}

function messageSearchCreatableContactItems() {
  return messageSearchCreatableContactItemsFromResults({
    handlesForResult: (result) => [messageSearchContactHandle(result)].filter(Boolean),
    displayNameForHandle: (result) => messageSearchContactDisplayName(result),
    fallbackNameForHandle: (result) => searchResultConversationName(result),
  });
}

function messageSearchCreatableParticipantContactItems() {
  return messageSearchCreatableContactItemsFromResults({
    handlesForResult: messageSearchParticipantHandlesForResult,
    displayNameForHandle: (result, handle) => (
      recipientCompareKey(messageSearchContactHandle(result)) === recipientCompareKey(handle)
        ? messageSearchContactDisplayName(result)
        : ""
    ),
    fallbackNameForHandle: (_result, handle) => handle,
  });
}

function addMessageSearchContactsToDraft() {
  const allHandles = messageSearchContactHandles();
  const handles = messageSearchContactHandles({ onlyNew: true });
  if (!allHandles.length) {
    el.messageSearchStatus.textContent = "No sender handles in loaded results";
    return false;
  }
  if (!handles.length) {
    const status = "All search senders already in new chat";
    el.messageSearchStatus.textContent = status;
    el.draftState.textContent = status;
    renderMessageSearchMoreControls();
    el.draftMessage.focus();
    return false;
  }

  const recipients = uniqueRecipientValues([...draftRecipientValues(), ...handles]);
  setDraftRecipients(recipients, { focus: true });
  const status = `Added ${handles.length} search sender${handles.length === 1 ? "" : "s"} to new chat`;
  el.messageSearchStatus.textContent = status;
  el.draftState.textContent = status;
  renderMessageSearchMoreControls();
  el.draftMessage.focus();
  return true;
}

function addMessageSearchParticipantsToDraft() {
  const allHandles = messageSearchParticipantHandles();
  const handles = messageSearchParticipantHandles({ onlyNew: true });
  if (!allHandles.length) {
    el.messageSearchStatus.textContent = "No participant handles in loaded results";
    return false;
  }
  if (!handles.length) {
    const status = "All search participants already in new chat";
    el.messageSearchStatus.textContent = status;
    el.draftState.textContent = status;
    renderMessageSearchMoreControls();
    el.draftMessage.focus();
    return false;
  }

  const recipients = uniqueRecipientValues([...draftRecipientValues(), ...handles]);
  setDraftRecipients(recipients, { focus: true });
  const status = `Added ${handles.length} search participant${handles.length === 1 ? "" : "s"} to new chat`;
  el.messageSearchStatus.textContent = status;
  el.draftState.textContent = status;
  renderMessageSearchMoreControls();
  el.draftMessage.focus();
  return true;
}

async function createMessageSearchContactItems({
  allHandles,
  items,
  button,
  label,
  noHandlesStatus,
  alreadySavedStatus,
  noCreatedStatus,
}) {
  if (!allHandles.length) {
    el.messageSearchStatus.textContent = noHandlesStatus;
    return;
  }
  if (!items.length) {
    el.messageSearchStatus.textContent = alreadySavedStatus;
    renderMessageSearchMoreControls();
    return;
  }

  button.disabled = true;
  el.messageSearchStatus.textContent = `Creating ${items.length} ${label} contact${items.length === 1 ? "" : "s"}`;
  let created = 0;
  let skipped = 0;
  const failures = [];
  for (const item of items) {
    try {
      const existing = await lookupContactForMessageHandle(item.handle);
      if (existing && existing.is_saved !== false) {
        skipped += 1;
        continue;
      }
      await api("/penguin-connect/contacts", {
        method: "POST",
        body: JSON.stringify(contactCreatePayload(existing || item.contact)),
      });
      cacheDraftRecipientContact({
        ...draftCreatedContactFromHandle(item.handle),
        display_name: item.contact.display_name,
      });
      created += 1;
      el.messageSearchStatus.textContent = `Created ${created}/${items.length}`;
    } catch (error) {
      failures.push(error.message);
    }
  }

  try {
    if (created) {
      await api("/penguin-connect/contacts/refresh", { method: "POST", body: "{}" });
    }
    await loadContacts({ force: true });
    await loadThreadContactMatches();
    refreshDraftRecipientChips();
  } catch (error) {
    failures.push(error.message);
  } finally {
    renderMessageSearchMoreControls();
    if (failures.length) {
      el.messageSearchStatus.textContent = `Created ${created}; ${failures.length} failed`;
    } else if (created) {
      el.messageSearchStatus.textContent = `Created ${created} ${label} contact${created === 1 ? "" : "s"}`;
    } else {
      el.messageSearchStatus.textContent = skipped
        ? alreadySavedStatus
        : noCreatedStatus;
    }
  }
}

async function createMessageSearchContacts() {
  await createMessageSearchContactItems({
    allHandles: messageSearchContactHandles(),
    items: messageSearchCreatableContactItems(),
    button: el.createSearchSendersButton,
    label: "sender",
    noHandlesStatus: "No sender handles in loaded results",
    alreadySavedStatus: "Search sender contacts already saved",
    noCreatedStatus: "No sender contacts created",
  });
}

async function createMessageSearchParticipantContacts() {
  await createMessageSearchContactItems({
    allHandles: messageSearchParticipantHandles(),
    items: messageSearchCreatableParticipantContactItems(),
    button: el.createSearchParticipantsButton,
    label: "participant",
    noHandlesStatus: "No participant handles in loaded results",
    alreadySavedStatus: "Search participant contacts already saved",
    noCreatedStatus: "No participant contacts created",
  });
}

function messageSearchRecipientListName() {
  const query = trim(el.globalMessageSearch.value, 56);
  if (query) return `Search senders: ${query}`;
  const view = messageSearchViews.find((item) => item.key === state.messageSearchView) || messageSearchViews[0];
  return `${view.label} search senders`;
}

function messageSearchParticipantListName() {
  const query = trim(el.globalMessageSearch.value, 56);
  if (query) return `Search participants: ${query}`;
  const view = messageSearchViews.find((item) => item.key === state.messageSearchView) || messageSearchViews[0];
  return `${view.label} search participants`;
}

async function saveMessageSearchContactsAsRecipientList() {
  const participants = messageSearchContactHandles();
  if (!participants.length) {
    el.messageSearchStatus.textContent = "No sender handles in loaded results";
    return;
  }

  el.saveSearchSendersButton.disabled = true;
  el.messageSearchStatus.textContent = "Saving search senders";
  try {
    const result = await api("/penguin-connect/recipient-lists", {
      method: "POST",
      body: JSON.stringify({
        name: messageSearchRecipientListName(),
        participants,
      }),
    });
    const saved = result.recipient_list || {};
    state.activeRecipientListId = saved.list_id || "";
    el.recipientListName.value = recipientListLabel(saved);
    setDraftRecipients(participants, { focus: true });
    mergeRecipientList(saved);
    renderRecipientLists();
    const status = `${recipientListLabel(saved)} saved`;
    el.messageSearchStatus.textContent = status;
    el.draftState.textContent = status;
  } catch (error) {
    el.messageSearchStatus.textContent = error.message;
  } finally {
    renderMessageSearchMoreControls();
  }
}

async function saveMessageSearchParticipantsAsRecipientList() {
  const participants = messageSearchParticipantHandles();
  if (!participants.length) {
    el.messageSearchStatus.textContent = "No participant handles in loaded results";
    return;
  }

  el.saveSearchParticipantsButton.disabled = true;
  el.messageSearchStatus.textContent = "Saving search participants";
  try {
    const result = await api("/penguin-connect/recipient-lists", {
      method: "POST",
      body: JSON.stringify({
        name: messageSearchParticipantListName(),
        participants,
      }),
    });
    const saved = result.recipient_list || {};
    state.activeRecipientListId = saved.list_id || "";
    el.recipientListName.value = recipientListLabel(saved);
    setDraftRecipients(participants, { focus: true });
    mergeRecipientList(saved);
    renderRecipientLists();
    const status = `${recipientListLabel(saved)} saved`;
    el.messageSearchStatus.textContent = status;
    el.draftState.textContent = status;
  } catch (error) {
    el.messageSearchStatus.textContent = error.message;
  } finally {
    renderMessageSearchMoreControls();
  }
}

function addLoadedMessageContactToDraft(message) {
  return addMessageContactHandleToDraft(messageContactHandle(message), {
    target: "message",
    missing: "No contact handle on message",
  });
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

async function openContactInMessages(contact) {
  const handle = contactRecipientHandle(contact);
  if (!handle) {
    el.contactStatus.textContent = "No phone or email on contact";
    return;
  }

  el.contactStatus.textContent = "Opening Messages";
  try {
    const result = await api("/penguin-connect/messages/draft", {
      method: "POST",
      body: JSON.stringify({
        participants: [handle],
        message: "",
        attachments: [],
        copy_to_clipboard: false,
        open_messages: false,
        open_addressed: true,
        open_attachments: false,
      }),
    });
    el.contactStatus.textContent = result.opened_addressed ? "Messages opened" : "Address ready";
    el.draftState.textContent = result.opened_addressed ? "Contact chat opened" : "Contact address ready";
  } catch (error) {
    el.contactStatus.textContent = error.message;
  }
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

function contactConversationSearchQuery(contact) {
  return contactRecipientHandle(contact) || contactDisplayName(contact);
}

function filterConversationsForContact(contact) {
  const query = contactConversationSearchQuery(contact);
  if (!query) {
    el.contactStatus.textContent = "No contact thread search value";
    return;
  }

  state.conversationView = "all";
  state.conversationLabel = "";
  state.focusMessageId = "";
  el.conversationSearch.value = query;
  renderConversations();
  const count = visibleConversationRows().length;
  el.contactStatus.textContent = count
    ? `Showing ${count} thread${count === 1 ? "" : "s"} for ${contactDisplayName(contact)}`
    : `No matching threads for ${contactDisplayName(contact)}`;
  el.conversationSearch.focus();
}

function filterConversationsForParticipant(participant, contact) {
  const handle = String(participant?.handle || "").trim();
  if (!handle) {
    el.threadPeopleState.textContent = "No participant thread search value";
    return;
  }

  const managedContact = participantManagedContact(
    { ...participant, handle },
    contact || threadContactMatch(handle)
  );
  filterConversationsForContact(managedContact);
  el.threadPeopleState.textContent = el.contactStatus.textContent;
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

async function openContactConversation(contact, conversation, { messageId = "" } = {}) {
  state.focusMessageId = String(messageId || "").trim();
  state.conversationView = "all";
  state.conversationLabel = "";
  el.conversationSearch.value = "";
  renderConversations();
  el.contactStatus.textContent = `Opening ${conversationDisplayName(conversation)}`;
  await selectConversation(conversation);
  el.contactStatus.textContent = state.focusMessageId
    ? `Opened ${conversationDisplayName(conversation)} at matching message`
    : `Opened ${conversationDisplayName(conversation)}`;
  buildCodexPrompt();
}

function contactRelatedThreadMetaText(conversation) {
  const unread = Number(conversation.unread_count || 0);
  const labels = labelsForConversation(conversation).slice(0, 2).map((label) => `#${label}`);
  const note = String(conversation.note || "").replace(/\s+/g, " ").trim();
  return [
    conversation.chat_type || "chat",
    conversation.source_service_name || conversation.source_provider || conversation.provider || "",
    unread > 0 ? `${unread} unread` : "",
    ...labels,
    note ? `note: ${trim(note, 72)}` : "",
    formatTime(conversation.last_message_ts || conversation.latest_timestamp || conversation.updated_at || ""),
  ].filter(Boolean).join(" · ");
}

function contactMessageContextsForConversation(contact, conversation) {
  const conversationId = String(conversation?.conversation_id || "").trim();
  const contexts = Array.isArray(contact?.message_context) ? contact.message_context : [];
  return contexts.filter((context) => String(context?.conversation_id || "").trim() === conversationId);
}

function contactPrimaryMessageContext(contact, conversation) {
  return contactMessageContextsForConversation(contact, conversation)
    .find((context) => String(context?.provider_message_id || "").trim()) || null;
}

function contactThreadMessageContextText(contact, conversation) {
  return contactMessageContextsForConversation(contact, conversation).slice(0, 2).map((context) => {
    const sender = String(context.message_sender || "").replace(/\s+/g, " ").trim();
    const text = String(context.message_text || "").replace(/\s+/g, " ").trim();
    const when = formatTime(context.message_timestamp || "");
    return [
      "message",
      sender ? `from ${trim(sender, 32)}` : "",
      text ? trim(text, 96) : "",
      when,
    ].filter(Boolean).join(" · ");
  }).filter(Boolean).join(" / ");
}

function renderContactRelatedThreads(container, contact, terms = []) {
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
    const messageContext = contactPrimaryMessageContext(contact, conversation);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "contact-thread-link";
    const title = document.createElement("span");
    title.className = "contact-thread-title";
    appendHighlightedText(title, conversationDisplayName(conversation), terms);
    const meta = document.createElement("span");
    meta.className = "contact-thread-meta";
    const metaText = contactRelatedThreadMetaText(conversation);
    appendHighlightedText(meta, metaText, terms);
    const message = document.createElement("span");
    message.className = "contact-thread-message";
    const messageText = contactThreadMessageContextText(contact, conversation);
    if (messageText) appendHighlightedText(message, messageText, terms);
    button.append(title, meta);
    if (messageText) button.append(message);
    const detailText = [metaText, messageText].filter(Boolean).join(" · ");
    button.title = detailText
      ? `Open ${conversationDisplayName(conversation)} · ${detailText}`
      : `Open ${conversationDisplayName(conversation)}`;
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      openContactConversation(contact, conversation, {
        messageId: messageContext?.provider_message_id || "",
      });
    });
    container.append(button);
  }
}

function contactMessageSearchQuery(contact) {
  const normalizedPhone = String(contact?.phone_normalized || "").trim();
  if (normalizedPhone.length >= 7) return normalizedPhone;

  const primaryHandle = String(contact?.primary_handle || "").trim();
  const phone = String(contact?.phone || "").trim();
  const phoneDigits = digitsOnly(phone || primaryHandle);
  if (phoneDigits.length >= 7 && !primaryHandle.includes("@")) return phoneDigits;

  return contact?.email || primaryHandle || phone || contactDisplayName(contact);
}

async function copyContactRecentMessage(result) {
  try {
    await copyText(messageCopyText(result));
    el.contactStatus.textContent = "Recent message copied";
  } catch (error) {
    el.contactStatus.textContent = error.message;
  }
}

function editContactRecentMessageNote(result) {
  if (!result?.conversation_id || !result.provider_message_id) return;
  state.activeContactMessageNoteEditorId = messageSearchResultKey(result);
  renderContactInspector();
}

async function saveContactRecentMessageNote(result, noteValue) {
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
    state.activeContactMessageNoteEditorId = "";
    el.contactStatus.textContent = response.has_note ? "Recent message note saved" : "Recent message note cleared";
    renderContactInspector();
    renderMessageSearchResults();
    renderMessages();
    buildCodexPrompt();
  } catch (error) {
    el.contactStatus.textContent = error.message;
  }
}

async function toggleContactRecentMessageRead(result) {
  if (!result?.conversation_id || !result.provider_message_id) return;
  const nextUnread = !isUnreadMessage(result);
  try {
    const response = await api(`/penguin-connect/conversations/${encodeURIComponent(result.conversation_id)}/messages/management`, {
      method: "POST",
      body: JSON.stringify({
        provider_message_id: result.provider_message_id,
        unread: nextUnread,
      }),
    });
    mergeMessageManagement(response);
    el.contactStatus.textContent = nextUnread ? "Recent message marked unread" : "Recent message marked read";
    renderContactInspector();
    renderMessageSearchResults();
    renderConversations();
    renderThreadHeader();
    renderThreadControls();
    renderMessages();
    buildCodexPrompt();
  } catch (error) {
    el.contactStatus.textContent = error.message;
  }
}

async function toggleContactRecentMessageStar(result) {
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
    el.contactStatus.textContent = response.is_starred ? "Recent message starred" : "Recent message unstarred";
    renderContactInspector();
    renderMessageSearchResults();
    renderMessages();
    buildCodexPrompt();
  } catch (error) {
    el.contactStatus.textContent = error.message;
  }
}

function contactRecentManageableMessages() {
  const seen = new Set();
  const messages = [];
  for (const result of state.activeContactMessages) {
    if (!result?.conversation_id || !result.provider_message_id) continue;
    const key = messageSearchResultKey(result);
    if (!key || key === "::" || seen.has(key)) continue;
    seen.add(key);
    messages.push(result);
  }
  return messages;
}

async function bulkUpdateContactRecentMessages(results, payloadForResult, { starting, empty, complete }) {
  const targets = results.filter((result) => result?.conversation_id && result.provider_message_id);
  if (!targets.length) {
    el.contactStatus.textContent = empty;
    renderContactInspector();
    return;
  }

  state.activeContactMessagesBulkBusy = true;
  renderContactInspector();
  el.contactStatus.textContent = starting;
  let updated = 0;
  const failures = [];
  for (const result of targets) {
    try {
      const response = await api(`/penguin-connect/conversations/${encodeURIComponent(result.conversation_id)}/messages/management`, {
        method: "POST",
        body: JSON.stringify({
          provider_message_id: result.provider_message_id,
          ...payloadForResult(result),
        }),
      });
      mergeMessageManagement(response);
      removeMessageSearchResultIfFiltered(response);
      updated += 1;
      el.contactStatus.textContent = `Updated ${updated}/${targets.length}`;
    } catch (error) {
      failures.push(error.message);
    }
  }

  state.activeContactMessagesBulkBusy = false;
  if (failures.length) {
    el.contactStatus.textContent = `Updated ${updated}; ${failures.length} failed`;
  } else {
    el.contactStatus.textContent = complete(updated);
  }
  renderContactInspector();
  renderMessageSearchResults();
  renderConversations();
  renderThreadHeader();
  renderThreadControls();
  renderMessages();
  buildCodexPrompt();
}

async function starLoadedContactRecentMessages() {
  const targets = contactRecentManageableMessages().filter((result) => !isStarredMessage(result));
  await bulkUpdateContactRecentMessages(targets, () => ({ starred: true }), {
    starting: `Starring ${targets.length} recent message${targets.length === 1 ? "" : "s"}`,
    empty: "Recent messages already starred",
    complete: (updated) => `Starred ${updated} recent message${updated === 1 ? "" : "s"}`,
  });
}

async function markLoadedContactRecentMessagesRead() {
  const targets = contactRecentManageableMessages().filter(isUnreadMessage);
  await bulkUpdateContactRecentMessages(targets, () => ({ unread: false }), {
    starting: `Marking ${targets.length} recent message${targets.length === 1 ? "" : "s"} read`,
    empty: "Recent messages already read",
    complete: (updated) => `Marked ${updated} recent message${updated === 1 ? "" : "s"} read`,
  });
}

async function markLoadedContactRecentMessagesUnread() {
  const targets = contactRecentManageableMessages().filter((result) => !isUnreadMessage(result));
  await bulkUpdateContactRecentMessages(targets, () => ({ unread: true }), {
    starting: `Marking ${targets.length} recent message${targets.length === 1 ? "" : "s"} unread`,
    empty: "Recent messages already unread",
    complete: (updated) => `Marked ${updated} recent message${updated === 1 ? "" : "s"} unread`,
  });
}

async function copyLoadedContactRecentMessages() {
  const rows = state.activeContactMessages;
  if (!rows.length) {
    el.contactStatus.textContent = "No recent messages to copy";
    return;
  }

  try {
    await copyText(rows.map(messageCopyText).filter(Boolean).join("\n\n"));
    el.contactStatus.textContent = `Copied ${rows.length} recent message${rows.length === 1 ? "" : "s"}`;
  } catch (error) {
    el.contactStatus.textContent = error.message;
  }
}

function renderContactInspectorMessages(container, contact) {
  container.replaceChildren();
  const key = contactDetailKey(contact);
  const visible = state.activeContactMessageKey === key
    || state.activeContactMessagesLoading
    || Boolean(state.activeContactMessagesError);
  container.hidden = !visible;
  if (!visible) return;

  const header = document.createElement("div");
  header.className = "contact-inspector-messages-head";
  const headerLabel = document.createElement("span");
  headerLabel.textContent = state.activeContactMessagesLoading
    ? "Loading recent messages"
    : state.activeContactMessagesError
      ? state.activeContactMessagesError
      : `${state.activeContactMessages.length} recent message${state.activeContactMessages.length === 1 ? "" : "s"}`;
  header.append(headerLabel);
  if (!state.activeContactMessagesLoading && !state.activeContactMessagesError && state.activeContactMessages.length) {
    const manageableMessages = contactRecentManageableMessages();
    const bulkBusy = state.activeContactMessagesBulkBusy;
    const unstarredCount = manageableMessages.filter((result) => !isStarredMessage(result)).length;
    const unreadCount = manageableMessages.filter(isUnreadMessage).length;
    const readCount = manageableMessages.length - unreadCount;
    const actions = document.createElement("span");
    actions.className = "contact-inspector-message-bulk";
    actions.innerHTML = `
      <button type="button" data-action="copy-recent">Copy loaded</button>
      <button type="button" data-action="star-recent">Star loaded</button>
      <button type="button" data-action="read-recent">Mark read</button>
      <button type="button" data-action="unread-recent">Mark unread</button>
    `;
    const copyButton = actions.querySelector('[data-action="copy-recent"]');
    copyButton.disabled = bulkBusy || state.activeContactMessages.length === 0;
    copyButton.textContent = `Copy ${state.activeContactMessages.length}`;
    copyButton.addEventListener("click", copyLoadedContactRecentMessages);
    const starButton = actions.querySelector('[data-action="star-recent"]');
    starButton.disabled = bulkBusy || unstarredCount === 0;
    starButton.textContent = bulkBusy
      ? "Updating"
      : (unstarredCount ? `Star ${unstarredCount}` : "All starred");
    starButton.addEventListener("click", starLoadedContactRecentMessages);
    const readButton = actions.querySelector('[data-action="read-recent"]');
    readButton.disabled = bulkBusy || unreadCount === 0;
    readButton.textContent = bulkBusy
      ? "Updating"
      : (unreadCount ? `Mark ${unreadCount} read` : "All read");
    readButton.addEventListener("click", markLoadedContactRecentMessagesRead);
    const unreadButton = actions.querySelector('[data-action="unread-recent"]');
    unreadButton.disabled = bulkBusy || readCount === 0;
    unreadButton.textContent = bulkBusy
      ? "Updating"
      : (readCount ? `Mark ${readCount} unread` : "All unread");
    unreadButton.addEventListener("click", markLoadedContactRecentMessagesUnread);
    header.append(actions);
  }
  container.append(header);

  if (state.activeContactMessagesLoading) return;
  if (!state.activeContactMessages.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state compact-state";
    empty.textContent = state.activeContactMessagesError ? "Try again" : "No recent local messages";
    container.append(empty);
    return;
  }

  for (const result of state.activeContactMessages) {
    const item = document.createElement("div");
    const unread = isUnreadMessage(result);
    const starred = isStarredMessage(result);
    const noteText = messageNoteText(result);
    const resultKey = messageSearchResultKey(result);
    const editingNote = state.activeContactMessageNoteEditorId && resultKey === state.activeContactMessageNoteEditorId;
    item.className = ["contact-message-preview", unread ? "unread" : "", starred ? "starred" : "", noteText ? "noted" : ""].filter(Boolean).join(" ");
    item.innerHTML = `
      <button class="contact-message-preview-main" type="button">
        <span class="contact-message-preview-top"></span>
        <span class="contact-message-preview-body"></span>
      </button>
      <span class="contact-message-preview-actions">
        <button type="button" data-action="open">Open</button>
        <button type="button" data-action="reply">Reply</button>
        <button type="button" data-action="draft">Draft</button>
        <button type="button" data-action="star">Star</button>
        <button type="button" data-action="note">Note</button>
        <button type="button" data-action="read-state">Mark unread</button>
        <button type="button" data-action="copy">Copy</button>
        <button type="button" data-action="find-contact">Find</button>
        <button type="button" data-action="threads">Threads</button>
      </span>
      <div class="contact-message-preview-note" hidden><span></span></div>
      <div class="contact-message-preview-note-editor" hidden>
        <textarea rows="2" maxlength="2000" placeholder="Private message note"></textarea>
        <div class="contact-message-preview-note-actions">
          <button type="button" data-action="save-recent-note">Save</button>
          <button type="button" data-action="cancel-recent-note">Cancel</button>
          <button type="button" data-action="clear-recent-note">Clear</button>
        </div>
      </div>
    `;
    item.querySelector(".contact-message-preview-top").textContent = [
      searchResultConversationName(result),
      messageSender(result),
      messageTime(result),
    ].filter(Boolean).join(" · ");
    item.querySelector(".contact-message-preview-body").textContent = messageSnippet(result, 120);
    const attachmentChips = renderCompactAttachmentChips(result, {
      conversationId: result.conversation_id,
      limit: 3,
    });
    if (attachmentChips) item.append(attachmentChips);
    item.querySelector(".contact-message-preview-main").addEventListener("click", () => useMessageSearchResult(result));
    const openButton = item.querySelector('[data-action="open"]');
    openButton.disabled = !result.conversation_id;
    openButton.addEventListener("click", () => useMessageSearchResult(result));
    const replyButton = item.querySelector('[data-action="reply"]');
    replyButton.disabled = !result.conversation_id;
    replyButton.addEventListener("click", () => replyToMessageSearchResult(result));
    item.querySelector('[data-action="draft"]').addEventListener("click", () => useMessageAsNewChatDraft(result));
    const starButton = item.querySelector('[data-action="star"]');
    starButton.textContent = starred ? "Unstar" : "Star";
    starButton.classList.toggle("active", starred);
    starButton.disabled = !result.conversation_id || !result.provider_message_id;
    starButton.addEventListener("click", () => toggleContactRecentMessageStar(result));
    const noteButton = item.querySelector('[data-action="note"]');
    noteButton.textContent = noteText ? "Edit note" : "Note";
    noteButton.classList.toggle("active", Boolean(noteText) || Boolean(editingNote));
    noteButton.disabled = !result.conversation_id || !result.provider_message_id;
    noteButton.addEventListener("click", () => editContactRecentMessageNote(result));
    const readButton = item.querySelector('[data-action="read-state"]');
    readButton.textContent = unread ? "Mark read" : "Mark unread";
    readButton.classList.toggle("active", unread);
    readButton.disabled = !result.conversation_id || !result.provider_message_id;
    readButton.addEventListener("click", () => toggleContactRecentMessageRead(result));
    const contactHandle = messageSearchContactHandle(result);
    const findContactButton = item.querySelector('[data-action="find-contact"]');
    findContactButton.disabled = !contactHandle;
    findContactButton.addEventListener("click", () => searchMessagesForContactRecentMessage(result));
    const threadsButton = item.querySelector('[data-action="threads"]');
    threadsButton.disabled = !contactHandle;
    threadsButton.addEventListener("click", () => filterConversationsForContactRecentMessage(result));
    const noteBox = item.querySelector(".contact-message-preview-note");
    if (noteText) {
      noteBox.hidden = false;
      noteBox.querySelector("span").textContent = noteText;
    }
    const noteEditor = item.querySelector(".contact-message-preview-note-editor");
    const noteInput = noteEditor.querySelector("textarea");
    if (editingNote) {
      noteEditor.hidden = false;
      noteInput.value = noteText;
      noteInput.addEventListener("keydown", (event) => {
        if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
          event.preventDefault();
          saveContactRecentMessageNote(result, noteInput.value);
        }
        if (event.key === "Escape") {
          state.activeContactMessageNoteEditorId = "";
          renderContactInspector();
        }
      });
      noteEditor.querySelector('[data-action="save-recent-note"]').addEventListener("click", () => saveContactRecentMessageNote(result, noteInput.value));
      noteEditor.querySelector('[data-action="cancel-recent-note"]').addEventListener("click", () => {
        state.activeContactMessageNoteEditorId = "";
        renderContactInspector();
      });
      noteEditor.querySelector('[data-action="clear-recent-note"]').addEventListener("click", () => saveContactRecentMessageNote(result, ""));
    }
    item.querySelector('[data-action="copy"]').addEventListener("click", () => copyContactRecentMessage(result));
    container.append(item);
  }

  const loaded = state.activeContactMessages.length;
  const limit = state.activeContactMessagesLimit;
  const canShowMore = loaded >= limit && limit < state.activeContactMessagesLimitMax;
  const canCompact = limit > 3;
  if (canShowMore || canCompact) {
    const controls = document.createElement("div");
    controls.className = "contact-message-preview-more";
    controls.innerHTML = `
      <span></span>
      <button type="button" data-action="more">Show more</button>
      <button type="button" data-action="compact">Compact</button>
    `;
    controls.querySelector("span").textContent = `${loaded} loaded · window ${limit}`;
    const moreButton = controls.querySelector('[data-action="more"]');
    moreButton.disabled = !canShowMore;
    moreButton.textContent = limit >= state.activeContactMessagesLimitMax ? "Max shown" : "Show more";
    moreButton.addEventListener("click", () => loadContactInspectorMessages(contact, {
      limit: Math.min(limit + state.activeContactMessagesLimitStep, state.activeContactMessagesLimitMax),
    }));
    const compactButton = controls.querySelector('[data-action="compact"]');
    compactButton.hidden = !canCompact;
    compactButton.addEventListener("click", () => loadContactInspectorMessages(contact, { limit: 3 }));
    container.append(controls);
  }
}

async function loadContactInspectorMessages(contact, { limit = 3 } = {}) {
  const query = contactMessageSearchQuery(contact);
  const key = contactDetailKey(contact);
  if (!query) {
    el.contactStatus.textContent = "No contact search value";
    return;
  }
  const targetLimit = Math.min(
    Math.max(1, Number(limit) || 3),
    state.activeContactMessagesLimitMax
  );
  const token = state.activeContactMessagesToken + 1;
  state.activeContactMessagesToken = token;
  state.activeContactMessageKey = key;
  state.activeContactMessages = [];
  state.activeContactMessagesLoading = true;
  state.activeContactMessagesBulkBusy = false;
  state.activeContactMessagesError = "";
  state.activeContactMessageNoteEditorId = "";
  state.activeContactMessagesLimit = targetLimit;
  renderContactInspector();
  try {
    const params = new URLSearchParams({
      query,
      limit: String(targetLimit),
      view: "recent",
    });
    const payload = await api(`/penguin-connect/messages/search?${params.toString()}`);
    if (token !== state.activeContactMessagesToken || state.activeContactKey !== key) return;
    state.activeContactMessages = payload.messages || [];
    state.activeContactMessagesLoading = false;
    state.activeContactMessagesError = "";
    el.contactStatus.textContent = `${state.activeContactMessages.length} recent message${state.activeContactMessages.length === 1 ? "" : "s"}`;
    renderContactInspector();
    buildCodexPrompt();
  } catch (error) {
    if (token !== state.activeContactMessagesToken || state.activeContactKey !== key) return;
    state.activeContactMessages = [];
    state.activeContactMessagesLoading = false;
    state.activeContactMessagesError = error.message;
    el.contactStatus.textContent = error.message;
    renderContactInspector();
    buildCodexPrompt();
  }
}

function renderContactInspector() {
  const contact = activeContact();
  el.contactInspector.replaceChildren();
  el.contactInspector.hidden = !contact;
  if (!contact) return;
  state.activeContact = contact;

  const favorite = isFavoriteContact(contact);
  const noteText = contactNoteText(contact);
  const handle = contactRecipientHandle(contact);
  const terms = highlightTerms(el.contactSearch.value.trim());
  el.contactInspector.innerHTML = `
    <div class="contact-inspector-head">
      <div class="contact-inspector-main">
        <span class="contact-inspector-name"></span>
        <span class="contact-inspector-handle"></span>
        <span class="contact-inspector-meta"></span>
      </div>
      <button class="contact-inspector-close" type="button" title="Clear contact detail" aria-label="Clear contact detail">x</button>
    </div>
    <div class="contact-inspector-actions">
      <button type="button" data-action="add">Add</button>
      <button type="button" data-action="copy">Copy</button>
      <button type="button" data-action="find">Find</button>
      <button type="button" data-action="threads">Threads</button>
      <button type="button" data-action="recent">Recent</button>
      <button type="button" data-action="messages">Msg</button>
      <button type="button" data-action="favorite"></button>
      <button type="button" data-action="note">Note</button>
      <button type="button" data-action="create">Create</button>
    </div>
    <div class="contact-inspector-note" hidden></div>
    <div class="contact-inspector-related"></div>
    <div class="contact-inspector-messages" hidden></div>
  `;
  appendHighlightedText(el.contactInspector.querySelector(".contact-inspector-name"), contactDisplayName(contact), terms);
  appendHighlightedText(el.contactInspector.querySelector(".contact-inspector-handle"), contactHandleText(contact), terms);
  appendHighlightedText(el.contactInspector.querySelector(".contact-inspector-meta"), [
    contact.organization && contact.organization !== contactDisplayName(contact) ? contact.organization : "",
    contact.is_saved === false ? "unsaved participant" : contact.handle_type || "contact",
    favorite ? "favorite" : "",
  ].filter(Boolean).join(" · "), terms);

  const note = el.contactInspector.querySelector(".contact-inspector-note");
  note.hidden = !noteText;
  appendHighlightedText(note, noteText, terms);

  const addButton = el.contactInspector.querySelector('[data-action="add"]');
  addButton.disabled = !handle;
  addButton.addEventListener("click", () => addContactToDraft(contact));
  const copyButton = el.contactInspector.querySelector('[data-action="copy"]');
  copyButton.disabled = !handle;
  copyButton.addEventListener("click", () => copyContactHandle(contact));
  const findButton = el.contactInspector.querySelector('[data-action="find"]');
  findButton.disabled = !(handle || contactDisplayName(contact));
  findButton.addEventListener("click", () => searchMessagesForContact(contact));
  const threadsButton = el.contactInspector.querySelector('[data-action="threads"]');
  threadsButton.disabled = !contactConversationSearchQuery(contact);
  threadsButton.addEventListener("click", () => filterConversationsForContact(contact));
  const recentButton = el.contactInspector.querySelector('[data-action="recent"]');
  recentButton.disabled = !contactMessageSearchQuery(contact);
  recentButton.addEventListener("click", () => loadContactInspectorMessages(contact));
  const messagesButton = el.contactInspector.querySelector('[data-action="messages"]');
  messagesButton.disabled = !handle;
  messagesButton.addEventListener("click", () => openContactInMessages(contact));
  const favoriteButton = el.contactInspector.querySelector('[data-action="favorite"]');
  favoriteButton.textContent = favorite ? "Unstar" : "Star";
  favoriteButton.classList.toggle("active", favorite);
  favoriteButton.disabled = !contactFavoriteManagementKey(contact);
  favoriteButton.addEventListener("click", () => toggleContactFavorite(contact));
  const noteButton = el.contactInspector.querySelector('[data-action="note"]');
  noteButton.textContent = noteText ? "Edit note" : "Note";
  noteButton.disabled = !contactNoteManagementKey(contact);
  noteButton.addEventListener("click", () => editContactNote(contact));
  const createButton = el.contactInspector.querySelector('[data-action="create"]');
  createButton.hidden = contact.is_saved !== false;
  createButton.disabled = !handle;
  createButton.addEventListener("click", () => quickCreateContact(contact, "contact"));
  el.contactInspector.querySelector(".contact-inspector-close").addEventListener("click", clearActiveContact);
  renderContactRelatedThreads(el.contactInspector.querySelector(".contact-inspector-related"), contact);
  renderContactInspectorMessages(el.contactInspector.querySelector(".contact-inspector-messages"), contact);
}

function renderThreadPeople() {
  el.threadPeople.replaceChildren();
  const participants = conversationParticipants();
  const hasParticipants = Boolean(state.selected && participants.length);
  el.threadPeopleAddAllButton.disabled = !hasParticipants;
  el.threadPeopleCopyAllButton.disabled = !hasParticipants;
  el.threadPeopleSaveListButton.disabled = !hasParticipants;
  el.threadPeopleCreateAllButton.disabled = !hasParticipants;
  const matchedCount = participants.filter((participant) => {
    const contact = threadContactMatch(participant.handle);
    return contact && contact.is_saved !== false;
  }).length;
  const unknownCount = participants.length - matchedCount;
  el.threadPeopleCreateAllButton.disabled = !hasParticipants || unknownCount <= 0;
  el.threadPeopleCreateAllButton.textContent = unknownCount > 0 ? `Create ${unknownCount}` : "Create unknown";
  el.threadPeopleState.textContent = state.selected
    ? `${participants.length} participant${participants.length === 1 ? "" : "s"}${matchedCount ? ` · ${matchedCount} saved` : ""}${unknownCount ? ` · ${unknownCount} unknown` : ""}`
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
        <button type="button" data-action="messages">Messages</button>
        <button type="button" data-action="threads">Threads</button>
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
    item.querySelector('[data-action="messages"]').addEventListener("click", () => searchMessagesForParticipant(participant, contact));
    item.querySelector('[data-action="threads"]').addEventListener("click", () => filterConversationsForParticipant(participant, contact));
    item.querySelector('[data-action="copy"]').addEventListener("click", () => copyParticipantHandle(participant));
    item.querySelector('[data-action="draft"]').addEventListener("click", () => addParticipantToDraft(participant.handle));
    const contactButton = item.querySelector('[data-action="contact"]');
    contactButton.textContent = savedContact ? "Info" : "Create";
    contactButton.disabled = !savedContact && !contactRecipientHandle(managedContact);
    contactButton.addEventListener("click", () => {
      if (savedContact) {
        showThreadParticipantContact(participant, contact);
      } else {
        quickCreateContact(managedContact, "thread");
      }
    });
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

async function openMessageSearchResultInMessages(result) {
  const conversationId = result?.conversation_id || "";
  if (!conversationId) {
    el.messageSearchStatus.textContent = "No conversation on result";
    return;
  }

  el.messageSearchStatus.textContent = "Opening Messages";
  try {
    const payload = await api(`/penguin-connect/conversations/${encodeURIComponent(conversationId)}/open-messages`, {
      method: "POST",
      body: "{}",
    });
    const count = Number(payload.participants_count || 0);
    el.messageSearchStatus.textContent = payload.opened_addressed
      ? `Opened Messages to ${count} recipient${count === 1 ? "" : "s"}`
      : "Opened Messages";
  } catch (error) {
    el.messageSearchStatus.textContent = error.message;
  }
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
  if (state.activeContact && contactManagementKeyMatches(state.activeContact, contactKey)) {
    state.activeContact = {
      ...state.activeContact,
      is_favorite: updatedFavorite ? Boolean(result.is_favorite) : state.activeContact.is_favorite,
      favorite_contact_key: updatedFavorite
        ? (result.is_favorite ? contactKey : "")
        : state.activeContact.favorite_contact_key || "",
      contact_note: updatedNote ? result.contact_note || "" : state.activeContact.contact_note || "",
      note_contact_key: updatedNote
        ? (String(result.contact_note || "").trim() ? contactKey : "")
        : state.activeContact.note_contact_key || "",
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
    renderContactInspector();
    renderContacts();
    buildCodexPrompt();
  } catch (error) {
    el.contactStatus.textContent = error.message;
  }
}

async function setBulkContactFavorites(favorite) {
  const targets = contactBulkManageableContacts().filter((contact) => isFavoriteContact(contact) !== favorite);
  if (!targets.length) {
    el.contactStatus.textContent = favorite ? "Selected contacts already starred" : "Selected contacts already unstarred";
    renderContactBulkActions();
    return;
  }

  el.contactFavoriteSelectedButton.disabled = true;
  el.contactUnfavoriteSelectedButton.disabled = true;
  el.contactStatus.textContent = favorite
    ? `Starring ${targets.length} contact${targets.length === 1 ? "" : "s"}`
    : `Unstarring ${targets.length} contact${targets.length === 1 ? "" : "s"}`;
  let updated = 0;
  const failures = [];
  for (const contact of targets) {
    const contactKey = contactFavoriteManagementKey(contact);
    if (!contactKey) continue;
    try {
      const result = await api("/penguin-connect/contacts/management", {
        method: "POST",
        body: JSON.stringify({
          contact_key: contactKey,
          favorite,
        }),
      });
      mergeContactManagement(result, { updatedNote: false });
      updated += 1;
      el.contactStatus.textContent = `Updated ${updated}/${targets.length}`;
    } catch (error) {
      failures.push(error.message);
    }
  }

  el.contactStatus.textContent = failures.length
    ? `Updated ${updated}; ${failures.length} failed`
    : (favorite
      ? `Starred ${updated} contact${updated === 1 ? "" : "s"}`
      : `Unstarred ${updated} contact${updated === 1 ? "" : "s"}`);
  renderContactInspector();
  renderContacts();
  renderThreadPeople();
  buildCodexPrompt();
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
    renderContactInspector();
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
  const knownSort = Object.prototype.hasOwnProperty.call(contactSortLabels, state.contactSort);
  if (!knownSort) state.contactSort = "default";
  el.contactSort.value = state.contactSort;
  el.contactSort.title = `Sort contacts by ${contactSortLabels[state.contactSort]}`;
}

function renderContactMoreControls() {
  const loaded = state.contacts.length;
  const limit = state.contactLimit;
  const atMax = limit >= state.contactLimitMax;
  const canLoadMore = !state.contactsLoading && loaded >= limit && !atMax;
  el.contactMoreBar.hidden = !state.contactsLoading && loaded === 0 && state.contactSource === "all" && !el.contactSearch.value.trim();
  el.contactCount.textContent = state.contactsLoading
    ? `Loading up to ${limit} contacts`
    : `${loaded} loaded · window ${limit}${atMax ? " max" : ""}`;
  el.loadMoreContactsButton.disabled = !canLoadMore;
  el.loadMoreContactsButton.textContent = state.contactsLoading
    ? "Loading"
    : (atMax ? "Max shown" : "Show more");
}

function renderContacts() {
  el.contactList.replaceChildren();
  const contacts = visibleContacts();
  const terms = highlightTerms(el.contactSearch.value.trim());
  renderContactBulkActions();
  renderContactMoreControls();
  renderContactInspector();
  if (!contacts.length) {
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

  for (const contact of contacts) {
    const item = document.createElement("div");
    const favorite = isFavoriteContact(contact);
    const noteText = contactNoteText(contact);
    const editingNote = state.contactNoteEditorKey === contactNoteManagementKey(contact);
    const selected = isContactSelected(contact);
    const active = isActiveContact(contact);
    item.className = `contact-item ${favorite ? "favorite-contact" : ""} ${noteText ? "noted-contact" : ""} ${selected ? "selected-contact" : ""} ${active ? "active-contact" : ""}`;
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
        <button class="contact-search-messages" type="button" title="Search local Messages" aria-label="Search local Messages for contact">Find</button>
        <button class="contact-thread-filter" type="button" title="Filter conversations" aria-label="Filter conversations for contact">Threads</button>
        <button class="contact-message" type="button" title="Open in Messages" aria-label="Open contact in Messages">Msg</button>
        <button class="contact-details" type="button" title="Show contact detail" aria-label="Show contact detail">Info</button>
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
    appendHighlightedText(item.querySelector(".contact-name"), contactDisplayName(contact), terms);
    appendHighlightedText(item.querySelector(".contact-handle"), contactHandleText(contact), terms);
    const contactMeta = contact.organization && contact.organization !== contactDisplayName(contact)
      ? contact.organization
      : contact.handle_type || "contact";
    appendHighlightedText(item.querySelector(".contact-meta"), contactMeta, terms);
    const selectButton = item.querySelector(".contact-select-toggle");
    selectButton.textContent = selected ? "x" : "";
    selectButton.classList.toggle("active", selected);
    selectButton.disabled = !contactSelectionKey(contact) || !contactRecipientHandle(contact);
    selectButton.setAttribute("aria-pressed", selected ? "true" : "false");
    selectButton.addEventListener("click", () => toggleContactSelection(contact));
    item.querySelector(".contact-main").addEventListener("click", () => {
      setActiveContact(contact, { rerenderList: false });
      useContact(contact);
    });
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
    const searchMessagesButton = item.querySelector(".contact-search-messages");
    searchMessagesButton.disabled = !(contactRecipientHandle(contact) || contactDisplayName(contact));
    searchMessagesButton.addEventListener("click", () => searchMessagesForContact(contact));
    const threadFilterButton = item.querySelector(".contact-thread-filter");
    threadFilterButton.disabled = !contactConversationSearchQuery(contact);
    threadFilterButton.addEventListener("click", () => filterConversationsForContact(contact));
    const messageButton = item.querySelector(".contact-message");
    messageButton.disabled = !contactRecipientHandle(contact);
    messageButton.addEventListener("click", () => openContactInMessages(contact));
    const detailsButton = item.querySelector(".contact-details");
    detailsButton.classList.toggle("active", active);
    detailsButton.addEventListener("click", () => setActiveContact(contact));
    const createButton = item.querySelector(".contact-create-result");
    createButton.hidden = contact.is_saved !== false;
    createButton.disabled = !contactRecipientHandle(contact);
    createButton.addEventListener("click", () => quickCreateContact(contact, "contact"));
    const noteBox = item.querySelector(".contact-note");
    if (noteText) {
      noteBox.hidden = false;
      appendHighlightedText(noteBox.querySelector("span"), noteText, terms);
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
    renderContactRelatedThreads(item.querySelector(".contact-related"), contact, terms);
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

function searchHasRunnableInput() {
  const query = el.globalMessageSearch.value.trim();
  const hasDateFilter = Boolean(el.messageDateFrom.value.trim() || el.messageDateTo.value.trim());
  const scoped = state.messageSearchView !== "all";
  return query.length >= 2 || scoped || hasDateFilter;
}

function renderMessageSearchMoreControls() {
  const runnable = searchHasRunnableInput();
  const loaded = state.messageSearchResults.length;
  const limit = state.messageSearchLimit;
  const atMax = limit >= state.messageSearchLimitMax;
  const canLoadMore = runnable && !state.messageSearchLoading && loaded >= limit && !atMax;
  const bulkBusy = state.messageSearchLoading || state.messageSearchBulkBusy;
  const manageableResults = messageSearchManageableResults();
  const unstarredLoadedCount = manageableResults.filter((result) => !isStarredMessage(result)).length;
  const unreadLoadedCount = manageableResults.filter(isUnreadMessage).length;
  const readLoadedCount = manageableResults.length - unreadLoadedCount;
  const allSenderCount = messageSearchContactHandles().length;
  const newSenderCount = messageSearchContactHandles({ onlyNew: true }).length;
  const allParticipantCount = messageSearchParticipantHandles().length;
  const newParticipantCount = messageSearchParticipantHandles({ onlyNew: true }).length;
  const creatableSenderCount = messageSearchCreatableContactItems().length;
  const creatableParticipantCount = messageSearchCreatableParticipantContactItems().length;
  el.messageSearchMoreBar.hidden = !runnable;
  el.messageSearchCount.textContent = state.messageSearchLoading
    ? `Loading up to ${limit} results`
    : `${loaded} loaded · window ${limit}${atMax ? " max" : ""}`;
  el.starSearchLoadedButton.disabled = bulkBusy || unstarredLoadedCount === 0;
  el.starSearchLoadedButton.textContent = state.messageSearchBulkBusy
    ? "Updating"
    : (unstarredLoadedCount
      ? `Star ${unstarredLoadedCount}`
      : (manageableResults.length ? "All starred" : "Star loaded"));
  el.markSearchReadButton.disabled = bulkBusy || unreadLoadedCount === 0;
  el.markSearchReadButton.textContent = state.messageSearchBulkBusy
    ? "Updating"
    : (unreadLoadedCount
      ? `Mark ${unreadLoadedCount} read`
      : (manageableResults.length ? "All read" : "Mark read"));
  el.markSearchUnreadButton.disabled = bulkBusy || readLoadedCount === 0;
  el.markSearchUnreadButton.textContent = state.messageSearchBulkBusy
    ? "Updating"
    : (readLoadedCount
      ? `Mark ${readLoadedCount} unread`
      : (manageableResults.length ? "All unread" : "Mark unread"));
  el.addSearchSendersButton.disabled = bulkBusy || allSenderCount === 0 || newSenderCount === 0;
  el.addSearchSendersButton.textContent = state.messageSearchLoading
    ? "Add senders"
    : (newSenderCount
      ? `Add ${newSenderCount} sender${newSenderCount === 1 ? "" : "s"}`
      : (allSenderCount ? "All senders added" : "Add senders"));
  el.addSearchParticipantsButton.disabled = bulkBusy || allParticipantCount === 0 || newParticipantCount === 0;
  el.addSearchParticipantsButton.textContent = state.messageSearchLoading
    ? "Add participants"
    : (newParticipantCount
      ? `Add ${newParticipantCount} participant${newParticipantCount === 1 ? "" : "s"}`
      : (allParticipantCount ? "All participants added" : "Add participants"));
  el.saveSearchSendersButton.disabled = bulkBusy || allSenderCount === 0;
  el.saveSearchSendersButton.textContent = state.messageSearchLoading
    ? "Save senders"
    : (allSenderCount
      ? `Save ${allSenderCount} sender${allSenderCount === 1 ? "" : "s"}`
      : "Save senders");
  el.saveSearchParticipantsButton.disabled = bulkBusy || allParticipantCount === 0;
  el.saveSearchParticipantsButton.textContent = state.messageSearchLoading
    ? "Save participants"
    : (allParticipantCount
      ? `Save ${allParticipantCount} participant${allParticipantCount === 1 ? "" : "s"}`
      : "Save participants");
  el.createSearchSendersButton.disabled = bulkBusy || creatableSenderCount === 0;
  el.createSearchSendersButton.textContent = state.messageSearchLoading
    ? "Create contacts"
    : (creatableSenderCount
      ? `Create ${creatableSenderCount} contact${creatableSenderCount === 1 ? "" : "s"}`
      : (allSenderCount ? "Contacts saved" : "Create contacts"));
  el.createSearchParticipantsButton.disabled = bulkBusy || creatableParticipantCount === 0;
  el.createSearchParticipantsButton.textContent = state.messageSearchLoading
    ? "Create participants"
    : (creatableParticipantCount
      ? `Create ${creatableParticipantCount} participant${creatableParticipantCount === 1 ? "" : "s"}`
      : (allParticipantCount ? "Participant contacts saved" : "Create participants"));
  el.loadMoreSearchButton.disabled = state.messageSearchBulkBusy || !canLoadMore;
  el.loadMoreSearchButton.textContent = state.messageSearchLoading
    ? "Loading"
    : (atMax ? "Max shown" : "Show more");
}

function renderMessageSearchResults() {
  el.messageSearchResults.replaceChildren();
  renderMessageSearchMoreControls();
  const query = el.globalMessageSearch.value.trim();
  const terms = highlightTerms(query);
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
    const unread = isUnreadMessage(result);
    const noteText = messageNoteText(result);
    const resultKey = messageSearchResultKey(result);
    const editingNote = state.messageSearchNoteEditorId && resultKey === state.messageSearchNoteEditorId;
    item.className = ["search-result", unread ? "unread" : "", starred ? "starred" : "", noted ? "noted" : ""].filter(Boolean).join(" ");
    item.innerHTML = `
      <button class="search-result-main" type="button">
        <span class="search-result-top"></span>
        <span class="search-result-body"></span>
      </button>
      <span class="search-result-actions">
        <button type="button" data-action="star">Star</button>
        <button type="button" data-action="read-state">Mark unread</button>
        <button type="button" data-action="note">Note</button>
        <button type="button" data-action="reply">Reply</button>
        <button type="button" data-action="draft">New draft</button>
        <button type="button" data-action="copy">Copy</button>
        <button type="button" data-action="add-contact">Add</button>
        <button type="button" data-action="contact">Contact</button>
        <button type="button" data-action="find-contact">Find</button>
        <button type="button" data-action="threads">Threads</button>
        <button type="button" data-action="open">Open</button>
        <button type="button" data-action="messages">Messages</button>
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
    appendHighlightedText(item.querySelector(".search-result-top"), [
      searchResultConversationName(result),
      sender,
      formatTime(result.message_timestamp || result.timestamp),
    ].filter(Boolean).join(" · "), terms);
    appendHighlightedText(item.querySelector(".search-result-body"), messageSnippet(result), terms);
    item.querySelector(".search-result-main").addEventListener("click", () => useMessageSearchResult(result));
    const attachmentChips = renderCompactAttachmentChips(result, {
      conversationId: result.conversation_id,
      terms,
    });
    if (attachmentChips) item.insertBefore(attachmentChips, item.querySelector(".search-result-note"));
    const starButton = item.querySelector('[data-action="star"]');
    starButton.textContent = starred ? "Unstar" : "Star";
    starButton.classList.toggle("active", starred);
    starButton.disabled = !result.conversation_id || !result.provider_message_id;
    starButton.addEventListener("click", () => toggleMessageSearchResultStar(result));
    const readButton = item.querySelector('[data-action="read-state"]');
    readButton.textContent = unread ? "Mark read" : "Mark unread";
    readButton.classList.toggle("active", unread);
    readButton.disabled = !result.conversation_id || !result.provider_message_id;
    readButton.addEventListener("click", () => toggleMessageSearchResultRead(result));
    const noteButton = item.querySelector('[data-action="note"]');
    noteButton.textContent = noted ? "Edit note" : "Note";
    noteButton.classList.toggle("active", noted || Boolean(editingNote));
    noteButton.disabled = !result.conversation_id || !result.provider_message_id;
    noteButton.addEventListener("click", () => editMessageSearchResultNote(result));
    const noteBox = item.querySelector(".search-result-note");
    if (noteText) {
      noteBox.hidden = false;
      appendHighlightedText(noteBox.querySelector("span"), noteText, terms);
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
    item.querySelector('[data-action="draft"]').addEventListener("click", () => useMessageAsNewChatDraft(result));
    item.querySelector('[data-action="copy"]').addEventListener("click", async () => {
      await copyText(messageCopyText(result));
      el.sendState.textContent = "Search result copied";
    });
    const addContactButton = item.querySelector('[data-action="add-contact"]');
    addContactButton.disabled = !contactHandle;
    addContactButton.addEventListener("click", () => addMessageSearchResultContactToDraft(result));
    const contactButton = item.querySelector('[data-action="contact"]');
    contactButton.disabled = !contactHandle;
    contactButton.addEventListener("click", () => useMessageSearchResultContact(result));
    const findContactButton = item.querySelector('[data-action="find-contact"]');
    findContactButton.disabled = !contactHandle;
    findContactButton.addEventListener("click", () => searchMessagesForSearchResultContact(result));
    const threadsButton = item.querySelector('[data-action="threads"]');
    threadsButton.disabled = !contactHandle;
    threadsButton.addEventListener("click", () => filterConversationsForSearchResultContact(result));
    item.querySelector('[data-action="open"]').addEventListener("click", () => useMessageSearchResult(result));
    const messagesButton = item.querySelector('[data-action="messages"]');
    messagesButton.disabled = !result.conversation_id;
    messagesButton.addEventListener("click", () => openMessageSearchResultInMessages(result));
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

function renderMessageHistoryControls() {
  const hasSelection = Boolean(state.selected);
  const loaded = state.messages.length;
  const visible = hasSelection ? visibleLoadedMessages() : [];
  const manageable = manageableLoadedMessages(visible);
  const unstarredVisibleCount = manageable.filter((message) => !isStarredMessage(message)).length;
  const unreadVisibleCount = manageable.filter(isUnreadMessage).length;
  const readVisibleCount = manageable.length - unreadVisibleCount;
  const filtered = Boolean(el.messageFilter.value.trim() || state.messageView !== "all");
  const limit = state.messageLimit;
  const atMax = limit >= state.messageLimitMax;
  const canLoadMore = hasSelection && !state.messagesLoading && loaded >= limit && !atMax;
  const bulkBusy = state.messageBulkBusy || state.messagesLoading;
  el.loadedMessageCount.textContent = hasSelection
    ? (state.messagesLoading
      ? `Loading up to ${limit} messages`
      : `${filtered ? `${visible.length}/${loaded} visible` : `${loaded} loaded`} · window ${limit}${atMax ? " max" : ""}`)
    : "No thread loaded";
  el.copyVisibleMessagesButton.disabled = bulkBusy || visible.length === 0;
  el.copyVisibleMessagesButton.textContent = visible.length
    ? `Copy ${visible.length}`
    : "Copy visible";
  el.starVisibleMessagesButton.disabled = bulkBusy || unstarredVisibleCount === 0;
  el.starVisibleMessagesButton.textContent = state.messageBulkBusy
    ? "Updating"
    : (unstarredVisibleCount
      ? `Star ${unstarredVisibleCount}`
      : (manageable.length ? "All starred" : "Star visible"));
  el.markVisibleMessagesReadButton.disabled = bulkBusy || unreadVisibleCount === 0;
  el.markVisibleMessagesReadButton.textContent = state.messageBulkBusy
    ? "Updating"
    : (unreadVisibleCount
      ? `Mark ${unreadVisibleCount} read`
      : (manageable.length ? "All read" : "Mark read"));
  el.markVisibleMessagesUnreadButton.disabled = bulkBusy || readVisibleCount === 0;
  el.markVisibleMessagesUnreadButton.textContent = state.messageBulkBusy
    ? "Updating"
    : (readVisibleCount
      ? `Mark ${readVisibleCount} unread`
      : (manageable.length ? "All unread" : "Mark unread"));
  el.loadMoreMessagesButton.disabled = !canLoadMore;
  el.loadMoreMessagesButton.textContent = state.messagesLoading
    ? "Loading"
    : (atMax ? "Max loaded" : "Load older");
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

async function copyMediaLink(item) {
  if (!item?.url) {
    el.threadMediaState.textContent = "No media link";
    return;
  }
  try {
    await copyText(new URL(item.url, window.location.origin).toString());
    el.threadMediaState.textContent = "Media link copied";
  } catch (error) {
    el.threadMediaState.textContent = error.message;
  }
}

function mediaAttachmentPath(item) {
  return attachmentLocalPath(item?.attachment);
}

function attachMediaToReply(item) {
  const path = mediaAttachmentPath(item);
  if (!path) {
    el.threadMediaState.textContent = "No local media path";
    return;
  }
  if (state.replyMediaAttachments.some((entry) => entry.path === path)) {
    el.threadMediaState.textContent = "Media already attached";
    el.composer.focus();
    return;
  }
  state.replyMediaAttachments.push({
    path,
    label: item.label || basename(path) || "media",
    type: item.type || "file",
  });
  renderAttachments();
  buildCodexPrompt();
  el.threadMediaState.textContent = "Media attached to reply";
  el.sendState.textContent = "Media attached to reply";
  el.composer.focus();
}

function attachMediaToDraft(item) {
  const path = mediaAttachmentPath(item);
  if (!path) {
    el.threadMediaState.textContent = "No local media path";
    return;
  }
  if (state.draftMediaAttachments.some((entry) => entry.path === path)) {
    el.threadMediaState.textContent = "Media already in new chat";
    el.draftMessage.focus();
    return;
  }
  state.draftMediaAttachments.push({
    path,
    label: item.label || basename(path) || "media",
    type: item.type || "file",
  });
  state.draftAttachmentFolder = "";
  state.draftAttachmentPaths = [];
  renderAttachments("draft");
  renderDraftPreview();
  saveNewChatDraft();
  el.threadMediaState.textContent = "Media attached to new chat";
  el.draftState.textContent = "Media attached to new chat";
  el.draftMessage.focus();
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
      const copy = document.createElement("button");
      copy.type = "button";
      copy.dataset.action = "copy-link";
      copy.textContent = "Copy";
      copy.addEventListener("click", () => copyMediaLink(item));
      row.querySelector(".media-actions").append(copy);
      const attach = document.createElement("button");
      attach.type = "button";
      attach.dataset.action = "attach-reply";
      attach.textContent = "Attach";
      attach.addEventListener("click", () => attachMediaToReply(item));
      row.querySelector(".media-actions").append(attach);
      const attachDraft = document.createElement("button");
      attachDraft.type = "button";
      attachDraft.dataset.action = "attach-draft";
      attachDraft.textContent = "New";
      attachDraft.title = "Attach to new chat";
      attachDraft.addEventListener("click", () => attachMediaToDraft(item));
      row.querySelector(".media-actions").append(attachDraft);
    }
    el.threadMedia.append(row);
  }
}

function renderMessages() {
  const query = el.messageFilter.value.trim().toLowerCase();
  const terms = highlightTerms(query);
  renderMessageViewFilters();
  renderMessageHistoryControls();
  const rows = visibleLoadedMessages();

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
        <button type="button" data-action="draft">New draft</button>
        <button type="button" data-action="copy">Copy</button>
        <button type="button" data-action="add-contact">Add</button>
        <button type="button" data-action="contact">Contact</button>
        <button type="button" data-action="find-contact">Find</button>
        <button type="button" data-action="threads">Threads</button>
      </div>
    `;
    appendHighlightedText(item.querySelector(".message-head span"), messageSender(message), terms);
    item.querySelector("time").textContent = messageTime(message);
    appendHighlightedText(item.querySelector(".message-body"), message.body_text || message.text || "", terms);
    const noteText = messageNoteText(message);
    const editingNote = state.messageNoteEditorId && message.provider_message_id === state.messageNoteEditorId;
    const noteBox = item.querySelector(".message-note");
    if (noteText) {
      noteBox.hidden = false;
      appendHighlightedText(noteBox.querySelector("span"), noteText, terms);
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
    item.querySelector('[data-action="draft"]').addEventListener("click", () => useMessageAsNewChatDraft(message));
    const addContactButton = item.querySelector('[data-action="add-contact"]');
    addContactButton.disabled = !messageContactHandle(message);
    addContactButton.addEventListener("click", () => addLoadedMessageContactToDraft(message));
    const contactButton = item.querySelector('[data-action="contact"]');
    contactButton.disabled = !messageContactHandle(message);
    contactButton.addEventListener("click", () => useLoadedMessageContact(message));
    const findContactButton = item.querySelector('[data-action="find-contact"]');
    findContactButton.disabled = !messageContactHandle(message);
    findContactButton.addEventListener("click", () => searchMessagesForLoadedMessageContact(message));
    const threadsButton = item.querySelector('[data-action="threads"]');
    threadsButton.disabled = !messageContactHandle(message);
    threadsButton.addEventListener("click", () => filterConversationsForLoadedMessageContact(message));
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
      appendHighlightedText(pill, attachmentLabel(attachment), terms);
      attachmentBox.append(pill);
    }
    if (!attachments.length) attachmentBox.remove();
    el.messageList.append(item);
  }
}

function renderAttachmentFilePreview(chip, file) {
  const kind = attachmentFileKind(file);
  if (kind !== "audio" && kind !== "image") return;
  const url = attachmentPreviewUrl(file);
  if (!url) return;

  chip.classList.add("with-preview", `${kind}-attachment-chip`);
  if (kind === "audio") {
    const audio = document.createElement("audio");
    audio.controls = true;
    audio.preload = "metadata";
    audio.src = url;
    audio.setAttribute("aria-label", attachmentFileLabel(file));
    chip.append(audio);
    return;
  }

  const image = document.createElement("img");
  image.src = url;
  image.alt = attachmentFileLabel(file);
  image.loading = "lazy";
  chip.append(image);
}

function renderAttachments(target = "reply") {
  const { list } = attachmentElementsFor(target);
  const files = attachmentFilesFor(target);
  list.replaceChildren();
  for (const [index, file] of files.entries()) {
    const chip = document.createElement("div");
    chip.className = "attachment-chip";
    chip.innerHTML = `<span></span><button class="remove-button" type="button" title="Remove">×</button>`;
    chip.querySelector("span").textContent = attachmentFileLabel(file);
    renderAttachmentFilePreview(chip, file);
    chip.querySelector("button").addEventListener("click", () => {
      const nextFiles = attachmentFilesFor(target).slice();
      revokeAttachmentPreview(nextFiles[index]);
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
  const mediaAttachments = target === "draft" ? state.draftMediaAttachments : state.replyMediaAttachments;
  if (target === "reply" || target === "draft") {
    for (const [index, item] of mediaAttachments.entries()) {
      const chip = document.createElement("div");
      chip.className = "attachment-chip";
      chip.innerHTML = `<span></span><button class="remove-button" type="button" title="Remove">×</button>`;
      chip.querySelector("span").textContent = `${item.label} · existing ${item.type || "media"}`;
      chip.querySelector("button").addEventListener("click", () => {
        mediaAttachments.splice(index, 1);
        renderAttachments(target);
        if (target === "draft") {
          state.draftAttachmentFolder = "";
          state.draftAttachmentPaths = [];
          renderDraftPreview();
          saveNewChatDraft();
          el.draftState.textContent = "Media attachment removed";
        } else {
          buildCodexPrompt();
          el.sendState.textContent = "Media attachment removed";
        }
      });
      list.append(chip);
    }
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
  el.openMessagesButton.disabled = !hasSelection;
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
    el.openMessagesButton.textContent = "Open Messages";
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
  el.threadStatus.textContent = state.threadActionMessage || `${status}${excluded}${managed ? ` · ${managed}` : ""} · ${unread} unread · ${source}`;
  el.pinButton.textContent = selected.is_pinned ? "Unpin" : "Pin";
  el.muteButton.textContent = selected.is_muted ? "Unmute" : "Mute";
  el.archiveButton.textContent = selected.is_archived ? "Unarchive" : "Archive";
  el.connectionButton.textContent = status === "active" ? "Disconnect" : "Reconnect";
  el.openMessagesButton.textContent = "Open Messages";
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

function isThreadManagementEditing() {
  return [
    el.threadLocalTitle,
    el.threadFollowUpAt,
    el.threadTags,
    el.threadNote,
  ].includes(document.activeElement);
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
    el.senderBadge.textContent = "Messages";
    if (status.ok) {
      if (Date.now() < state.activityStatusUntil) return;
      el.statusLine.textContent = "Messages ready · local send enabled";
    } else {
      state.activityStatusUntil = 0;
      el.statusLine.textContent = "Messages warning";
    }
  } catch (error) {
    state.activityStatusUntil = 0;
    el.statusLine.textContent = `Messages offline · ${error.message}`;
    el.senderBadge.textContent = "Messages";
  }
}

async function loadConversations({ autoSelect = true, preserveManagementEditing = false, announceActivity = false } = {}) {
  try {
    const payload = await api("/penguin-connect/conversations");
    const conversations = payload.conversations || [];
    const activity = announceActivity ? newConversationActivity(conversations) : [];
    state.conversations = conversations;
    updateConversationActivitySnapshot(conversations);
    if (state.selected) {
      state.selected = state.conversations.find((conversation) => conversation.conversation_id === state.selected.conversation_id) || state.selected;
      renderThreadHeader();
      renderThreadControls();
      if (!preserveManagementEditing || !isThreadManagementEditing()) {
        renderManagementFields();
      }
    }
    el.senderBadge.textContent = "Messages";
    renderConversations();
    announceNewConversationActivity(activity);
    renderContacts();
    refreshDraftRecipientChips();
    if (autoSelect && !state.selected && state.conversations.length) {
      const initialConversation = visibleConversationRows()[0]
        || state.conversations.find((conversation) => !conversation.is_archived)
        || state.conversations[0];
      await selectConversation(initialConversation);
    }
  } catch (error) {
    el.conversationList.innerHTML = `<div class="error-state">${error.message}</div>`;
  }
}

async function loadContacts({ force = false } = {}) {
  const query = el.contactSearch.value.trim();
  const loadToken = state.contactLoadToken + 1;
  state.contactLoadToken = loadToken;
  const browsesUnsaved = state.contactSource === "participants";
  const browsesFavorites = state.contactSource === "favorites";
  const browsesNoted = state.contactSource === "noted";
  const browsesSaved = state.contactSource === "contacts";
  renderContactSourceFilters();
  state.contactsLoading = true;
  renderContactMoreControls();

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
      limit: String(state.contactLimit),
      source: state.contactSource,
    });
    const payload = await api(`/penguin-connect/contacts?${params.toString()}`);
    if (loadToken !== state.contactLoadToken) return;
    state.contacts = payload.contacts || [];
    state.contactsLoading = false;
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
    refreshDraftRecipientChips();
    buildCodexPrompt();
  } catch (error) {
    if (loadToken !== state.contactLoadToken) return;
    state.contacts = [];
    state.contactsLoading = false;
    el.contactStatus.textContent = error.message;
    renderContacts();
    refreshDraftRecipientChips();
    buildCodexPrompt();
  }
}

function scheduleContactSearch() {
  resetContactLimit();
  clearTimeout(state.contactSearchTimer);
  state.contactSearchTimer = setTimeout(() => loadContacts(), 180);
}

function resetContactLimit() {
  state.contactLimit = 20;
}

async function loadMoreContacts() {
  if (state.contactsLoading) return;
  const nextLimit = Math.min(state.contactLimit + state.contactLimitStep, state.contactLimitMax);
  if (nextLimit <= state.contactLimit) return;
  state.contactLimit = nextLimit;
  await loadContacts();
}

async function loadMessageSearch() {
  const query = el.globalMessageSearch.value.trim();
  const dateFrom = el.messageDateFrom.value.trim();
  const dateTo = el.messageDateTo.value.trim();
  const searchToken = state.messageSearchToken + 1;
  state.messageSearchToken = searchToken;
  const hasDateFilter = Boolean(dateFrom || dateTo);
  const scoped = state.messageSearchView !== "all";
  state.messageSearchLoading = false;
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
  state.messageSearchLoading = true;
  renderMessageSearchMoreControls();
  el.messageSearchStatus.textContent = query
    ? "Searching local cache"
    : hasDateFilter
      ? "Loading messages in date range"
      : `Loading ${view.label.toLowerCase()} messages`;
  try {
    const params = new URLSearchParams({
      query,
      limit: String(state.messageSearchLimit),
      view: state.messageSearchView,
    });
    if (dateFrom) params.set("date_from", dateFrom);
    if (dateTo) params.set("date_to", dateTo);
    if (state.messageSearchView === "current" && state.selected?.conversation_id) {
      params.set("conversation_id", state.selected.conversation_id);
    }
    const payload = await api(`/penguin-connect/messages/search?${params.toString()}`);
    if (searchToken !== state.messageSearchToken) return;
    state.messageSearchResults = payload.messages || [];
    state.messageSearchLoading = false;
    const rangeSuffix = hasDateFilter ? " in range" : "";
    el.messageSearchStatus.textContent = `${state.messageSearchResults.length} ${view.label.toLowerCase()} match${state.messageSearchResults.length === 1 ? "" : "es"}${rangeSuffix}`;
    renderMessageSearchResults();
    buildCodexPrompt();
  } catch (error) {
    if (searchToken !== state.messageSearchToken) return;
    state.messageSearchResults = [];
    state.messageSearchLoading = false;
    el.messageSearchStatus.textContent = error.message;
    renderMessageSearchResults();
    buildCodexPrompt();
  }
}

function resetMessageSearchLimit() {
  state.messageSearchLimit = 30;
}

function scheduleMessageSearch({ resetLimit = true } = {}) {
  if (resetLimit) resetMessageSearchLimit();
  clearTimeout(state.messageSearchTimer);
  state.messageSearchTimer = setTimeout(() => loadMessageSearch(), 180);
}

async function loadMoreMessageSearchResults() {
  if (state.messageSearchLoading || !searchHasRunnableInput()) return;
  const nextLimit = Math.min(state.messageSearchLimit + state.messageSearchLimitStep, state.messageSearchLimitMax);
  if (nextLimit <= state.messageSearchLimit) return;
  state.messageSearchLimit = nextLimit;
  await loadMessageSearch();
}

async function selectConversation(conversation) {
  state.selected = conversation;
  state.messages = [];
  state.messagesLoading = true;
  state.messageLimit = 200;
  state.threadActionMessage = "";
  resetThreadContactMatches();
  clearReplyContext();
  el.composer.value = draftTextForConversation(conversation);
  renderThreadHeader();
  renderThreadControls();
  renderManagementFields();
  renderThreadPeople();
  renderThreadMedia();
  renderConversations();
  scrollSelectedConversationIntoView();
  renderMessageSearchFilters();
  renderMessages();
  loadThreadContactMatches(conversation);
  if (state.messageSearchView === "current") {
    loadMessageSearch();
  }
  await loadMessages();
}

async function loadMessages({ preserveScroll = false, quiet = false } = {}) {
  if (!state.selected) return;
  const conversationId = state.selected.conversation_id;
  const beforeScrollHeight = preserveScroll ? el.messageList.scrollHeight : 0;
  const beforeScrollTop = preserveScroll ? el.messageList.scrollTop : 0;
  state.messagesLoading = true;
  if (quiet) {
    renderMessageHistoryControls();
  } else {
    renderMessages();
  }
  try {
    const payload = await api(`/penguin-connect/conversations/${encodeURIComponent(conversationId)}/messages?limit=${state.messageLimit}`);
    if (state.selected?.conversation_id !== conversationId) return;
    state.messages = payload.messages || [];
    state.messagesLoading = false;
    renderMessages();
    renderThreadMedia();
    buildCodexPrompt();
    requestAnimationFrame(() => {
      const focused = el.messageList.querySelector(".message.focused");
      if (preserveScroll) {
        el.messageList.scrollTop = Math.max(0, el.messageList.scrollHeight - beforeScrollHeight + beforeScrollTop);
      } else if (focused) {
        focused.scrollIntoView({ block: "center" });
      } else {
        el.messageList.scrollTop = el.messageList.scrollHeight;
      }
    });
  } catch (error) {
    state.messagesLoading = false;
    if (!quiet) {
      el.messageList.innerHTML = `<div class="error-state">${error.message}</div>`;
    } else {
      state.threadActionMessage = error.message;
      renderThreadControls();
    }
    renderMessageHistoryControls();
  }
}

async function loadOlderMessages() {
  if (!state.selected || state.messagesLoading) return;
  const nextLimit = Math.min(state.messageLimit + state.messageLimitStep, state.messageLimitMax);
  if (nextLimit <= state.messageLimit) return;
  state.messageLimit = nextLimit;
  await loadMessages({ preserveScroll: true });
}

async function refreshLocalMessages() {
  if (state.localRefreshBusy) return;
  const hadSelection = Boolean(state.selected);
  const conversationId = state.selected?.conversation_id || "";
  state.localRefreshBusy = true;
  state.threadActionMessage = "";
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

function shouldAutoRefreshLocalState() {
  return !document.hidden
    && !state.localRefreshBusy
    && !state.autoRefreshBusy
    && state.voiceMemoRecorder?.state !== "recording";
}

async function autoRefreshLocalState() {
  if (!shouldAutoRefreshLocalState()) return;
  const conversationId = state.selected?.conversation_id || "";
  state.autoRefreshBusy = true;
  try {
    await loadStatus();
    await loadConversations({
      autoSelect: false,
      preserveManagementEditing: true,
      announceActivity: true,
    });
    if (conversationId && state.selected?.conversation_id === conversationId && !state.messagesLoading) {
      await loadMessages({ preserveScroll: true, quiet: true });
    }
  } finally {
    state.autoRefreshBusy = false;
  }
}

function startAutoRefresh() {
  if (state.autoRefreshTimerId) return;
  state.autoRefreshTimerId = window.setInterval(autoRefreshLocalState, autoRefreshIntervalMs);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      autoRefreshLocalState();
    }
  });
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
  const outboundMessage = outgoingReplyText(message);
  const attachmentPaths = state.replyMediaAttachments.map((item) => item.path).filter(Boolean);
  if (!outboundMessage.trim() && !state.attachments.length && !attachmentPaths.length) {
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
        message: outboundMessage,
        attachment_paths: attachmentPaths,
        attachments,
      }),
    });
    el.composer.value = "";
    clearReplyContext();
    revokeAttachmentPreviews(state.attachments);
    state.attachments = [];
    state.replyMediaAttachments = [];
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
  state.threadActionMessage = "";
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

async function openSelectedConversationInMessages() {
  if (!state.selected) return;
  const conversationId = state.selected.conversation_id;
  el.openMessagesButton.disabled = true;
  state.threadActionMessage = "Opening Messages";
  el.threadStatus.textContent = state.threadActionMessage;
  try {
    const result = await api(`/penguin-connect/conversations/${encodeURIComponent(conversationId)}/open-messages`, {
      method: "POST",
      body: "{}",
    });
    const count = Number(result.participants_count || 0);
    state.threadActionMessage = result.opened_addressed
      ? `Opened Messages to ${count} recipient${count === 1 ? "" : "s"}`
      : "Opened Messages";
    el.threadStatus.textContent = state.threadActionMessage;
  } catch (error) {
    state.threadActionMessage = error.message;
    el.threadStatus.textContent = state.threadActionMessage;
  } finally {
    el.openMessagesButton.disabled = !state.selected;
    el.openMessagesButton.textContent = "Open Messages";
  }
}

async function setConversationManagement(fields) {
  if (!state.selected) return;
  state.threadActionMessage = "";
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

function selectedConversationParticipantHandles(targets = selectedConversationSnapshot()) {
  return uniqueRecipientValues(
    targets.flatMap((conversation) => conversationParticipants(conversation).map((participant) => participant.handle))
  );
}

function selectedConversationPeopleContactCandidates(targets = selectedConversationSnapshot()) {
  return [
    ...state.draftRecipientContactCache,
    ...state.contacts,
    ...Object.values(state.threadContactMatches || {}),
    ...(state.selected?.contact_context || []),
    ...targets.flatMap((conversation) => Array.isArray(conversation.contact_context) ? conversation.contact_context : []),
  ].filter((contact) => contact && typeof contact === "object");
}

function selectedConversationCreatablePeople(targets = selectedConversationSnapshot()) {
  const candidates = selectedConversationPeopleContactCandidates(targets);
  return selectedConversationParticipantHandles(targets)
    .filter((handle) => {
      const saved = bestContactForHandle(handle, candidates);
      return !(saved && saved.is_saved !== false);
    })
    .map((handle) => ({
      handle,
      contact: messageContactFromHandle(handle, handle),
    }));
}

function selectedConversationPeopleListName(targets = selectedConversationSnapshot()) {
  if (targets.length === 1) return `${conversationDisplayName(targets[0])} people`;
  const query = el.conversationSearch.value.trim();
  if (query) return `Selected people: ${query}`;
  return `${targets.length} selected conversations people`;
}

function addSelectedConversationPeopleToDraft() {
  const targets = selectedConversationSnapshot();
  const participants = selectedConversationParticipantHandles(targets);
  if (!participants.length) {
    state.bulkMessage = targets.length ? "No selected people" : "Select conversations";
    renderConversations();
    return;
  }

  const before = uniqueRecipientValues(draftRecipientValues());
  const beforeKeys = new Set(before.map(recipientCompareKey));
  const recipients = setDraftRecipients([...before, ...participants], { focus: true });
  const addedCount = recipients.filter((recipient) => !beforeKeys.has(recipientCompareKey(recipient))).length;
  state.bulkMessage = addedCount
    ? `Added ${addedCount} selected person${addedCount === 1 ? "" : "s"}`
    : "Selected people already added";
  el.draftState.textContent = state.bulkMessage;
  renderConversations();
}

async function copySelectedConversationPeople() {
  const targets = selectedConversationSnapshot();
  const participants = selectedConversationParticipantHandles(targets);
  if (!participants.length) {
    state.bulkMessage = targets.length ? "No selected people" : "Select conversations";
    renderConversations();
    return;
  }

  try {
    await copyText(participants.join("\n"));
    state.bulkMessage = `Copied ${participants.length} selected ${participants.length === 1 ? "person" : "people"}`;
  } catch (error) {
    state.bulkMessage = error.message;
  }
  renderConversations();
}

async function createSelectedConversationPeopleContacts() {
  const targets = selectedConversationSnapshot();
  const allHandles = selectedConversationParticipantHandles(targets);
  const items = selectedConversationCreatablePeople(targets);
  if (!allHandles.length) {
    state.bulkMessage = targets.length ? "No selected people" : "Select conversations";
    renderConversations();
    return;
  }
  if (!items.length) {
    state.bulkMessage = "Selected people contacts already saved";
    renderConversations();
    return;
  }

  state.bulkBusy = true;
  state.bulkMessage = `Creating ${items.length} selected contact${items.length === 1 ? "" : "s"}`;
  renderConversations();
  let created = 0;
  let skipped = 0;
  const failures = [];
  for (const item of items) {
    try {
      const existing = await lookupContactForMessageHandle(item.handle);
      if (existing && existing.is_saved !== false) {
        skipped += 1;
        continue;
      }
      await api("/penguin-connect/contacts", {
        method: "POST",
        body: JSON.stringify(contactCreatePayload(existing || item.contact)),
      });
      cacheDraftRecipientContact({
        ...draftCreatedContactFromHandle(item.handle),
        display_name: item.contact.display_name,
      });
      created += 1;
      state.bulkMessage = `Created ${created}/${items.length}`;
      renderConversations();
    } catch (error) {
      failures.push(error.message);
    }
  }

  try {
    if (created) {
      await api("/penguin-connect/contacts/refresh", { method: "POST", body: "{}" });
    }
    await loadContacts({ force: true });
    await loadThreadContactMatches();
    refreshDraftRecipientChips();
  } catch (error) {
    failures.push(error.message);
  } finally {
    state.bulkBusy = false;
    if (failures.length) {
      state.bulkMessage = `Created ${created}; ${failures.length} failed`;
    } else if (created) {
      state.bulkMessage = `Created ${created} selected contact${created === 1 ? "" : "s"}`;
    } else {
      state.bulkMessage = skipped
        ? "Selected people contacts already saved"
        : "No selected contacts created";
    }
    renderConversations();
    renderContacts();
  }
}

async function saveSelectedConversationPeopleAsRecipientList() {
  const targets = selectedConversationSnapshot();
  const participants = selectedConversationParticipantHandles(targets);
  if (!participants.length) {
    state.bulkMessage = targets.length ? "No selected people" : "Select conversations";
    renderConversations();
    return;
  }

  state.bulkBusy = true;
  state.bulkMessage = "Saving selected people";
  renderConversations();
  try {
    const result = await api("/penguin-connect/recipient-lists", {
      method: "POST",
      body: JSON.stringify({
        name: selectedConversationPeopleListName(targets),
        participants,
      }),
    });
    const saved = result.recipient_list || {};
    state.activeRecipientListId = saved.list_id || "";
    el.recipientListName.value = recipientListLabel(saved);
    setDraftRecipients(participants, { focus: true });
    mergeRecipientList(saved);
    renderRecipientLists();
    state.bulkMessage = `${recipientListLabel(saved)} saved`;
    el.draftState.textContent = state.bulkMessage;
  } catch (error) {
    state.bulkMessage = error.message;
  } finally {
    state.bulkBusy = false;
    renderConversations();
  }
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
  state.threadActionMessage = "";
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
  const uploads = state.attachments.map((file) => `${file.name} (${file.type || "file"}, ${file.size} bytes)`);
  const media = state.replyMediaAttachments.map((item) => `${item.label} (${item.type || "media"}, existing local media)`);
  return [...uploads, ...media].join(", ") || "none";
}

function newChatThreadMatchText() {
  if (state.draftThreadResolving) return "checking existing thread";
  const match = state.draftThreadMatch;
  if (!match) return "not checked";
  if (match.match_state === "exact") {
    return `exact: ${draftThreadMatchLabel(match.matched_conversation || {})}`;
  }
  if (match.match_state === "multiple") {
    const rows = draftThreadMatchRows().slice(0, 3).map(draftThreadMatchLabel).filter(Boolean);
    return `multiple: ${rows.join(" / ") || "matching threads"}`;
  }
  if (match.match_state === "error") return `error: ${match.error || "thread check failed"}`;
  return "none";
}

function newChatDraftContext() {
  const recipients = uniqueRecipientValues(draftRecipientValues());
  const body = draftBodyText();
  const attachments = draftAttachmentLabels();
  return [
    `Recipients: ${recipients.length ? recipients.join(", ") : "none"}`,
    `Recipient count: ${recipients.length}`,
    `Existing thread check: ${recipients.length ? newChatThreadMatchText() : "none"}`,
    `Attachments: ${attachments.length ? attachments.join(", ") : "none"}`,
    `Staged attachment folder: ${state.draftAttachmentFolder || "none"}`,
    `Draft body: ${body ? trim(body, 800) : "none"}`,
  ].join("\n");
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

function contactRecentContext(limit = 8) {
  const contact = activeContact();
  if (!contact || !state.activeContactMessages.length) return "none";
  const rows = state.activeContactMessages.slice(0, limit).map((message) => {
    const flags = [
      isUnreadMessage(message) ? "unread" : "",
      isStarredMessage(message) ? "starred" : "",
    ].filter(Boolean).join(", ");
    const flagText = flags ? ` | ${flags}` : "";
    const note = messageNoteText(message) ? ` | private note: ${trim(messageNoteText(message), 180)}` : "";
    return `${formatTime(message.message_timestamp || message.timestamp)} | ${searchResultConversationName(message)} | ${messageSender(message)}: ${messageSnippet(message, 180)}${flagText}${note}`;
  });
  return [
    `Contact: ${contactDisplayName(contact)} | ${contactHandleText(contact)}`,
    `Loaded: ${state.activeContactMessages.length} recent message${state.activeContactMessages.length === 1 ? "" : "s"} · window ${state.activeContactMessagesLimit}`,
    rows.join("\n"),
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
  el.useCodexNewChatButton.disabled = state.codexBusy || !hasAnswer;
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
    "New chat draft:",
    newChatDraftContext(),
    "",
    "Loaded contact context:",
    contactContext(),
    "",
    "Loaded contact recent messages:",
    contactRecentContext(),
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

function useCodexAnswerAsNewChatDraft() {
  const answer = codexAnswerText();
  if (!answer) return;
  el.draftMessage.value = answer;
  renderDraftPreview();
  buildCodexPrompt();
  saveNewChatDraft();
  el.draftState.textContent = "Codex answer moved to new chat";
  el.draftMessage.focus();
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
  revokeAttachmentPreviews(state.draftAttachments);
  state.draftAttachments = [];
  state.draftMediaAttachments = [];
  state.draftAttachmentFolder = "";
  state.draftAttachmentPaths = [];
  renderDraftRecipientChips();
  renderAttachments("draft");
  renderDraftPreview([]);
  clearDraftThreadMatch();
  renderRecipientLists();
  clearDraftRecipientSuggestions();
  clearSavedNewChatDraft();
}

async function stageDraft() {
  const participants = setDraftRecipients(draftRecipientValues());
  if (!participants.length) {
    el.draftState.textContent = "Add recipient";
    return;
  }
  if (state.voiceMemoRecorder?.state === "recording" && state.voiceMemoTarget === "draft") {
    el.draftState.textContent = "Stop voice memo before sending";
    return;
  }

  el.stageDraftButton.disabled = true;
  el.sendDraftButton.disabled = true;
  el.draftState.textContent = "Staging";
  try {
    const result = await api("/penguin-connect/messages/draft", {
      method: "POST",
      body: JSON.stringify(await draftRequestPayload(participants)),
    });
    applyDraftStageResult(result, participants);
  } catch (error) {
    el.draftState.textContent = error.message;
  } finally {
    el.stageDraftButton.disabled = false;
    el.sendDraftButton.disabled = false;
  }
}

async function draftRequestPayload(participants) {
  return {
    participants,
    message: el.draftMessage.value,
    attachment_paths: draftExistingAttachmentPaths(),
    attachments: await filesAsBrowserAttachments(state.draftAttachments),
    copy_to_clipboard: el.draftCopyToggle.checked,
    open_messages: false,
    open_addressed: el.draftOpenToggle.checked,
    open_attachments: el.draftOpenAttachmentsToggle.checked,
  };
}

function draftStageActions(result) {
  return [
    result.copied ? "copied" : "",
    result.opened_addressed ? "addressed chat opened" : result.opened_messages ? "opened" : "",
    result.opened_attachments ? "files opened" : result.attachment_count ? "files staged" : "",
  ].filter(Boolean).join(" + ");
}

function applyDraftStageResult(result, participants) {
  state.draftAttachmentFolder = result.attachment_folder || "";
  state.draftAttachmentPaths = result.attachment_paths || [];
  renderDraftPreview(result.participants || participants, result.draft || "");
  const actions = draftStageActions(result);
  el.draftState.textContent = actions ? `Draft ${actions}` : "Draft ready";
}

async function sendDraftIfExisting() {
  const participants = setDraftRecipients(draftRecipientValues());
  if (!participants.length) {
    el.draftState.textContent = "Add recipient";
    return;
  }
  if (state.voiceMemoRecorder?.state === "recording" && state.voiceMemoTarget === "draft") {
    el.draftState.textContent = "Stop voice memo before sending";
    return;
  }
  if (!el.draftMessage.value.trim() && !state.draftAttachments.length && !draftExistingAttachmentPaths().length) {
    el.draftState.textContent = "Nothing to send";
    return;
  }

  el.sendDraftButton.disabled = true;
  el.stageDraftButton.disabled = true;
  el.draftState.textContent = "Checking thread";
  try {
    const result = await api("/penguin-connect/messages/send-draft", {
      method: "POST",
      body: JSON.stringify(await draftRequestPayload(participants)),
    });
    if (result.send_mode === "sent") {
      const label = result.matched_conversation?.display_name || "existing thread";
      const conversationId = result.conversation_id || result.matched_conversation?.conversation_id || "";
      clearDraftForm();
      el.draftState.textContent = `Sent to ${label}`;
      await loadConversations({ autoSelect: false });
      if (conversationId && state.selected?.conversation_id === conversationId) {
        await loadMessages();
      }
      return;
    }

    applyDraftStageResult(result, participants);
    const reason = result.send_error === "multiple_matching_conversations"
      ? "multiple matching threads"
      : "no exact thread";
    const actions = draftStageActions(result);
    el.draftState.textContent = actions
      ? `${reason}; draft ${actions}`
      : `${reason}; draft ready`;
  } catch (error) {
    el.draftState.textContent = error.message;
  } finally {
    el.sendDraftButton.disabled = false;
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
    resetContactLimit();
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
  resetContactLimit();
  loadContacts({
    force: state.contactSource === "participants"
      || state.contactSource === "favorites"
      || state.contactSource === "noted"
      || state.contactSource === "contacts"
      || el.contactSearch.value.trim().length >= 2,
  });
});
el.contactSort.addEventListener("change", () => {
  state.contactSort = contactSortLabels[el.contactSort.value] ? el.contactSort.value : "default";
  renderContactSourceFilters();
  renderContacts();
});
el.loadMoreContactsButton.addEventListener("click", loadMoreContacts);
el.globalMessageSearch.addEventListener("input", scheduleMessageSearch);
el.messageDateFrom.addEventListener("input", scheduleMessageSearch);
el.messageDateTo.addEventListener("input", scheduleMessageSearch);
el.clearMessageDatesButton.addEventListener("click", () => {
  el.messageDateFrom.value = "";
  el.messageDateTo.value = "";
  resetMessageSearchLimit();
  loadMessageSearch();
});
el.globalMessageSearchFilters.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-message-search-view]");
  if (!button) return;
  state.messageSearchView = messageSearchViews.some((view) => view.key === button.dataset.messageSearchView)
    ? button.dataset.messageSearchView
    : "all";
  resetMessageSearchLimit();
  loadMessageSearch();
});
el.loadMoreSearchButton.addEventListener("click", loadMoreMessageSearchResults);
el.starSearchLoadedButton.addEventListener("click", starLoadedMessageSearchResults);
el.markSearchReadButton.addEventListener("click", markLoadedMessageSearchResultsRead);
el.markSearchUnreadButton.addEventListener("click", markLoadedMessageSearchResultsUnread);
el.addSearchSendersButton.addEventListener("click", addMessageSearchContactsToDraft);
el.addSearchParticipantsButton.addEventListener("click", addMessageSearchParticipantsToDraft);
el.saveSearchSendersButton.addEventListener("click", saveMessageSearchContactsAsRecipientList);
el.saveSearchParticipantsButton.addEventListener("click", saveMessageSearchParticipantsAsRecipientList);
el.createSearchSendersButton.addEventListener("click", createMessageSearchContacts);
el.createSearchParticipantsButton.addEventListener("click", createMessageSearchParticipantContacts);
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
el.contactFavoriteSelectedButton.addEventListener("click", () => setBulkContactFavorites(true));
el.contactUnfavoriteSelectedButton.addEventListener("click", () => setBulkContactFavorites(false));
el.contactCreateVisibleButton.addEventListener("click", createVisibleUnknownContacts);
el.contactClearSelectedButton.addEventListener("click", clearSelectedContacts);
el.messageFilter.addEventListener("input", renderMessages);
el.copyVisibleMessagesButton.addEventListener("click", copyVisibleLoadedMessages);
el.starVisibleMessagesButton.addEventListener("click", starVisibleLoadedMessages);
el.markVisibleMessagesReadButton.addEventListener("click", markVisibleLoadedMessagesRead);
el.markVisibleMessagesUnreadButton.addEventListener("click", markVisibleLoadedMessagesUnread);
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
  scheduleDraftThreadResolve(recipients);
  scheduleDraftRecipientSuggestions();
  saveNewChatDraft();
});
el.draftRecipientSuggestions.addEventListener("mousedown", (event) => {
  if (event.target.closest(".draft-recipient-suggestion")) {
    event.preventDefault();
  }
});
el.draftRecipients.addEventListener("blur", (event) => {
  if (event.relatedTarget && (
    el.draftRecipientChips.contains(event.relatedTarget)
    || el.draftRecipientSuggestions.contains(event.relatedTarget)
  )) return;
  setDraftRecipients(draftRecipientValues());
  clearDraftRecipientSuggestions();
});
el.recipientListName.addEventListener("input", saveNewChatDraft);
el.draftMessage.addEventListener("input", () => {
  renderDraftPreview();
  saveNewChatDraft();
});
el.draftCopyToggle.addEventListener("change", saveNewChatDraft);
el.draftOpenToggle.addEventListener("change", saveNewChatDraft);
el.draftOpenAttachmentsToggle.addEventListener("change", saveNewChatDraft);
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
el.openMessagesButton.addEventListener("click", openSelectedConversationInMessages);
el.loadMoreMessagesButton.addEventListener("click", loadOlderMessages);
el.connectionButton.addEventListener("click", toggleConnection);
el.clearReplyContextButton.addEventListener("click", clearReplyContext);
el.clearButton.addEventListener("click", () => {
  el.composer.value = "";
  clearReplyContext();
  revokeAttachmentPreviews(state.attachments);
  state.attachments = [];
  state.replyMediaAttachments = [];
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
el.threadPeopleCopyAllButton.addEventListener("click", copyThreadParticipants);
el.threadPeopleSaveListButton.addEventListener("click", saveThreadParticipantsAsRecipientList);
el.threadPeopleCreateAllButton.addEventListener("click", createUnknownThreadParticipants);
el.sendDraftButton.addEventListener("click", sendDraftIfExisting);
el.stageDraftButton.addEventListener("click", stageDraft);
el.copyDraftRecipientsButton.addEventListener("click", copyDraftRecipients);
el.copyDraftBodyButton.addEventListener("click", copyDraftBody);
el.copyDraftPreviewButton.addEventListener("click", copyDraftPreview);
el.draftCreateUnknownButton.addEventListener("click", createUnknownDraftRecipients);
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
el.useCodexNewChatButton.addEventListener("click", useCodexAnswerAsNewChatDraft);
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
el.conversationSort.addEventListener("change", () => {
  state.conversationSort = conversationSortLabels[el.conversationSort.value] ? el.conversationSort.value : "recent";
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
el.bulkAddPeopleButton.addEventListener("click", addSelectedConversationPeopleToDraft);
el.bulkCopyPeopleButton.addEventListener("click", copySelectedConversationPeople);
el.bulkSavePeopleButton.addEventListener("click", saveSelectedConversationPeopleAsRecipientList);
el.bulkCreatePeopleButton.addEventListener("click", createSelectedConversationPeopleContacts);
el.bulkClearDraftsButton.addEventListener("click", bulkClearDrafts);
el.bulkMarkReadButton.addEventListener("click", bulkMarkSelectedRead);
el.bulkPinButton.addEventListener("click", bulkPinSelected);
el.bulkMuteButton.addEventListener("click", bulkMuteSelected);
el.bulkArchiveButton.addEventListener("click", bulkArchiveSelected);
document.addEventListener("keydown", handleGlobalShortcuts);

const restoredNewChatDraft = restoreNewChatDraft();
if (restoredNewChatDraft) {
  el.draftState.textContent = "Local draft restored";
}

renderAllEmojiButtons();
renderAllVoiceMemoControls();
renderMessages();
renderContacts();
renderContactSourceFilters();
renderDraftRecipientChips();
renderAttachments();
renderAttachments("draft");
renderDraftPreview();
scheduleDraftThreadResolve();
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
startAutoRefresh();
loadContacts();
loadRecipientLists();
