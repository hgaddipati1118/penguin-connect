"""Utilities for extracting net-new email text without quoted reply noise."""

from __future__ import annotations

from bs4 import BeautifulSoup, NavigableString, Tag
from dataclasses import dataclass
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import unicodedata
from typing import Iterable, Optional

_MSO_CONDITIONAL_RE = re.compile(r"<!--\[if.*?<!\[endif\]-->", re.IGNORECASE | re.DOTALL)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_HTML_QUOTE_MARKER_RE = re.compile(
    r"<blockquote\b|gmail_quote|gmail_attr|gmail_extra|yahoo_quoted|protonmail_quote|moz-cite-prefix|"
    r"divRplyFwdMsg|mail-editor-reference-message-container|border-left\s*:|border-top\s*:",
    re.IGNORECASE,
)
_HTML_PLAIN_REPLY_MARKER_RE = re.compile(r"(?:^|>)\s*On[\s\S]{0,200}?wrote:\s*(?=<|\n|$)", re.IGNORECASE)
_DISPLAY_NONE_RE = re.compile(r"\bdisplay\s*:\s*none\b", re.IGNORECASE)
_BORDER_LEFT_QUOTE_RE = re.compile(r"border-left\s*:[^;]*(solid|rgb\(204|#ccc|#999)", re.IGNORECASE)
_OUTLOOK_REPLY_BORDER_RE = re.compile(r"border-top\s*:[^;]*solid", re.IGNORECASE)

_FORWARDED_MARKER_RE = re.compile(r"^-{2,}\s*(forwarded|original) message\s*-{2,}\s*$", re.IGNORECASE)
_BEGIN_FORWARD_RE = re.compile(r"^begin forwarded message:\s*$", re.IGNORECASE)
_ON_WROTE_RE = re.compile(r"^on\s+.+\s+wrote:\s*$", re.IGNORECASE)
_INLINE_ON_WROTE_RE = re.compile(r"(?<!\S)(On\s+.+?\s+wrote:)", re.IGNORECASE)
_HTML_ON_WROTE_TEXT_RE = re.compile(r"^\s*On[\s\S]{0,200}?wrote:\s*$", re.IGNORECASE)
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
    r"get outlook for (ios|android)|sent via superhuman|"
    r"sent from gmail mobile|sent from proton mail)[.!]*$",
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
_SIGNATURE_MARKERS_FILE_ENV = "PENGUIN_CONNECT_SIGNATURE_MARKERS_FILE"
_DEFAULT_SIGNATURE_MARKERS_FILE = Path(__file__).resolve().parent.parent / ".penguin_connect_signature_markers.json"


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


def _reply_header_end_index(lines: list[str], index: int) -> Optional[int]:
    stripped = (lines[index] or "").strip()
    if not stripped:
        return None
    if _ON_WROTE_RE.match(stripped):
        return index + 1
    if not stripped.lower().startswith("on "):
        return None

    combined = stripped
    for candidate_index in range(index + 1, min(index + 4, len(lines))):
        candidate = (lines[candidate_index] or "").strip()
        if not candidate:
            break
        combined = f"{combined} {candidate}"
        if _ON_WROTE_RE.match(combined):
            return candidate_index + 1

    return None


def _is_quote_boundary(lines: list[str], index: int) -> bool:
    stripped = (lines[index] or "").strip()
    if not stripped:
        return False
    if _reply_header_end_index(lines, index) is not None:
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


def _normalize_signature_markers(markers: Optional[Iterable[str]]) -> tuple[str, ...]:
    if not markers:
        return ()
    normalized_markers: list[str] = []
    for raw_marker in markers:
        if not raw_marker:
            continue
        for chunk in re.split(r"\|\||\r?\n", raw_marker):
            marker = _normalize_text(chunk).casefold()
            if marker:
                normalized_markers.append(marker)
    return tuple(dict.fromkeys(normalized_markers))


def _default_signature_markers() -> tuple[str, ...]:
    raw_path = (os.environ.get(_SIGNATURE_MARKERS_FILE_ENV) or "").strip()
    path = Path(raw_path).expanduser() if raw_path else _DEFAULT_SIGNATURE_MARKERS_FILE
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ()
    if isinstance(payload, dict):
        return _normalize_signature_markers(payload.get("signature_markers"))
    if isinstance(payload, list):
        return _normalize_signature_markers(payload)
    return ()


def _trim_custom_signature_block(lines: list[str], signature_markers: tuple[str, ...]) -> tuple[list[str], bool]:
    markers = signature_markers
    if not lines or not markers:
        return lines, False

    for index, line in enumerate(lines):
        normalized_line = _normalize_text((line or "").strip()).casefold()
        if not normalized_line:
            continue
        if any(normalized_line.startswith(marker) for marker in markers):
            trimmed = lines[:index]
            while trimmed and not trimmed[-1].strip():
                trimmed.pop()
            return trimmed, True

    return lines, False


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
    if _INLINE_ON_WROTE_RE.search(normalized) or any(_reply_header_end_index(lines, index) is not None for index in range(len(lines))):
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


def strip_quoted_plain_text(text: str, *, signature_markers: Optional[Iterable[str]] = None) -> ParsedEmailBody:
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

    kept_lines, custom_signature_removed = _trim_custom_signature_block(
        kept_lines,
        _normalize_signature_markers(signature_markers) or _default_signature_markers(),
    )
    kept_lines, signature_removed = _trim_trailing_signature(kept_lines)
    signature_removed = custom_signature_removed or signature_removed
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


def _remove_display_none_elements(soup: BeautifulSoup) -> bool:
    removed = False
    for element in list(soup.find_all(style=True)):
        if not isinstance(element, Tag):
            continue
        style = (element.get("style") or "").lower()
        if _DISPLAY_NONE_RE.search(style):
            element.decompose()
            removed = True
    return removed


