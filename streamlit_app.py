import json
import os
from urllib.parse import quote
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
        st.error("Ingest the previous day first. Run days in order: 1 → 2 → 3 → …")
    elif exc.code == "backend_unreachable":
        st.error("Backend is not reachable. Start it with `python -m workelate.api --port 8000`.")
    else:
        st.error(exc.code)
    if exc.payload:
        st.json(exc.payload)


def show_result(result):
    st.json(result)


def person_picker(people, key, label="Person"):
    names = {person["id"]: person["name"] for person in people}
    ids = list(names)
    default = ids.index("p02") if "p02" in ids else 0 if ids else None
    return st.selectbox(label, options=ids, index=default,
                        format_func=lambda person_id: f"{person_id} - {names[person_id]}",
                        placeholder="Ingest a day to load available people", key=key)


def date_options(person):
    if not person:
        return {"timezone": None, "brief_dates": [], "ask_dates": [], "record_dates": []}
    if person not in person_date_options:
        try:
            person_date_options[person] = api_get(f"/api/people/{quote(person, safe='')}/dates")
        except ApiError as exc:
            show_error(exc)
            return {"timezone": None, "brief_dates": [], "ask_dates": [], "record_dates": []}
    return person_date_options[person]


def mapped_date_picker(label, dates, person, section, empty_message="No available dates for this person"):
    key = f"{section}_date"
    mapped_person = f"{section}_date_person"
    # Reset on every person change, including returning to someone selected
    # earlier. Keep manual date choices only while the person stays the same.
    if st.session_state.get(mapped_person) != person or st.session_state.get(key) not in dates:
        st.session_state[key] = dates[-1] if dates else None
        st.session_state[mapped_person] = person
        st.session_state.pop(f"{section}_output", None)
    if not dates:
        st.info(empty_message)
        return None
    return st.selectbox(label, options=dates, index=None,
                        placeholder=empty_message, key=key)


def clear_outputs():
    for key in ("brief_output", "ask_output"):
        st.session_state.pop(key, None)


def show_briefs(result):
    if not result:
        st.info("No meetings are available for this person on the selected date.")
    for item in result:
        st.subheader(item.get("meeting_id", "Meeting"))
        if item.get("paragraph"):
            st.write(item["paragraph"])
        elif item.get("status") == "nothing_new":
            st.info("No new updates for this meeting.")
        else:
            for entry in item.get("open_these", [])[:1]:
                st.write(entry["why"])
            for change in item.get("changed", []):
                st.write(change["text"])
            for promise in item.get("promises", []):
                st.write(f"{promise['owner']}: {promise['what']} (due {promise['due']})")
            if item.get("question_to_ask"):
                st.write("Question to ask: " + item["question_to_ask"]["text"])
    with st.expander("Full response"):
        show_result(result)


def show_answer(result):
    if result.get("answer"):
        st.write(result["answer"]["text"])
        if result["answer"].get("sources"):
            st.caption("Sources: " + ", ".join(result["answer"]["sources"]))
    elif result.get("clarify"):
        st.info(result["clarify"]["question"])
    elif result.get("refused"):
        st.info(result["refused"]["reason"])
    with st.expander("Full response"):
        show_result(result)


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
    st.divider()
    st.subheader("Project stack")
    st.caption("Frontend: Streamlit")
    st.caption("Backend: Python JSON API")
    st.caption("Database: SQLite")
    st.caption("Vector DB: ChromaDB / SQLite")

ingest_tab, brief_tab, ask_tab, forget_tab = st.tabs(["Ingest", "Brief", "Ask", "Forget"])

