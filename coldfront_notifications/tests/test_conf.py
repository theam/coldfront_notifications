"""Unit tests for coldfront_notifications.conf defaults."""
import unittest

import coldfront_notifications.tests.django_setup  # noqa: F401


class TestConfDefaults(unittest.TestCase):
    """Tests that conf.py exports sensible defaults."""

    def test_batch_size_default(self):
        from coldfront_notifications.conf import BATCH_SIZE
        self.assertEqual(BATCH_SIZE, 50)

    def test_batch_delay_default(self):
        from coldfront_notifications.conf import BATCH_DELAY
        self.assertEqual(BATCH_DELAY, 2.0)

    def test_max_retries_default(self):
        from coldfront_notifications.conf import MAX_RETRIES
        self.assertEqual(MAX_RETRIES, 3)

    def test_retry_delay_default(self):
        from coldfront_notifications.conf import RETRY_DELAY
        self.assertEqual(RETRY_DELAY, 1.0)

    def test_preview_page_size_default(self):
        from coldfront_notifications.conf import PREVIEW_PAGE_SIZE
        self.assertEqual(PREVIEW_PAGE_SIZE, 50)

    def test_preview_render_limit_default(self):
        from coldfront_notifications.conf import PREVIEW_RENDER_LIMIT
        self.assertEqual(PREVIEW_RENDER_LIMIT, 10)


if __name__ == "__main__":
    unittest.main()
