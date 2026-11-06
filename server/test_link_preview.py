import unittest
from unittest import mock

import link_preview
from link_preview import get_link_preview, normalize_url, parse_preview, PreviewRefused


class LinkPreviewParseTests(unittest.TestCase):
    def test_reads_open_graph_tags(self):
        markup = """
        <html><head>
        <title>Fallback</title>
        <meta property="og:title" content="Slashy &amp; you">
        <meta property="og:description" content="  AI email
           that drafts for you ">
        <meta property="og:image" content="/img/card.png">
        <meta property="og:site_name" content="Slashy">
        </head></html>
        """
        preview = parse_preview(markup, "https://www.slashy.com/pricing")
        self.assertEqual(preview["title"], "Slashy & you")
        self.assertEqual(preview["description"], "AI email that drafts for you")
        self.assertEqual(preview["image"], "https://www.slashy.com/img/card.png")
        self.assertEqual(preview["site_name"], "Slashy")

    def test_falls_back_to_title_and_host(self):
        preview = parse_preview("<title>\n Plain page </title>", "https://www.example.com/a")
        self.assertEqual(preview["title"], "Plain page")
        self.assertEqual(preview["site_name"], "example.com")
        self.assertEqual(preview["image"], "")

    def test_rejects_non_http_images(self):
        markup = '<meta property="og:title" content="x"><meta property="og:image" content="javascript:alert(1)">'
        self.assertEqual(parse_preview(markup, "https://a.com")["image"], "")


class LinkPreviewSafetyTests(unittest.TestCase):
    def test_normalize_url(self):
        self.assertEqual(normalize_url("www.slashy.com"), "https://www.slashy.com")
        with self.assertRaises(PreviewRefused):
            normalize_url("file:///etc/passwd")
        with self.assertRaises(PreviewRefused):
            normalize_url("javascript:alert(1)")

    def test_private_hosts_are_refused(self):
        link_preview._cache.clear()
        for url in ("http://127.0.0.1:8888/penguin-connect/health", "http://localhost/", "http://192.168.1.1/"):
            preview = get_link_preview(url)
            self.assertFalse(preview["available"], url)
            self.assertEqual(preview["reason"], "private_host", url)

    def test_results_are_cached(self):
        link_preview._cache.clear()
        with mock.patch.object(link_preview, "_fetch", return_value={"url": "https://a.com", "available": False}) as fetch:
            get_link_preview("https://a.com")
            get_link_preview("https://a.com")
        self.assertEqual(fetch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
