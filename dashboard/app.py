"""Streamlit control room for the Alpaca Paper worker."""

from __future__ import annotations

import hashlib
import hmac
import os

import streamlit as st
from dotenv import load_dotenv

from botfolio.config import load_config
from botfolio.dashboard_ui import render_news, render_paper
from botfolio.events.store import EventStore
from botfolio.storage import Storage

st.set_page_config(page_title="Botfolio Paper Trading", page_icon="📊", layout="wide")


@st.cache_resource
def get_store(path: str) -> EventStore:
    return EventStore(Storage(path))


load_dotenv(os.environ.get("BOTFOLIO_ENV_FILE", ".env"))
dashboard_password = os.getenv("BOTFOLIO_DASHBOARD_PASSWORD", "")
if dashboard_password:
    password_hash = hashlib.sha256(dashboard_password.encode()).hexdigest()
    if not hmac.compare_digest(st.session_state.get("_dashboard_login", ""), password_hash):
        st.title("Sign in to Botfolio")
        st.write("Enter the dashboard password to open the paper trading dashboard.")
        with st.form("dashboard_sign_in", clear_on_submit=True):
            entered = st.text_input("Dashboard password", type="password")
            submitted = st.form_submit_button("Sign in", type="primary")
        if submitted:
            if hmac.compare_digest(entered.encode(), dashboard_password.encode()):
                st.session_state["_dashboard_login"] = password_hash
                st.rerun()
            else:
                st.error("That password did not match. Please try again.")
        st.stop()
    if st.sidebar.button("Sign out"):
        del st.session_state["_dashboard_login"]
        st.rerun()
cfg = load_config()
st.sidebar.title("Botfolio")
page = st.sidebar.radio("Explore", ["Paper trading", "News & AI"])
if st.sidebar.button("Refresh page"):
    st.rerun()

store = get_store(cfg.db_path)
if page == "Paper trading":
    render_paper(cfg, store.storage)
else:
    render_news(store)
