"""Mobile responsive styling for warehouse / phone use.

Injects a small CSS block scoped to narrow viewports (Android portrait, the
target device for warehouse staff). All rules live inside a `@media` query so
desktop layouts are left completely untouched.

Call `inject_mobile_css()` once near the top of a page's render() — it is
idempotent-safe to call per rerun (Streamlit just re-emits the <style> block).

Presentation only: no widgets, no session state, no logic.
"""
import streamlit as st

# Target: modern Android portrait (~360–430px). 640px breakpoint covers
# phones in portrait and small tablets without affecting desktop.
_MOBILE_CSS = """
<style>
@media (max-width: 640px) {

    /* Reclaim the large default top padding so content is visible without
       scrolling past empty space. */
    .block-container {
        padding-top: 1.2rem !important;
        padding-left: 0.8rem !important;
        padding-right: 0.8rem !important;
        padding-bottom: 3rem !important;
    }

    /* Bigger, thumb-friendly tap targets. 44px is the standard minimum. */
    .stButton > button,
    .stDownloadButton > button {
        min-height: 46px !important;
        font-size: 1rem !important;
        border-radius: 10px !important;
    }

    /* Inputs at 16px font prevents the mobile browser from auto-zooming the
       page when a field is focused. */
    .stTextInput input,
    .stNumberInput input,
    .stSelectbox div[data-baseweb="select"] > div,
    .stDateInput input {
        font-size: 16px !important;
        min-height: 44px !important;
    }

    /* Tab bar: let it scroll horizontally instead of cramping 6 tabs into the
       viewport. Tabs keep their full label on one line. */
    .stTabs [data-baseweb="tab-list"] {
        overflow-x: auto !important;
        flex-wrap: nowrap !important;
        scrollbar-width: thin;
    }
    .stTabs [data-baseweb="tab-list"]::-webkit-scrollbar {
        height: 3px;
    }
    .stTabs [data-baseweb="tab"] {
        flex: 0 0 auto !important;
        white-space: nowrap !important;
    }

    /* Page title a touch smaller so it doesn't dominate a small screen. */
    h1 { font-size: 1.5rem !important; }

    /* Dataframes can overflow horizontally — make that scroll obvious and
       keep them from blowing out the layout width. */
    .stDataFrame { width: 100% !important; }
}
</style>
"""


def inject_mobile_css() -> None:
    """Emit the mobile responsive stylesheet. Safe to call every rerun."""
    st.markdown(_MOBILE_CSS, unsafe_allow_html=True)
