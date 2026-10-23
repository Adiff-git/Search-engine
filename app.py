"""Run with: streamlit run app.py"""

import time
import streamlit as st
from search_engine import ensure_index, search_documents

st.set_page_config(page_title='News Search · Week 2', page_icon='🔎', layout='centered')


@st.cache_resource
def get_index():
    # Streamlit reruns reuse this resource; disk persistence also covers restarts.
    return ensure_index()


st.title('🔎 News Search')
st.caption('Explore the Vietnamese news collected in Week 1.')
try:
    with st.spinner('Opening the news index…'):
        ix = get_index()
except Exception as exc:
    st.error(f'Cannot open the dataset/index: {exc}')
    st.stop()

with st.sidebar:
    st.header('Search options')
    st.metric('Indexed articles', f'{ix.doc_count():,}')
    collection = st.selectbox('Source', ['All sources', 'Báo Mới', 'VnExpress'])
    mode = st.radio('Match words', ['All words', 'Any word'])
    page_size = st.selectbox('Results per page', [10, 20, 50])
    st.caption('Search covers titles and available descriptions. Full article bodies were not collected.')
    st.caption('Vietnamese accents matter. Example: “cao tốc”.')

with st.form('search_form'):
    query = st.text_input('Search query', placeholder='Try: cao tốc, thực phẩm, or "Long Thành"')
    submitted = st.form_submit_button('Search', type='primary')
if submitted:
    st.session_state['active_query'] = query.strip()

active_query = st.session_state.get('active_query', '')
if not active_query:
    st.info('Enter a query to find matching articles.')
    st.stop()

signature = (active_query, collection, mode, page_size)
if st.session_state.get('signature') != signature:
    st.session_state['signature'] = signature
    st.session_state['page'] = 1

started = time.perf_counter()
try:
    result = search_documents(ix, active_query, collection, mode == 'All words',
                              st.session_state.get('page', 1), page_size)
except Exception as exc:
    st.error(f'Search could not complete: {exc}')
    st.stop()
elapsed = time.perf_counter() - started
st.write(f"**{result['total']:,} matches** · {elapsed:.3f} seconds")
st.caption(f'Results for: {active_query}')
if not result['documents']:
    st.info('No matching articles. Try fewer words, Any word, or another source.')
else:
    if result['pages'] > 1:
        st.number_input('Page', min_value=1, max_value=result['pages'], step=1, key='page')
    st.caption(f"Page {result['page']} of {result['pages']} · Ranked by relevance")
    for document in result['documents']:
        with st.container(border=True):
            st.subheader(document['title'])
            st.caption(f"{document['collection']} · {document['publisher']} · {document['published']}")
            st.write(document['description'] or 'No description available in the crawled dataset.')
            st.link_button('Read article ↗', document['url'])

with st.expander('Search tips'):
    st.write('Use quotes for phrases ("Long Thành"), AND / OR / NOT for Boolean queries, '
             'or title: to search only titles. All words is the default.')
