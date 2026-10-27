# Full Loop Search — Whoosh + Streamlit

Tìm bài viết và tìm ảnh bằng văn bản, mở rộng bài Text Search Engine tuần trước.
Có comment tiếng Việt trong code để theo dõi từng bước.

## Cài đặt

Python 3.10 trở lên. Mở terminal tại thư mục chứa README này:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install firefox
```

Windows PowerShell: dùng `.venv\Scripts\Activate.ps1` để kích hoạt môi trường.
HTTP, HTML parser, XML và SQLite dùng thư viện chuẩn của Python. Riêng Báo Mới
cần Firefox của Playwright vì nội dung bài được dựng bằng JavaScript; VnExpress
vẫn dùng HTTP nhẹ như trước.

## Chạy ứng dụng

**Terminal 1 — giao diện:**

```bash
source .venv/bin/activate
streamlit run app.py
```

Mở http://localhost:8501. Chọn **Articles** để tìm bài hoặc **Images** để tìm ảnh.
Nhập `cao tốc` hoặc cụm từ có dấu ngoặc kép như `"Long Thành"`.
Có lọc nguồn, All words/Any word và phân trang.

**Terminal 2 — crawler chạy liên tục:**

```bash
source .venv/bin/activate
python crawler.py
```

Crawler kiểm tra ngay khi khởi động, rồi mỗi **900 giây (15 phút)** tính từ đầu
lượt trước. Nếu một lượt kéo dài hơn 15 phút, lượt tiếp theo chạy sau khi lượt
hiện tại kết thúc; không chạy chồng. Giữ terminal này mở. Ctrl+C dừng crawler;
chạy lại cùng lệnh để tiếp tục. Streamlit rerun không khởi động crawler mới.

Chạy thử một lượt nhỏ trước:

```bash
python crawler.py --once --source VnExpress --max-articles 5
```

Chỉ crawl Báo Mới:

```bash
python crawler.py --source 'Báo Mới'
```

Mặc định crawl cả hai nguồn, không giới hạn số bài phát hiện. `--max-articles`
chỉ giới hạn số bài tải trong một lượt, những bài còn lại vẫn ở hàng đợi.
`--once` thoát với mã lỗi 1 nếu gặp lỗi nguồn/bài để tránh báo thành công giả.
Nhấn **Refresh index status** hoặc tìm lại trên giao diện để đọc index mới nhất.

## Yêu cầu và cách thực hiện

| Yêu cầu | Cách làm |
| --- | --- |
| Text search | Whoosh tìm tiêu đề và văn bản bài; snapshot cũ chỉ có tiêu đề/mô tả |
| Text-based image search | Mỗi ảnh là một subdocument lưu URL ảnh, URL bài gốc và khối chữ đầu tiên ngay phía sau ảnh |
| So khớp đúng văn bản | Chế độ Images chỉ tìm trong đoạn chữ của ảnh, không tìm tiêu đề, URL hoặc alt text |
| Crawl mỗi 15 phút | Tiến trình `crawler.py` độc lập với Streamlit |
| Bỏ qua bài đã crawl | URL chuẩn hóa là khóa chính SQLite; chỉ tải bài chưa có payload thành công |
| Index tăng dần | Commit bài mới và ảnh vào index hiện có; không dựng lại toàn bộ |
| Phục hồi khi ngắt | Payload lưu trước khi index; bài chưa index được replay mà không tải lại HTML |

Ảnh được ghép theo thứ tự DOM trong **thân bài**: lấy khối chữ không rỗng đầu tiên
phía sau ảnh (caption hoặc đoạn văn). Không vượt qua ảnh kế tiếp để tìm chữ;
không dùng chữ trước ảnh. Bỏ ảnh không có chữ phía sau, ảnh ở menu/footer và
các vùng quảng cáo/liên quan được nhận diện. Nếu một bài không có ảnh phù hợp,
bài vẫn được lưu và index cho tìm kiếm văn bản.

## Nguồn dữ liệu và giới hạn

- Lần đầu tạo index mới, nhập 2.117 bản ghi Báo Mới và 65 bản ghi VnExpress từ
  hai JSON tuần 1. Chúng chưa có dữ liệu ảnh, nên Images ban đầu có thể rỗng.
- Crawler phát hiện bài từ RSS `https://vnexpress.net/rss/tin-moi-nhat.rss`
  và trang `https://baomoi.com/tin-moi.epi`, sau đó lấy HTML/JSON của từng bài
  chưa xử lý.