with ingest_tab:
    st.header("Ingest")
    st.caption("Load one day at a time. Day 1 resets the run.")
    day = st.number_input("Day", min_value=1, value=1, step=1, key="ingest_day")
    if st.button("Ingest day", type="primary"):
        try:
            show_result(api_post("/api/ingest", {"day": day}))
            clear_outputs()
        except ApiError as exc:
            show_error(exc)
    st.divider()
    st.caption("Convenience: ingest any range of days in order (day 1 resets the run).")
    first_day, last_day = st.columns(2)
    with first_day:
        start_day = st.number_input("From day", min_value=1, value=1, step=1, key="ingest_start_day")
    with last_day:
        end_day = st.number_input("To day", min_value=1, value=5, step=1, key="ingest_end_day")
    st.caption("Days beyond the included pack need their own day-NN.jsonl source files.")
    if end_day < start_day:
        st.error("To day must be greater than or equal to From day.")
    elif st.button(f"Ingest days {start_day}–{end_day}"):
        total = end_day - start_day + 1
        progress = st.progress(0.0)
        for value in range(start_day, end_day + 1):
            try:
                result = api_post("/api/ingest", {"day": value})
                clear_outputs()
                st.success(f"Day {value}: ok={result.get('ok')}")
            except ApiError as exc:
                show_error(exc)
                break
            progress.progress((value - start_day + 1) / total)

try:
    people = api_get("/api/people")
except ApiError as exc:
    people = []
    show_error(exc)

person_date_options = {}

with ingest_tab:
    st.divider()
    person = person_picker(people, "ingest_person", label="Available people")
    dates = date_options(person)
    st.caption("Available record dates: " + (", ".join(dates["record_dates"]) or "None yet"))
    st.caption("Ingestion loads records for all people.")

with brief_tab:
    st.header("Brief")
    person = person_picker(people, "brief_person")
    dates = date_options(person)
    meeting_date = mapped_date_picker("Meeting date", dates["brief_dates"], person, "brief",
                                      empty_message="No meets available" if person else "No available dates for this person")
    if dates["brief_dates"]:
        st.caption("Meeting dates in " + (dates["timezone"] or "the selected person's timezone"))
    pretty = st.checkbox("LLM-polished paragraph", value=True, key="brief_pretty")
    brief_context = (person, meeting_date, pretty)
    if st.button("Generate brief", type="primary", disabled=not person or not meeting_date):
        st.session_state.pop("brief_output", None)
        try:
            with st.spinner("Generating brief..."):
                result = api_post("/api/brief", {"person": person, "date": meeting_date, "pretty": pretty})
            st.session_state["brief_output"] = (brief_context, result)
        except ApiError as exc:
            show_error(exc)
    saved_brief = st.session_state.get("brief_output")
    if saved_brief and saved_brief[0] == brief_context:
        show_briefs(saved_brief[1])

with ask_tab:
    st.header("Ask")
    person = person_picker(people, "ask_person")
    dates = date_options(person)
    query_date = mapped_date_picker("Answer as of date", [item["date"] for item in dates["ask_dates"]], person, "ask")
    at = next((item["at"] for item in dates["ask_dates"] if item["date"] == query_date), None)
    if at:
        st.caption(f"Through the end of {query_date} in {dates['timezone']}. UTC cutoff: {at}")
    question = st.text_area("Question", value="What did I promise about checkout copy?", key="ask_question")
    ask_context = (person, at, question)
    if st.button("Ask", type="primary", disabled=not person or not at or not question.strip()):
        st.session_state.pop("ask_output", None)
        try:
            with st.spinner("Getting answer..."):
                result = api_post("/api/ask", {"person": person, "at": at, "ask": question})
            st.session_state["ask_output"] = (ask_context, result)
        except ApiError as exc:
            show_error(exc)
    saved_answer = st.session_state.get("ask_output")
    if saved_answer and saved_answer[0] == ask_context:
        show_answer(saved_answer[1])

with forget_tab:
    st.header("Forget")
    person = person_picker(people, "forget_person")
    dates = date_options(person)
    st.caption("Available record dates: " + (", ".join(dates["record_dates"]) or "None yet"))
    what = st.text_input("What to forget", value="Forget my preference about short briefs", key="forget_what")
    if st.button("Forget", type="primary", disabled=not person):
        try:
            result = api_post("/api/forget", {"person": person, "what": what})
            clear_outputs()
            st.session_state["forget_output"] = (person, what, result)
            st.rerun()
        except ApiError as exc:
            show_error(exc)
    saved_forget = st.session_state.get("forget_output")
    if saved_forget and saved_forget[:2] == (person, what):
        st.success("Forget request completed.")
        show_result(saved_forget[2])
