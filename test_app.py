"""Kiểm tra widget và kết quả bằng AppTest, không cần server hoặc mạng."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest
from article_parser import parse_article
from full_loop import ensure_full_index, index_article
from test_full_loop import HTML, URL


class AppTests(unittest.TestCase):
    def test_image_and_article_search(self):
        with tempfile.TemporaryDirectory() as directory:
            ix = ensure_full_index(directory, datasets=[])
            index_article(ix, parse_article(HTML, URL, 'VnExpress'), directory)
            st.cache_resource.clear()
            try:
                with patch('full_loop.ensure_full_index', return_value=ix), patch('crawler.crawl_status', return_value=None):
                    app = AppTest.from_file(str(Path(__file__).with_name('app.py'))).run()
                    app.radio[0].set_value('Images').run()
                    app.text_input[0].set_value('cao tốc')
                    app.button(key='FormSubmitter:search_form-Search').click().run()
                    self.assertFalse(app.exception)
                    self.assertEqual(len(app.get('image')) + len(app.get('imgs')), 1)
                    self.assertTrue(any('1 matches' in m.value for m in app.markdown))
                    app.selectbox[0].set_value('Báo Mới').run()
                    self.assertFalse(app.exception)
                    self.assertTrue(any('0 matches' in m.value for m in app.markdown))
                    app.radio[0].set_value('Articles').run()
                    app.selectbox[0].set_value('All sources').run()
                    self.assertFalse(app.exception)
                    self.assertTrue(any('1 matches' in m.value for m in app.markdown))
            finally:
                st.cache_resource.clear()
                ix.close()


if __name__ == '__main__':
    unittest.main()
