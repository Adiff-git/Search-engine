"""Fixtures cục bộ kiểm tra toàn bộ vòng crawl, không gửi request lên báo."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from article_parser import parse_article, listing_links
from crawler import (SOURCES, HybridFetcher, article_from_baomoi_json, run_cycle,
                     open_database, sync_pending, crawl_status)
from full_loop import ensure_full_index, index_article, search_full, index_counts

URL = 'https://vnexpress.net/bai-viet-123.html'
HTML = '''<html><h1>Tiêu đề riêng biệt</h1><article class="fck_detail">
<p>Chữ trước ảnh không liên quan.</p>
<figure><img src="data:image/gif;base64,x" data-src="/photo-a.jpg">
<figcaption><p>Công nhân xây <b>cao tốc</b>.</p></figcaption></figure>
<p>Nội dung tiếp theo không gán cho ảnh A.</p>
<div><img src="/photo-b.jpg"></div><p>Trẻ em đến trường.</p>
<img src="/no-text.jpg"><img src="/last.jpg">
</article><footer><p>Chữ ngoài thân bài</p><img src="/logo.jpg"></footer></html>'''


class FullLoopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.ix = ensure_full_index(self.state, datasets=[])
        self.addCleanup(self.ix.close)

    def test_image_association_boundaries(self):
        article = parse_article(HTML, URL, 'VnExpress')
        self.assertEqual(article['images'], [
            {'image_url': 'https://vnexpress.net/photo-a.jpg', 'description': 'Công nhân xây cao tốc .'},
            {'image_url': 'https://vnexpress.net/photo-b.jpg', 'description': 'Trẻ em đến trường.'}])
        with self.assertRaises(ValueError):
            parse_article('<h1>Blocked</h1>', URL, 'VnExpress')
        self.assertEqual(listing_links('<a href="/tin-c123.epi?x=1">a</a><a href="/tin-c123.epi#x">b</a>',
                                       'https://baomoi.com'), ['https://baomoi.com/tin-c123.epi'])

    def test_rendered_baomoi_body_class(self):
        html = '''<html><h1>Bài Báo Mới</h1><main class="content-detail">
        <figure><img src="/anh.jpg"><figcaption>Chú thích ngay sau ảnh.</figcaption></figure>
        <p>Nội dung bài viết.</p></main></html>'''
        article = parse_article(html, 'https://baomoi.com/bai-c123.epi', 'Báo Mới')
        self.assertEqual(article['images'], [{
            'image_url': 'https://baomoi.com/anh.jpg',
            'description': 'Chú thích ngay sau ảnh.'}])

    def test_hybrid_fetcher_routes_only_baomoi_articles_to_browser(self):
        fetcher = HybridFetcher()
        with patch('crawler.fetch', return_value='static') as static:
            self.assertEqual(fetcher('https://baomoi.com/tin-moi.epi'), 'static')
            self.assertEqual(fetcher('https://vnexpress.net/a-1.html'), 'static')
        self.assertEqual(static.call_count, 2)

    def test_baomoi_api_object_becomes_image_subdocument(self):
        url = 'https://baomoi.com/bai-thu-c123.epi'
        payload = {'data': {'items': [{'contentId': 123, 'title': 'Tin mới',
            'description': 'Đoạn chữ đi cùng ảnh trên card.', 'date': 1_700_000_000,
            'publisher': {'name': 'Báo thử'},
            'thumb': 'https://photo-baomoi.bmcdn.me/2026_10_28_1_123/example.jpg.avif',
            'url': url}]}}
        article = article_from_baomoi_json(payload, url)
        self.assertEqual(article['publisher'], 'Báo thử')
        self.assertEqual(article['description'], 'Đoạn chữ đi cùng ảnh trên card.')
        self.assertEqual(article['images'], [{
            'image_url': 'https://photo-baomoi.bmcdn.me/2026_10_28_1_123/example.jpg.avif',
            'description': 'Đoạn chữ đi cùng ảnh trên card.'}])

    def test_image_query_only_searches_following_text_and_retry_is_idempotent(self):
        article = parse_article(HTML, URL, 'VnExpress')
        index_article(self.ix, article, self.state)
        index_article(self.ix, article, self.state)
        self.assertEqual(index_counts(self.ix), {'article': 1, 'image': 2})
        result = search_full(self.ix, '"cao tốc"', kind='image')
        self.assertEqual(result['total'], 1)
        self.assertTrue(result['documents'][0]['image_url'].endswith('photo-a.jpg'))
        self.assertEqual(search_full(self.ix, 'riêng biệt', kind='image')['total'], 0)
        self.assertEqual(search_full(self.ix, 'title:riêng', kind='image')['total'], 0)
        self.assertEqual(search_full(self.ix, 'trẻ', collection='Báo Mới', kind='image')['total'], 0)
        self.assertEqual(search_full(self.ix, 'riêng biệt')['total'], 1)

    def test_two_cycles_skip_download_and_resume_after_commit_crash(self):
        calls = []
        def fetcher(url):
            calls.append(url)
            return f'<rss><channel><item><link>{URL}</link></item></channel></rss>' if url == SOURCES['VnExpress'] else HTML
        first = run_cycle(self.state, ['VnExpress'], fetcher, delay=0, datasets=[])
        before = {p.name: p.read_bytes() for p in (self.state / 'index').iterdir()}
        second = run_cycle(self.state, ['VnExpress'], fetcher, delay=0, datasets=[])
        after = {p.name: p.read_bytes() for p in (self.state / 'index').iterdir()}
        self.assertEqual(first['fetched'], 1)
        self.assertEqual(second['fetched'], 0)
        self.assertEqual(calls.count(URL), 1)
        self.assertEqual(before, after)
        # Mô phỏng crash sau commit Whoosh nhưng trước khi SQLite ghi indexed=1.
        db = open_database(self.state)
        try:
            db.execute('UPDATE articles SET indexed=0')
            db.commit()
            with patch('crawler.fetch', side_effect=AssertionError('Must not download')):
                self.assertEqual(sync_pending(db, self.ix, self.state), 1)
            self.assertEqual(index_counts(self.ix), {'article': 1, 'image': 2})
        finally:
            db.close()
        self.assertEqual(crawl_status(self.state)['counts']['indexed'], 1)

    def test_saved_payload_survives_index_failure(self):
        def fetcher(url):
            return f'<rss><channel><item><link>{URL}</link></item></channel></rss>' if url == SOURCES['VnExpress'] else HTML
        with patch('crawler.index_article', side_effect=RuntimeError('simulated disk error')):
            with self.assertRaises(RuntimeError):
                run_cycle(self.state, ['VnExpress'], fetcher, delay=0, datasets=[])
        def only_feed(url):
            self.assertEqual(url, SOURCES['VnExpress'])
            return fetcher(url)
        result = run_cycle(self.state, ['VnExpress'], only_feed, delay=0, datasets=[])
        self.assertEqual(result['fetched'], 0)
        self.assertEqual(result['indexed'], 1)

    def test_failed_download_not_marked_done(self):
        def fetcher(url):
            if url == SOURCES['VnExpress']:
                return f'<rss><channel><item><link>{URL}</link></item></channel></rss>'
            raise OSError('HTTP 503')
        result = run_cycle(self.state, ['VnExpress'], fetcher, delay=0, datasets=[])
        self.assertEqual(result['failures'], 1)
        status = crawl_status(self.state)
        self.assertEqual(status['counts']['pending'], 1)
        self.assertEqual(status['counts']['indexed'], 0)


if __name__ == '__main__':
    unittest.main()