def _remove_explicit_quote_elements(soup: BeautifulSoup) -> bool:
    removed = False
    for element in list(soup.find_all(True)):
        if not isinstance(element, Tag):
            continue
        attrs = element.attrs if isinstance(element.attrs, dict) else {}
        classes = {token.strip().lower() for token in attrs.get("class", []) if token}
        element_id = str(attrs.get("id") or "").strip().lower()
        style = str(attrs.get("style") or "").lower()
        if (
            element.name == "blockquote"
            or bool(classes & _IGNORED_HTML_CLASSES)
            or element_id in _IGNORED_HTML_IDS
            or _BORDER_LEFT_QUOTE_RE.search(style)
        ):
            element.decompose()
            removed = True
    return removed


def _text_looks_like_reply_header(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    if _HTML_ON_WROTE_TEXT_RE.match(normalized):
        return True
    lines = normalized.split("\n")
    return any(_reply_header_end_index(lines, index) is not None for index in range(len(lines)))


def _text_looks_like_header_block(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    lines = normalized.split("\n")
    return any(_is_header_block_start(lines, index) for index in range(len(lines)))


def _remove_outlook_quote_sections(soup: BeautifulSoup) -> bool:
    removed = False
    for element in list(soup.find_all(style=True)):
        if not isinstance(element, Tag):
            continue
        style = (element.get("style") or "").lower()
        if not _OUTLOOK_REPLY_BORDER_RE.search(style):
            continue
        text = _normalize_text(element.get_text("\n"))
        if not _text_looks_like_header_block(text):
            continue
        wrapper = element.parent if isinstance(element.parent, Tag) else element
        container = wrapper.parent if isinstance(wrapper.parent, Tag) else None
        if container is None:
            wrapper.decompose()
            removed = True
            continue
        remove_children = False
        for child in list(container.children):
            if child == wrapper:
                remove_children = True
            if remove_children and isinstance(child, Tag):
                child.decompose()
                removed = True
        break
    return removed


def _remove_reply_header_elements(soup: BeautifulSoup) -> bool:
    removed = False
    for element in list(soup.find_all(["div", "p", "span"])):
        if not isinstance(element, Tag):
            continue
        text = _normalize_text(element.get_text("\n"))
        if not text:
            continue
        if _text_looks_like_reply_header(text) or _text_looks_like_header_block(text):
            element.decompose()
            removed = True
    return removed


def _remove_quoted_text_nodes(soup: BeautifulSoup) -> bool:
    removed = False
    for text_node in list(soup.find_all(string=True)):
        if not isinstance(text_node, NavigableString):
            continue
        text_value = str(text_node)
        normalized = _normalize_text(text_value)
        if not normalized:
            continue
        if _text_looks_like_reply_header(text_value) or _text_looks_like_header_block(text_value):
            text_node.extract()
            removed = True
            continue
        filtered_lines = [
            line for line in text_value.splitlines() if not _QUOTE_PREFIX_RE.match((line or "").lstrip())
        ]
        if len(filtered_lines) == len(text_value.splitlines()):
            continue
        replacement = "\n".join(filtered_lines).strip()
        if replacement:
            text_node.replace_with(replacement)
        else:
            text_node.extract()
        removed = True
    return removed


def _clean_quoted_html_text(html_text: str) -> tuple[str, bool]:
    stripped_html = _strip_mso_comments(html_text)
    soup = BeautifulSoup(stripped_html, "html.parser")
    removed_any = False
    removed_any = _remove_display_none_elements(soup) or removed_any
    removed_any = _remove_explicit_quote_elements(soup) or removed_any
    removed_any = _remove_outlook_quote_sections(soup) or removed_any
    removed_any = _remove_reply_header_elements(soup) or removed_any
    removed_any = _remove_quoted_text_nodes(soup) or removed_any
    return soup.get_text("\n"), removed_any


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


def strip_quoted_html_text(html_text: str, *, signature_markers: Optional[Iterable[str]] = None) -> ParsedEmailBody:
    if not html_text:
        return ParsedEmailBody(text="", source="html", quoted_content_removed=False, signature_removed=False)

    had_html_quote_markers = bool(_HTML_QUOTE_MARKER_RE.search(html_text) or _HTML_PLAIN_REPLY_MARKER_RE.search(html_text))
    plain, dom_quote_removed = _clean_quoted_html_text(html_text)
    parsed = strip_quoted_plain_text(plain, signature_markers=signature_markers)
    safety_flags = _safety_flags_for_text(parsed.text, source="html")
    return ParsedEmailBody(
        text=parsed.text,
        source="html",
        quoted_content_removed=had_html_quote_markers or dom_quote_removed or parsed.quoted_content_removed,
        signature_removed=parsed.signature_removed,
        safe_for_send=not safety_flags,
        safety_flags=safety_flags,
    )


def extract_latest_email_text(
    *,
    plain_text: Optional[str] = None,
    html_text: Optional[str] = None,
    snippet: Optional[str] = None,
    signature_markers: Optional[Iterable[str]] = None,
) -> ParsedEmailBody:
    if html_text:
        parsed_html = strip_quoted_html_text(html_text, signature_markers=signature_markers)
        if _has_meaningful_text(parsed_html.text):
            return parsed_html

    if plain_text:
        parsed_plain = strip_quoted_plain_text(plain_text, signature_markers=signature_markers)
        if _has_meaningful_text(parsed_plain.text):
            return parsed_plain

    if snippet:
        parsed_snippet = strip_quoted_plain_text(snippet, signature_markers=signature_markers)
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
