"""Run with: streamlit run app.py"""

# Streamlit biến các lệnh Python thành thành phần trên trang web.
# `st` là tên viết tắt để gọi thư viện; time dùng để đo thời gian tìm kiếm.
import time
import streamlit as st
from full_loop import ensure_full_index, search_full, index_counts
from crawler import crawl_status

# Khi tương tác với widget ngoài form, Streamlit thường chạy lại file từ đầu.
# Vì vậy, biến thông thường sẽ được gán lại; dữ liệu cần nhớ dùng session_state.
st.set_page_config(page_title='News Search · Full Loop', page_icon='🔎', layout='centered')


# Decorator (dòng bắt đầu bằng @) bổ sung cơ chế cache cho hàm bên dưới.
# Cache giữ đối tượng index trong bộ nhớ để không mở lại sau mỗi lần rerun.
# Khi khởi động lại ứng dụng, ensure_full_index vẫn tái sử dụng index trên ổ đĩa.
@st.cache_resource
def get_index():
    # Streamlit reruns reuse this resource; disk persistence also covers restarts.
    return ensure_full_index()


st.title('🔎 News Search')
st.caption('Search articles and images by their following text.')
st.markdown('''<style>
.match { background: #ffe66d; color: #222; font-weight: 700; padding: 0 2px; border-radius: 3px; }
</style>''', unsafe_allow_html=True)
# try/except: nếu mở index thất bại, hiển thị lỗi và dừng lượt chạy hiện tại.
try:
    with st.spinner('Opening the news index…'):
        ix = get_index()
except Exception as exc:
    st.error(f'Cannot open the dataset/index: {exc}')
    st.stop()

# Các lệnh trong khối `with` này được hiển thị ở thanh bên.
# selectbox/radio vừa tạo giao diện, vừa trả về lựa chọn của người dùng.
with st.sidebar:
    st.header('Search options')
    counts = index_counts(ix)
    st.metric('Indexed articles', f"{counts['article']:,}")
    st.metric('Indexed images', f"{counts['image']:,}")
    search_type = st.radio('Search type', ['Articles', 'Images'])
    kind = 'image' if search_type == 'Images' else 'article'
    if st.button('Refresh index status'):
        st.rerun()
    status = crawl_status()
    if status and status['latest']:
        latest = status['latest']
        st.caption(f"Last crawl started: {latest['started_at']}")
        st.caption(f"Finished: {latest['finished_at'] or 'Running / interrupted'}")
        st.caption(f"Pending articles: {status['counts']['pending']}")
        if latest['report']:
            import json
            report = json.loads(latest['report'])
            if report['errors'] or report['failures']:
                st.warning('Last crawl had errors. Expand details below.')
                with st.expander('Crawl details'):
                    st.json(report)
    else:
        st.info('Start the crawler in another terminal: python crawler.py')
    collection = st.selectbox('Source', ['All sources', 'Báo Mới', 'VnExpress'])
    mode = st.radio('Match words', ['All words', 'Any word'])
    page_size = st.selectbox('Results per page', [10, 20, 50])
    st.caption('Images match only the text immediately following them. Articles match titles and available text.')
    if kind == 'image' and counts['image'] == 0:
        st.info('No images indexed yet. Run python crawler.py --once first.')
    st.caption('Vietnamese accents matter. Example: “cao tốc”.')

# Form gom ô nhập và nút Search: sửa nội dung ô nhập chưa gửi truy vấn ngay.
# Chỉ khi bấm nút, form mới gửi giá trị và kích hoạt chạy lại script.
with st.form('search_form'):
    query = st.text_input('Search query', placeholder='Try: cao tốc, thực phẩm, or "Long Thành"')
    submitted = st.form_submit_button('Search', type='primary')
# session_state giống một dictionary được giữ qua các lần rerun của phiên này.
# Lưu truy vấn đã gửi để đổi nguồn/trang vẫn tìm theo truy vấn đó.
if submitted:
    st.session_state['active_query'] = query.strip()

active_query = st.session_state.get('active_query', '')
if not active_query:
    st.info('Enter a query to find matching articles.')
    st.stop()

# Nếu đổi truy vấn hoặc tùy chọn, quay về trang 1 để tránh trang không tồn tại.
# Tuple này chỉ dùng để so sánh lựa chọn hiện tại với lần chạy trước.
signature = (active_query, collection, mode, page_size, kind)
if st.session_state.get('signature') != signature:
    st.session_state['signature'] = signature
    st.session_state['page'] = 1

# Giao diện giao việc tìm kiếm cho search_engine.py và nhận dictionary kết quả.
# Biểu thức mode == ... trả về True (tất cả từ) hoặc False (bất kỳ từ nào).
started = time.perf_counter()
try:
    result = search_full(ix, active_query, collection, mode == 'All words',
                              st.session_state.get('page', 1), page_size, kind=kind)
except Exception as exc:
    st.error(f'Search could not complete: {exc}')
    st.stop()
elapsed = time.perf_counter() - started
st.write(f"**{result['total']:,} matches** · {elapsed:.3f} seconds")
st.caption(f'Results for: {active_query}')
# Dictionary kết quả có total (số bài), pages (số trang), page và documents.
# Chỉ các bài thuộc trang hiện tại nằm trong danh sách documents.
if not result['documents']:
    st.info('No matching articles. Try fewer words, Any word, or another source.')
else:
    if result['pages'] > 1:
        # key='page' liên kết widget với st.session_state['page'].
        # Khi đổi trang, giá trị mới có sẵn trước khi script chạy lại.
        st.number_input('Page', min_value=1, max_value=result['pages'], step=1, key='page')
    st.caption(f"Page {result['page']} of {result['pages']} · Ranked by relevance")
    # Mỗi phần tử là một dictionary mô tả bài viết; vòng for tạo một thẻ cho mỗi bài.
    for document in result['documents']:
        with st.container(border=True):
            if document.get('title_highlight'):
                st.markdown(document['title_highlight'], unsafe_allow_html=True)
            else:
                st.subheader(document['title'])
            if kind == 'image':
                st.image(document['image_url'])
                st.link_button('Open original image ↗', document['image_url'])
            st.caption(f"{document['collection']} · {document['publisher']} · {document['published']}")
            highlighted = document.get('description_highlight')
            if highlighted:
                st.markdown(highlighted, unsafe_allow_html=True)
            else:
                description = document['description']
                st.write(description[:800] + ('…' if len(description) > 800 else '')
                         or 'No description available in the crawled dataset.')
            st.link_button('Read article ↗', document['url'])

with st.expander('Search tips'):
    st.write('Use quotes for phrases ("Long Thành"), AND / OR / NOT for Boolean queries, '
             'or title: to search only titles in Articles mode. All words is the default.')
