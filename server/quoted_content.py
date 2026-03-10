"""Utilities for extracting net-new email text without quoted reply noise."""

from __future__ import annotations

from dataclasses import dataclass
import html
from html.parser import HTMLParser
import re
import unicodedata
from typing import Optional

_MSO_CONDITIONAL_RE = re.compile(r"<!--\[if.*?<!\[endif\]-->", re.IGNORECASE | re.DOTALL)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_HTML_QUOTE_MARKER_RE = re.compile(
    r"<blockquote\b|gmail_quote|gmail_attr|gmail_extra|yahoo_quoted|protonmail_quote|moz-cite-prefix|"
    r"divRplyFwdMsg|mail-editor-reference-message-container|border-left\s*:|border-top\s*:",
    re.IGNORECASE,
)
_DISPLAY_NONE_RE = re.compile(r"\bdisplay\s*:\s*none\b", re.IGNORECASE)
_BORDER_LEFT_QUOTE_RE = re.compile(r"border-left\s*:[^;]*(solid|rgb\(204|#ccc|#999)", re.IGNORECASE)
_OUTLOOK_REPLY_BORDER_RE = re.compile(r"border-top\s*:[^;]*solid", re.IGNORECASE)

_FORWARDED_MARKER_RE = re.compile(r"^-{2,}\s*(forwarded|original) message\s*-{2,}\s*$", re.IGNORECASE)
_BEGIN_FORWARD_RE = re.compile(r"^begin forwarded message:\s*$", re.IGNORECASE)
_ON_WROTE_RE = re.compile(r"^on\s+.+\s+wrote:\s*$", re.IGNORECASE)
_INLINE_ON_WROTE_RE = re.compile(r"(?<!\S)(On\s+.+?\s+wrote:)", re.IGNORECASE)
_INLINE_FORWARD_RE = re.compile(
    r"(?<!\S)(-{2,}\s*(?:forwarded|original) message\s*-{2,}|Begin forwarded message:)",
    re.IGNORECASE,
)
_QUOTE_PREFIX_RE = re.compile(r"^(>+|\|)")
_DISCLAIMER_START_RE = re.compile(
    r"^(external email:|caution: this e-mail originated outside|notice: this e-mail|"
    r"confidentiality notice|this email and any attachments)",
    re.IGNORECASE,
)
_TRAILING_SIGNATURE_RE = re.compile(
    r"^(sent from my (iphone|ipad|mac|android|galaxy|pixel)|"
    r"get outlook for (ios|android)|sent with slashy|sent via superhuman|"
    r"sent from gmail mobile|sent from proton mail)$",
    re.IGNORECASE,
)
_HEADER_LINE_PREFIXES = ("from:", "sent:", "date:", "to:", "cc:", "bcc:", "subject:")
_SIGNOFF_RE = re.compile(
    r"^(best|thanks|thank you|regards|cheers|sincerely|warmly|appreciate it|talk soon|speak soon|"
    r"looking forward|see you|many thanks|thx|best regards|kind regards)[,!.\-]*$",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_URL_RE = re.compile(r"\b(?:https?://|www\.)\S+\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?:\+\d[\d().\-\s]{6,}\d|\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4})")
_SOCIAL_LINE_RE = re.compile(r"\b(linkedin|twitter|instagram|facebook|schedule|calendar|let'?s connect)\b", re.IGNORECASE)
_ROLE_LINE_RE = re.compile(
    r"\b(founder|co-founder|ceo|cto|cfo|chief|engineer|manager|director|partner|associate|investor|"
    r"product|sales|operations|marketing|support|team)\b",
    re.IGNORECASE,
)
_NAME_LIKE_RE = re.compile(r"^[A-Z][A-Za-z'`.-]+(?:\s+[A-Z][A-Za-z'`.-]+){0,3}$")
_IGNORED_HTML_CLASSES = {
    "gmail_quote",
    "gmail_attr",
    "gmail_extra",
    "yahoo_quoted",
    "protonmail_quote",
    "moz-cite-prefix",
    "front-blockquote",
}
_IGNORED_HTML_IDS = {
    "divrplyfwdmsg",
    "mail-editor-reference-message-container",
}
_BLOCK_BREAK_TAGS = {
    "address",
    "article",
    "aside",
    "br",
    "div",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "li",
    "main",
    "ol",
    "p",
    "section",
    "table",
    "td",
    "th",
    "tr",
    "ul",
}
_STRUCTURAL_HTML_TAGS = {"blockquote", "head", "link", "meta", "noscript", "script", "style", "svg", "title"}
_ZERO_WIDTH_CODEPOINTS = {
    0x034F,
    0x061C,
    0x00AD,
    0x180E,
    0x200B,
    0x200C,
    0x200D,
    0x2060,
    0xFEFF,
}


@dataclass(frozen=True)
class ParsedEmailBody:
    text: str
    source: str
    quoted_content_removed: bool
    signature_removed: bool
    safe_for_send: bool = True
    safety_flags: tuple[str, ...] = ()


def _normalize_text(value: str) -> str:
    if not value:
        return ""

    unescaped = html.unescape(value)
    normalized = unicodedata.normalize("NFKC", unescaped.replace("\r\n", "\n").replace("\r", "\n"))
    cleaned_chars: list[str] = []
    for char in normalized:
        codepoint = ord(char)
        category = unicodedata.category(char)
        if codepoint == 0x00A0:
            cleaned_chars.append(" ")
            continue
        if category in {"Cf", "Mn", "Me"} and codepoint not in _ZERO_WIDTH_CODEPOINTS:
            cleaned_chars.append(char)
            continue
        if codepoint in _ZERO_WIDTH_CODEPOINTS or category == "Cf":
            continue
        cleaned_chars.append(char)

    text = "".join(cleaned_chars)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _has_meaningful_text(value: str) -> bool:
    return bool(re.search(r"[A-Za-z0-9]", value or ""))


def _is_header_block_start(lines: list[str], index: int) -> bool:
    line = (lines[index] or "").strip().lower()
    if not line.startswith("from:"):
        return False

    window = [(lines[i] or "").strip().lower() for i in range(index, min(index + 6, len(lines)))]
    has_date = any(candidate.startswith("sent:") or candidate.startswith("date:") for candidate in window[1:])
    has_recipients = any(
        candidate.startswith("to:") or candidate.startswith("cc:") or candidate.startswith("bcc:") or candidate.startswith("subject:")
        for candidate in window[1:]
    )
    return has_date and has_recipients


def _is_quote_boundary(lines: list[str], index: int) -> bool:
    stripped = (lines[index] or "").strip()
    if not stripped:
        return False
    if _ON_WROTE_RE.match(stripped):
        return True
    if _FORWARDED_MARKER_RE.match(stripped) or _BEGIN_FORWARD_RE.match(stripped):
        return True
    if _DISCLAIMER_START_RE.match(stripped):
        return True
    if _is_header_block_start(lines, index):
        return True
    return False


def _trim_trailing_signature(lines: list[str]) -> tuple[list[str], bool]:
    if not lines:
        return lines, False

    trimmed = list(lines)
    removed = False
    while trimmed and not trimmed[-1].strip():
        trimmed.pop()

    while trimmed:
        stripped = trimmed[-1].strip()
        if not stripped:
            trimmed.pop()
            removed = True
            continue
        if _TRAILING_SIGNATURE_RE.match(stripped):
            trimmed.pop()
            removed = True
            continue
        if stripped in {"--", "__"}:
            trimmed.pop()
            removed = True
            while trimmed and not trimmed[-1].strip():
                trimmed.pop()
            continue
        break

    last_blank = -1
    for index in range(len(trimmed) - 1, -1, -1):
        if not trimmed[index].strip():
            last_blank = index
            break
    if last_blank >= 0:
        trailing_block = trimmed[last_blank + 1 :]
        if _looks_like_signature_block(trailing_block):
            trimmed = trimmed[:last_blank]
            removed = True
            while trimmed and not trimmed[-1].strip():
                trimmed.pop()

    for index, line in enumerate(trimmed):
        stripped = line.strip()
        if not _SIGNOFF_RE.match(stripped):
            continue
        trailing_block = trimmed[index:]
        if len([candidate for candidate in trailing_block if candidate.strip()]) >= 2:
            trimmed = trimmed[:index]
            removed = True
            while trimmed and not trimmed[-1].strip():
                trimmed.pop()
            break

    return trimmed, removed


def _has_contact_marker(value: str) -> bool:
    return bool(_EMAIL_RE.search(value) or _URL_RE.search(value) or _PHONE_RE.search(value) or _SOCIAL_LINE_RE.search(value))


def _looks_like_signature_block(block: list[str]) -> bool:
    normalized_block = [line.strip() for line in block if line.strip()]
    if len(normalized_block) < 2:
        return False
    contact_lines = sum(1 for line in normalized_block if _has_contact_marker(line))
    has_name_line = bool(_NAME_LIKE_RE.match(normalized_block[0]))
    has_role_line = any(_ROLE_LINE_RE.search(line) for line in normalized_block[1:])
    if contact_lines >= 2:
        return True
    if contact_lines >= 1 and (has_name_line or has_role_line):
        return True
    if len(normalized_block) >= 3 and has_name_line and has_role_line:
        return True
    return False


def _has_header_block(lines: list[str]) -> bool:
    return any(_is_header_block_start(lines, index) for index in range(len(lines)))


def _safety_flags_for_text(text: str, *, source: str) -> tuple[str, ...]:
    normalized = _normalize_text(text)
    if not normalized:
        return ()

    lines = normalized.split("\n")
    flags: list[str] = []

    if source == "snippet":
        flags.append("snippet_only")
    if _INLINE_ON_WROTE_RE.search(normalized) or any(_ON_WROTE_RE.match((line or "").strip()) for line in lines):
        flags.append("reply_header")
    if _INLINE_FORWARD_RE.search(normalized) or any(
        _FORWARDED_MARKER_RE.match((line or "").strip()) or _BEGIN_FORWARD_RE.match((line or "").strip())
        for line in lines
    ):
        flags.append("forward_header")
    if any(_QUOTE_PREFIX_RE.match((line or "").strip()) for line in lines if (line or "").strip()):
        flags.append("quote_prefix")
    if _has_header_block(lines):
        flags.append("header_block")
    if _looks_like_signature_block(lines[-4:]) or (_SIGNOFF_RE.match(lines[-1].strip()) if lines and lines[-1].strip() else False):
        flags.append("signature_tail")
    if re.search(r"<(?:/?[a-z][^>]*)>", normalized, re.IGNORECASE):
        flags.append("html_residue")

    return tuple(dict.fromkeys(flags))


def strip_quoted_plain_text(text: str) -> ParsedEmailBody:
    normalized = _normalize_text(text)
    if not normalized:
        return ParsedEmailBody(text="", source="plain", quoted_content_removed=False, signature_removed=False)

    inline_match = _INLINE_ON_WROTE_RE.search(normalized) or _INLINE_FORWARD_RE.search(normalized)
    inline_quote_removed = False
    if inline_match and inline_match.start() > 0:
        normalized = normalized[: inline_match.start()].rstrip()
        inline_quote_removed = True

    lines = normalized.split("\n")
    kept_lines: list[str] = []
    quoted_content_removed = inline_quote_removed

    for index, line in enumerate(lines):
        stripped = line.strip()
        if _is_quote_boundary(lines, index):
            quoted_content_removed = True
            break
        if _QUOTE_PREFIX_RE.match(stripped):
            quoted_content_removed = True
            if _has_meaningful_text("\n".join(kept_lines)):
                break
            continue
        if stripped.lower() in _HEADER_LINE_PREFIXES:
            quoted_content_removed = True
            break
        kept_lines.append(line)

    kept_lines, signature_removed = _trim_trailing_signature(kept_lines)
    cleaned = _normalize_text("\n".join(kept_lines))
    safety_flags = _safety_flags_for_text(cleaned, source="plain")
    return ParsedEmailBody(
        text=cleaned,
        source="plain",
        quoted_content_removed=quoted_content_removed,
        signature_removed=signature_removed,
        safe_for_send=not safety_flags,
        safety_flags=safety_flags,
    )


def _strip_mso_comments(value: str) -> str:
    if not value:
        return ""
    value = _MSO_CONDITIONAL_RE.sub("", value)
    return _HTML_COMMENT_RE.sub("", value)


def _attr_tokens(attrs: dict[str, str], name: str) -> set[str]:
    value = (attrs.get(name) or "").strip().lower()
    return {token for token in re.split(r"\s+", value) if token}


def _should_skip_html_element(tag: str, attrs: dict[str, str]) -> bool:
    normalized_tag = (tag or "").strip().lower()
    if normalized_tag in _STRUCTURAL_HTML_TAGS:
        return True

    class_tokens = _attr_tokens(attrs, "class")
    id_tokens = _attr_tokens(attrs, "id")
    if class_tokens & _IGNORED_HTML_CLASSES:
        return True
    if id_tokens & _IGNORED_HTML_IDS:
        return True

    style = (attrs.get("style") or "").lower()
    if _DISPLAY_NONE_RE.search(style):
        return True
    if _BORDER_LEFT_QUOTE_RE.search(style):
        return True
    if _OUTLOOK_REPLY_BORDER_RE.search(style):
        return True

    return False


class _EmailHtmlTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_stack: list[bool] = []
        self._chunks: list[str] = []

    def _is_skipping(self) -> bool:
        return bool(self._skip_stack and self._skip_stack[-1])

    def _append_break(self) -> None:
        if not self._chunks:
            return
        if self._chunks[-1].endswith("\n"):
            return
        self._chunks.append("\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attr_map = {name.lower(): value or "" for name, value in attrs}
        should_skip = self._is_skipping() or _should_skip_html_element(tag, attr_map)
        self._skip_stack.append(should_skip)
        if not should_skip and tag.lower() in _BLOCK_BREAK_TAGS:
            self._append_break()

    def handle_endtag(self, tag: str) -> None:
        was_skipping = self._skip_stack.pop() if self._skip_stack else False
        if not was_skipping and tag.lower() in _BLOCK_BREAK_TAGS:
            self._append_break()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attr_map = {name.lower(): value or "" for name, value in attrs}
        if self._is_skipping() or _should_skip_html_element(tag, attr_map):
            return
        if tag.lower() in _BLOCK_BREAK_TAGS:
            self._append_break()

    def handle_data(self, data: str) -> None:
        if self._is_skipping():
            return
        self._chunks.append(data)

    def text_content(self) -> str:
        return "".join(self._chunks)


def strip_quoted_html_text(html_text: str) -> ParsedEmailBody:
    if not html_text:
        return ParsedEmailBody(text="", source="html", quoted_content_removed=False, signature_removed=False)

    had_html_quote_markers = bool(_HTML_QUOTE_MARKER_RE.search(html_text))
    parser = _EmailHtmlTextParser()
    parser.feed(_strip_mso_comments(html_text))
    parser.close()
    plain = parser.text_content()
    parsed = strip_quoted_plain_text(plain)
    safety_flags = _safety_flags_for_text(parsed.text, source="html")
    return ParsedEmailBody(
        text=parsed.text,
        source="html",
        quoted_content_removed=had_html_quote_markers or parsed.quoted_content_removed,
        signature_removed=parsed.signature_removed,
        safe_for_send=not safety_flags,
        safety_flags=safety_flags,
    )


def extract_latest_email_text(
    *,
    plain_text: Optional[str] = None,
    html_text: Optional[str] = None,
    snippet: Optional[str] = None,
) -> ParsedEmailBody:
    if plain_text:
        parsed_plain = strip_quoted_plain_text(plain_text)
        if _has_meaningful_text(parsed_plain.text):
            return parsed_plain

    if html_text:
        parsed_html = strip_quoted_html_text(html_text)
        if _has_meaningful_text(parsed_html.text):
            return parsed_html

    if snippet:
        parsed_snippet = strip_quoted_plain_text(snippet)
        safety_flags = _safety_flags_for_text(parsed_snippet.text, source="snippet")
        if _has_meaningful_text(parsed_snippet.text):
            return ParsedEmailBody(
                text=parsed_snippet.text,
                source="snippet",
                quoted_content_removed=parsed_snippet.quoted_content_removed,
                signature_removed=parsed_snippet.signature_removed,
                safe_for_send=not safety_flags,
                safety_flags=safety_flags,
            )

    return ParsedEmailBody(text="", source="empty", quoted_content_removed=False, signature_removed=False)
