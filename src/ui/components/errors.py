"""Humane error display for the Streamlit UI.

Every page used to pair `logger.error(f"...: {e}", exc_info=True)` with
`st.error(f"...: {e}")` at each catch site — duplicating the exception text
into the UI (alarming, and a minor information disclosure). This helper
collapses both calls into one and keeps raw exception text out of the UI;
full detail still reaches the server logs.
"""
import logging

import streamlit as st


def show_error(logger: logging.Logger, message: str, exc: Exception) -> None:
    """Log the full exception server-side and show a friendly message to the user.

    Args:
        logger: The calling module's logger (from get_logger(__name__)).
        message: Plain-language description of what failed. Shown to the user
            as-is, and used as the log context — do not include the exception
            text in it (that's appended automatically for the log line only).
        exc: The caught exception.
    """
    logger.error(f"{message}: {exc}", exc_info=True)
    st.error(f"⚠️ {message}. Please try again, or check with support if this continues.")
