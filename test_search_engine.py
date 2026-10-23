import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from search_engine import ensure_index, search_documents


class SearchEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.data = root / 'articles.json'
        self.data.write_text(json.dumps({'articles': [
            {'title': 'Cao tốc Long Thành', 'description': 'Dự án giao thông',
             'url': 'https://example.com/1#fragment'},
            {'title': 'Duplicate', 'url': 'https://example.com/1'},
            {'title': 'Thực phẩm tốt', 'description': 'Sức khỏe',
             'url': 'https://example.com/2'},
        ]}), encoding='utf-8')
        self.index_dir = root / 'index'
        self.ix = ensure_index(self.index_dir, [(self.data, 'Báo Mới')])
        self.addCleanup(self.ix.close)

    def test_search_vietnamese_phrase_description_and_source(self):
        self.assertEqual(self.ix.doc_count(), 2)
        for query in ['CAO TỐC', '"Long Thành"', '"giao thông"']:
            result = search_documents(self.ix, query)
            self.assertEqual(result['total'], 1)
            self.assertEqual(result['documents'][0]['url'], 'https://example.com/1')
        self.assertEqual(search_documents(self.ix, 'cao', 'VnExpress')['total'], 0)
        self.assertEqual(search_documents(self.ix, 'notfound')['total'], 0)
        self.assertEqual(search_documents(self.ix, '   ')['total'], 0)

    def test_boolean_and_pagination(self):
        self.assertEqual(search_documents(self.ix, 'cao phẩm')['total'], 0)
        result = search_documents(self.ix, 'cao phẩm', match_all=False, page=2, page_size=1)
        self.assertEqual((result['total'], result['pages'], result['page']), (2, 2, 2))
        self.assertEqual(len(result['documents']), 1)

    def test_reopening_does_not_read_dataset_or_write_index(self):
        before = {p.name: p.read_bytes() for p in self.index_dir.iterdir()}
        self.data.unlink()  # Reopening must even work without the original JSON.
        with patch('search_engine.load_documents', side_effect=AssertionError('Re-indexed')):
            reopened = ensure_index(self.index_dir, [(self.data, 'Báo Mới')])
            self.assertEqual(reopened.doc_count(), 2)
            reopened.close()
        after = {p.name: p.read_bytes() for p in self.index_dir.iterdir()}
        self.assertEqual(before, after)

    def test_failed_build_can_retry(self):
        destination = Path(self.temp.name) / 'retry'
        missing = Path(self.temp.name) / 'missing.json'
        with self.assertRaises(FileNotFoundError):
            ensure_index(destination, [(missing, 'Báo Mới')])
        self.assertFalse(destination.exists())
        recovered = ensure_index(destination, [(self.data, 'Báo Mới')])
        self.assertEqual(recovered.doc_count(), 2)
        recovered.close()


if __name__ == '__main__':
    unittest.main()
