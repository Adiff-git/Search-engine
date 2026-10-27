"""Đọc HTML bằng thư viện chuẩn: ghép ảnh với khối chữ đầu tiên ngay sau nó."""
from dataclasses import dataclass, field
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlsplit, urlunsplit


def canonical_url(value, base=''):
    parts = urlsplit(urljoin(base, value))
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        return ''
    # Các nguồn được hỗ trợ xác định bài bằng path, không dùng query/fragment.
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, '', ''))


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def text(self):
        if self.tag in ('script', 'style', 'noscript'):
            return ''
        return ' '.join(c.text() if isinstance(c, Node) else c for c in self.children)


class TreeParser(HTMLParser):
    """Cây DOM nhỏ, đủ cho HTML bài báo; không chạy JavaScript."""
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node('root')
        self.stack = [self.root]
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def clean(text):
    return ' '.join(text.split())


def parse_article(html, url, collection):
    root = TreeParser(html).root
    nodes = list(root.walk())
    def meta_value(*names):
        for node in nodes:
            if node.tag != 'meta':
                continue
            key = (node.attrs.get('property') or node.attrs.get('name') or
                   node.attrs.get('itemprop') or '').casefold()
            if key in names and node.attrs.get('content'):
                return clean(node.attrs['content'])
        return ''

    title = (next((clean(n.text()) for n in nodes if n.tag == 'h1'), '') or
             meta_value('og:title', 'twitter:title'))
    # Chỉ lấy thân bài, không tìm ảnh ở menu, quảng cáo hay danh sách bài liên quan.
    body_classes = {
        'fck_detail', 'article-body', 'article__body', 'article-content',
        'article__content', 'body-content',
        # Báo Mới dựng phần nội dung sau khi JavaScript chạy. Các class này
        # xuất hiện trên DOM đã render, không nhất thiết có trong HTML urllib.
        'xeditor_content', 'app_h5_article', 'content-detail',
    }
    body = next((n for n in nodes if (body_classes.intersection((n.attrs.get('class') or '').split()) or n.attrs.get('itemprop') == 'articleBody')), None)
    if not title:
        raise ValueError('Không tìm thấy tiêu đề/thân bài; trang bị chặn hoặc cần cập nhật bộ đọc HTML.')
    published = next((n.attrs.get('content', '') for n in nodes if n.tag == 'meta' and
                      (n.attrs.get('property') == 'article:published_time' or n.attrs.get('itemprop') == 'datePublished')), '')
    images, paragraphs = [], []
    pending, fragments = None, []

    def flush():
        nonlocal pending, fragments
        text = clean(' '.join(fragments))
        if text:
            paragraphs.append(text)
            if pending:
                images.append({'image_url': pending, 'description': text})
                pending = None
        fragments = []

    def visit(node):
        nonlocal pending, fragments
        if isinstance(node, str):
            fragments.append(node)
            return
        classes = (node.attrs.get('class') or '').split()
        if node.tag in ('script', 'style', 'noscript', 'aside', 'nav') or any(
                c in {'related-news', 'box-related', 'advertisement', 'ads'} for c in classes):
            return
        if node.tag == 'img':
            flush()  # Chữ trước ảnh không được gán cho chính ảnh đó.
            value = next((node.attrs.get(k) for k in ('data-src', 'data-original', 'src')
                          if node.attrs.get(k) and not node.attrs[k].startswith('data:')), '')
            if not value:
                value = (node.attrs.get('data-srcset') or node.attrs.get('srcset') or '').split(',')[0].strip().split(' ')[0]
            candidate = urljoin(url, value) if value else ''
            pending = candidate if urlsplit(candidate).scheme in ('http', 'https') else None
            return
        blocks = {'p', 'figcaption', 'div', 'h2', 'h3', 'li', 'td'}
        if node.tag in blocks:
            flush()
        for child in node.children:
            visit(child)
        if node.tag in blocks:
            flush()

    if body is not None:
        visit(body)
        flush()
    else:
        # Một số trang Báo Mới trả trang Next.js/metadata thay vì DOM thân bài.
        # OG description và image cùng thuộc một card bài, dùng làm fallback để
        # bài vẫn được index thay vì bị đánh dấu crawl thành công với payload rỗng.
        description = meta_value('og:description', 'description', 'twitter:description')
        image_url = meta_value('og:image', 'twitter:image')
        if description:
            paragraphs.append(description)
            if image_url and urlsplit(image_url).scheme in ('http', 'https'):
                images.append({'image_url': image_url, 'description': description})
    # Ảnh không có chữ phía sau hoặc đứng ngay trước ảnh khác được bỏ qua.
    if not paragraphs:
        raise ValueError('Thân bài không có văn bản; không đánh dấu crawl thành công.')
    seen = set()
    unique = []
    for image in images:
        if image['image_url'] not in seen:
            seen.add(image['image_url'])
            unique.append(image)
    return dict(url=canonical_url(url), title=title, description='\n'.join(paragraphs),
                collection=collection, publisher=collection, published=published, images=unique)


def listing_links(html, base):
    """Báo Mới: chỉ chọn URL bài /...-c123.epi trên cùng tên miền."""
    return sorted({url for n in TreeParser(html).root.walk() if n.tag == 'a'
                   and (url := canonical_url(n.attrs.get('href', ''), base))
                   and urlsplit(url).hostname == 'baomoi.com'
                   and re.search(r'/[^/]+-c\d+\.epi$', urlsplit(url).path)})
