import unittest

from quoted_content import extract_latest_email_text, strip_quoted_html_text, strip_quoted_plain_text


class QuotedContentTests(unittest.TestCase):
    def test_strip_quoted_plain_text_keeps_only_latest_reply(self):
        parsed = strip_quoted_plain_text(
            "Latest reply\n\nSent from my iPhone\n\nOn Tue, Mar 10, 2026 at 10:00 AM Alice <alice@example.com> wrote:\n> Older line\n> Another line"
        )

        self.assertEqual(parsed.text, "Latest reply")
        self.assertTrue(parsed.quoted_content_removed)
        self.assertTrue(parsed.signature_removed)

    def test_strip_quoted_plain_text_removes_forward_header_block(self):
        parsed = strip_quoted_plain_text(
            "Works for me.\n\nFrom: Alice <alice@example.com>\nSent: Tuesday, March 10, 2026 10:00 AM\nTo: Bob <bob@example.com>\nSubject: Re: Hello"
        )

        self.assertEqual(parsed.text, "Works for me.")
        self.assertTrue(parsed.quoted_content_removed)

    def test_extract_latest_email_text_uses_html_fallback(self):
        parsed = extract_latest_email_text(
            html_text="""
            <div>Newest reply</div>
            <div class="gmail_quote">
              <div>On Mar 10, 2026, Alice wrote:</div>
              <blockquote>Older quoted content</blockquote>
            </div>
            """
        )

        self.assertEqual(parsed.text, "Newest reply")
        self.assertEqual(parsed.source, "html")
        self.assertTrue(parsed.quoted_content_removed)

    def test_strip_quoted_plain_text_unescapes_entities_and_strips_signature_block(self):
        parsed = strip_quoted_plain_text(
            "Haha, that&#39;s a feature!\n\nExample Person\nFounder\n202-555-0101\nperson@example.test\nLet&#39;s Connect"
        )

        self.assertEqual(parsed.text, "Haha, that's a feature!")
        self.assertTrue(parsed.signature_removed)
        self.assertTrue(parsed.safe_for_send)

    def test_strip_quoted_plain_text_handles_inline_reply_header(self):
        parsed = strip_quoted_plain_text(
            "Yes, that works for me. On Tue, Mar 10, 2026 at 10:00 AM Sender <sender@example.test> wrote: Older text"
        )

        self.assertEqual(parsed.text, "Yes, that works for me.")
        self.assertTrue(parsed.quoted_content_removed)
        self.assertTrue(parsed.safe_for_send)

    def test_strip_quoted_html_text_removes_outlook_quote_block(self):
        parsed = strip_quoted_html_text(
            """
            <div>Latest response</div>
            <div style="border-left:1px #ccc solid;padding-left:12px">
              <div>Earlier response</div>
            </div>
            """
        )

        self.assertEqual(parsed.text, "Latest response")
        self.assertTrue(parsed.quoted_content_removed)
        self.assertTrue(parsed.safe_for_send)

    def test_strip_quoted_html_text_removes_hidden_preheader_and_gmail_quote(self):
        parsed = strip_quoted_html_text(
            """
            <div style="display:none">Hidden preheader</div>
            <div>Latest response</div>
            <div class="gmail_quote">
              <div class="gmail_attr">On Tue, Mar 10, 2026, Sender wrote:</div>
              <blockquote>Earlier text</blockquote>
            </div>
            """
        )

        self.assertEqual(parsed.text, "Latest response")
        self.assertTrue(parsed.quoted_content_removed)
        self.assertTrue(parsed.safe_for_send)

    def test_extract_latest_email_text_marks_snippet_only_as_unsafe(self):
        parsed = extract_latest_email_text(snippet="Please send this quick update")

        self.assertEqual(parsed.text, "Please send this quick update")
        self.assertEqual(parsed.source, "snippet")
        self.assertFalse(parsed.safe_for_send)
        self.assertIn("snippet_only", parsed.safety_flags)

    def test_strip_quoted_plain_text_marks_html_residue_as_unsafe(self):
        parsed = strip_quoted_plain_text("Latest response <blockquote>Older response</blockquote>")

        self.assertEqual(parsed.text, "Latest response <blockquote>Older response</blockquote>")
        self.assertFalse(parsed.safe_for_send)
        self.assertIn("html_residue", parsed.safety_flags)


if __name__ == "__main__":
    unittest.main()
