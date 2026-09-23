"""Shared pytest fixtures for tests/ui/."""
import pytest
import streamlit as st


@pytest.fixture(autouse=True)
def _clear_streamlit_cache():
    """st.cache_data's underlying cache is process-global, not scoped to
    one AppTest instance or one test file -- without clearing it, one
    test's cached instrument-discovery result (or lack thereof) can
    silently leak into a completely different test. Runs before every
    test under tests/ui/.
    """
    st.cache_data.clear()
    yield