- Đây là phạm vi trang/feed mới nhất, không phải cam kết thu thập toàn bộ kho báo.
  Nếu dừng crawler lâu, bài đã rời feed có thể không được phát hiện.
- Với Báo Mới, crawler ưu tiên JSON response/embedded JSON mà chính trang dùng,
  ghép thumbnail với description trong cùng card bài; nếu không có JSON phù hợp
  mới fallback sang DOM. Parser DOM hỗ trợ `fck_detail`, article-body/article-content,
  `xeditor_content`, `content-detail` và `itemprop="articleBody"`. Crawler dùng
  một Firefox headless dùng lại trong cả lượt cho các bài Báo Mới, chờ DOM được
  render rồi mới parse. Nếu báo chặn hoặc đổi cấu trúc, lỗi được ghi lại và bài
  không bị đánh dấu thành công. Adapter live vẫn cần được chạy thử trên mạng của
  máy nộp vì website có thể tiếp tục thay đổi hoặc chặn tự động hóa.
- Ảnh được hiển thị bằng URL gốc; link hết hạn hoặc chống hotlink có thể làm ảnh
  không tải được. UI vẫn có nút mở ảnh gốc và bài gốc.
- Bài lỗi được thử lại với thời gian chờ tăng dần từ 15 phút đến tối đa 24 giờ.
  Bài đã crawl thành công không được tải lại để kiểm tra chỉnh sửa nội dung.
- Các snapshot và văn bản ảnh thuộc các nhà xuất bản, dùng cho bài tập IR.

## File cần đọc

| File | Vai trò |
| --- | --- |
| `app.py` | Giao diện và trạng thái phiên Streamlit |
| `article_parser.py` | Đọc HTML và ghép ảnh với văn bản ngay sau ảnh |
| `full_loop.py` | Schema bài/ảnh, tạo index một lần, thêm dữ liệu và tìm kiếm |
| `crawler.py` | Phát hiện URL, lưu SQLite, phục hồi và lịch 15 phút |
| `search_engine.py` | Hàm đọc snapshot/chuẩn hóa và phiên bản tìm kiếm tuần trước |
| `test_full_loop.py` | Ghép ảnh, chống trùng, kiểm tra resume và lỗi |
| `test_app.py` | Kiểm tra tương tác và hiển thị kết quả bằng Streamlit AppTest |

## Dữ liệu chạy được lưu ở đâu?

```text
.full_loop/
  crawl.sqlite       URL đã phát hiện, payload, lỗi, trạng thái index, lịch sử lượt
  index/             Whoosh index gồm bài viết và ảnh
  crawler.lock       Chỉ một crawler xử lý tại một thời điểm
  index.lock         Bảo vệ lúc tạo/ghi index
```

Toàn bộ `.full_loop/` được Git bỏ qua. Đường dẫn tính theo vị trí code, không theo
thư mục terminal hiện tại. Index `.whoosh_index/` của bài cũ được giữ nguyên;
ứng dụng mới dùng `.full_loop/index/` với schema mở rộng.

Giữ `.full_loop/` để tiếp tục tiến độ. Không xóa riêng index vì SQLite sẽ vẫn ghi
nhận các bài đã index. Nếu chủ động muốn làm lại từ đầu: dừng cả hai tiến trình,
sao lưu rồi xóa cả `.full_loop/`, sau đó khởi động lại. Thay đổi JSON không tự
kích hoạt dựng lại index. Không có nút rebuild trong GUI.

## Kiểm thử

```bash
python -m unittest -v test_search_engine.py test_full_loop.py test_app.py
```

Tests dùng HTML mẫu và thư mục tạm, không gọi mạng hoặc sửa snapshot thật.
Có kiểm tra ảnh không bị ghép với chữ trước nó/ngoài thân bài, chỉ khớp đoạn chữ
của ảnh, chạy lại không tải bài cũ hoặc thay đổi index, retry không tạo bản trùng,
và phục hồi payload đã lưu sau lỗi index. AppTest kiểm tra truy vấn, nguồn và
hiển thị ảnh; không xác nhận tải ảnh thật từ máy chủ báo.

## GitHub

Code và hai JSON có thể commit. Không commit môi trường `.venv/`, index,
SQLite hoặc secrets. Các bản nộp/ZIP cũ không được cập nhật tự động.

Tài liệu: [Whoosh indexing](https://whoosh.readthedocs.io/en/latest/indexing.html),
[Streamlit caching](https://docs.streamlit.io/develop/concepts/architecture/caching).
