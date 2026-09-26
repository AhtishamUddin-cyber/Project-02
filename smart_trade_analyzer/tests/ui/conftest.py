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


@pytest.fixture(autouse=True)
def _isolated_tracking_db(tmp_path, monkeypatch):
    """Every UI test gets its own throwaway SQLite file for trade tracking, so
    tests never share state with each other or write into the real repo
    root. Runs before every test under tests/ui/.
    """
    monkeypatch.setenv("SMART_TRADE_ANALYZER_DB", str(tmp_path / "trade_history.sqlite3"))
    yield
