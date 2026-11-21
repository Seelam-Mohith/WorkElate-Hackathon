import json
import os
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import streamlit as st

DEFAULT_API_URL = os.environ.get("WORKELATE_API_URL", "http://127.0.0.1:8000")


class ApiError(Exception):
    def __init__(self, code, payload=None):
        super().__init__(code)
        self.code = code
        self.payload = payload or {}


def api_url():
    return st.session_state.get("api_url", DEFAULT_API_URL).rstrip("/")


def api_get(path):
    try:
        with urlopen(api_url() + path, timeout=10) as response:
            return json.loads(response.read())
    except HTTPError as exc:
        raise ApiError(f"HTTP {exc.code}") from None
    except (URLError, TimeoutError, ValueError):
        raise ApiError("backend_unreachable") from None


def api_post(path, body):
    request = Request(api_url() + path, data=json.dumps(body).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=180) as response:
            return json.loads(response.read())
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read())
        except (ValueError, UnicodeDecodeError):
            payload = {}
        raise ApiError(payload.get("error", f"HTTP {exc.code}"), payload) from None
    except (URLError, TimeoutError, ValueError):
        raise ApiError("backend_unreachable") from None


def show_error(exc):
    if exc.code == "previous_day_not_ingested":
        st.error("Ingest the previous day first. Run days in order: 1 → 2 → 3 → 4 → 5.")
    elif exc.code == "backend_unreachable":
        st.error("Backend is not reachable. Start it with `python -m workelate.api --port 8000`.")
    else:
        st.error(exc.code)
    if exc.payload:
        st.json(exc.payload)


def show_result(result):
    st.json(result)


st.set_page_config(page_title="WorkElate API Tester", page_icon=":material/smart_toy:", layout="wide")
st.title("WorkElate API Tester")
st.caption("Streamlit UI for the local WorkElate JSON API. Start the backend before using these forms.")

with st.sidebar:
    st.text_input("Backend URL", value=DEFAULT_API_URL, key="api_url")
    if st.button("Check backend", use_container_width=True):
        try:
            api_get("/api/health")
            st.success("Backend is healthy")
        except ApiError as exc:
            show_error(exc)

ingest_tab, brief_tab, ask_tab, forget_tab = st.tabs(["Ingest", "Brief", "Ask", "Forget"])

with ingest_tab:
    st.header("Ingest")
    st.caption("Load one day at a time. Day 1 resets the run.")
    day = st.number_input("Day", min_value=1, max_value=5, value=1, step=1, key="ingest_day")
    if st.button("Ingest day", type="primary"):
        try:
            show_result(api_post("/api/ingest", {"day": day}))
        except ApiError as exc:
            show_error(exc)
    st.divider()
    st.caption("Convenience: reset and load all five days in order.")
    if st.button("Ingest days 1–5"):
        progress = st.progress(0)
        for value in range(1, 6):
            try:
                result = api_post("/api/ingest", {"day": value})
                st.success(f"Day {value}: ok={result.get('ok')}")
            except ApiError as exc:
                show_error(exc)
                break
            progress.progress(value / 5)

with brief_tab:
    st.header("Brief")
    person = st.text_input("Person", value="p02", key="brief_person")
    meeting_date = st.date_input("Meeting date", value=date(2026, 11, 3), key="brief_date")
    pretty = st.checkbox("LLM-polished paragraph", value=True, key="brief_pretty")
    if st.button("Generate brief", type="primary"):
        try:
            result = api_post("/api/brief", {"person": person, "date": meeting_date.isoformat(), "pretty": pretty})
            if pretty:
                for item in result:
                    st.subheader(item.get("meeting_id", "Meeting"))
                    st.write(item.get("paragraph", ""))
            show_result(result)
        except ApiError as exc:
            show_error(exc)

with ask_tab:
    st.header("Ask")
    person = st.text_input("Person", value="p02", key="ask_person")
    at = st.text_input("At (UTC)", value="2026-11-03T06:00:00Z", key="ask_at")
    question = st.text_area("Question", value="What did I promise about checkout copy?", key="ask_question")
    if st.button("Ask", type="primary"):
        try:
            show_result(api_post("/api/ask", {"person": person, "at": at, "ask": question}))
        except ApiError as exc:
            show_error(exc)

with forget_tab:
    st.header("Forget")
    person = st.text_input("Person", value="p02", key="forget_person")
    what = st.text_input("What to forget", value="Forget my preference about short briefs", key="forget_what")
    if st.button("Forget", type="primary"):
        try:
            show_result(api_post("/api/forget", {"person": person, "what": what}))
        except ApiError as exc:
            show_error(exc)
