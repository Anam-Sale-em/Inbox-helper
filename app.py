"""Inbox Helper review UI. Run with: streamlit run app.py

UI only: pipeline logic lives in inbox/ and main.py. Nothing is ever sent from here;
approved replies open as Gmail drafts for a human to send.
"""

import html
import json
import os
import traceback
from datetime import datetime
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote, urlencode

import streamlit as st
from pydantic import ValidationError

from inbox import calendar_slots, draft, gmail_source, llm, pipeline
import re

from inbox.checks import FIXED_FLAGS, run_checks, unlisted_times
from inbox.context import load_context
from inbox.reschedule import SLOTS_TOKEN, approval_blocker, fallback_decline, unwrap_filled_blanks
from difflib import SequenceMatcher

from inbox.dedupe import NEAR_DUPLICATE_RATIO, dedupe, normalize
from inbox.intake import load_pasted, load_upload
from inbox.feedback import feedback_problem
from inbox.google_auth import GoogleSetupError
from inbox.llm import MockResponseMissing
from inbox.names import first_name
from inbox.schema import Message
from main import (load_report, split_empty, split_no_reply, group_by_intent, make_run_dir,
                  other_names, save, check_drafts, save_pipeline_output)

DATA_DIR = Path("data")
SUBJECT = "Re: your application"
MAX_WORDS = 80
REVISION_NOTE_AFTER = 3
OFFER, DECLINE = "Yes, offer new slots", "No, keep the original time"  # soft note only; revising is never blocked

INTENT_LABELS = {
    "needs_human": "Needs human",
    "reschedule_interview": "Reschedule",
    "application_status": "Application status",
    "salary_question": "Salary",
    "offer_response": "Offer response",
    "withdrawal": "Withdrawal",
    "document_submission": "Documents",
    "other": "Other",
}
FLAG_HELP = {
    "unlisted_time": "The reply mentions a specific time. The AI shouldn't add times on its own, so check it's one you can offer.",
    "salary": "A salary figure appears in the reply. Approve only if you meant to share it.",
    "date_commitment": "A day/date appears close to a word like 'confirm'. Check the reply doesn't promise a date you haven't agreed.",
    "offer_or_outcome": "The reply sounds like an offer or a result. Check that's been decided.",
    "other_candidate": "Another candidate's name appears in the reply. Remove it before sending.",
}
FLAG_LABELS = {
    "unlisted_time": "Specific time mentioned",
    "salary": "Salary mentioned",
    "date_commitment": "Date commitment",
    "offer_or_outcome": "Offer/outcome",
    "other_candidate": "Other candidate named",
}
STATUS_CLASS = {"Pending": "pending", "Approved": "approved", "On hold": "hold"}

SHIELD = ('<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
          'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
          '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/></svg>')
HEADER = (
    '<div class="ih-top"><div>'
    '<div class="ih-title">Inbox Helper</div>'
    '<div class="ih-sub">Candidate messages sorted and drafted by AI. You approve every reply.</div>'
    f'</div><div class="ih-safe">{SHIELD}Nothing is sent automatically</div></div>'
)

CSS = """
<style>
/* Palette: navy = trust/primary, green = approved, amber = on hold, red = needs a look. */
:root {--ih-ink:#172B3D; --ih-muted:#5B6B7C; --ih-line:#D6DEE7; --ih-page:#F4F6F9; --ih-navy:#1D4F86;
       --ih-green:#24734F; --ih-amber:#9A6206; --ih-red:#B42318;}

.ih-top {display:flex; justify-content:space-between; align-items:flex-end; gap:24px; flex-wrap:wrap;
         padding:4px 0 18px; margin-bottom:8px; border-bottom:1px solid var(--ih-line);}
.ih-title {font-size:1.75rem; font-weight:700; letter-spacing:-.01em; color:var(--ih-ink); line-height:1.2;}
.ih-sub {color:var(--ih-muted); margin-top:4px;}
.ih-safe {display:flex; align-items:center; gap:8px; font-size:.85rem; color:var(--ih-green);
          background:#EAF4EE; border:1px solid #BFDCCB; padding:6px 12px; border-radius:6px;}
.ih-safe svg {flex:none;}

/* White surfaces for the things you act on. */
.st-key-metrics, [class*="st-key-card_"] {background:#FFFFFF;}
[class*="st-key-card_"] {border-left-width:4px !important; border-left-style:solid !important;}
[class*="st-key-card_pending"]  {border-left-color:#9AA8B6 !important;}
[class*="st-key-card_approved"] {border-left-color:var(--ih-green) !important;}
[class*="st-key-card_hold"]     {border-left-color:var(--ih-amber) !important;}

.ih-head {display:flex; justify-content:space-between; align-items:flex-start; gap:12px;}
.ih-name {font-weight:600; font-size:1.05rem; color:var(--ih-ink);}
.ih-meta {color:var(--ih-muted); font-size:.85rem; margin-top:2px; display:flex; gap:14px; flex-wrap:wrap;}
.ih-chip {font-size:.78rem; font-weight:600; padding:3px 10px; border-radius:999px; white-space:nowrap;}
.ih-chip.pending  {color:#44546A; background:#EEF2F6;}
.ih-chip.approved {color:#FFFFFF; background:var(--ih-green);}
.ih-chip.hold     {color:var(--ih-amber); background:#FDF3E1;}
.ih-msg {white-space:pre-wrap; padding:12px 16px; margin:12px 0 8px; border-radius:6px; line-height:1.55;
         color:var(--ih-ink); background:var(--ih-page); border-left:3px solid #9FB3C8;
         max-height:240px; overflow-y:auto;}
.ih-flag {display:inline-block; font-size:.78rem; font-weight:600; padding:3px 9px; margin:2px 6px 2px 0;
          border-radius:4px; color:var(--ih-red); background:#FEF3F2; border:1px solid #FECDCA; cursor:help;}
.ih-note {display:inline-block; font-size:.78rem; padding:3px 9px; margin:2px 6px 2px 0; border-radius:4px;
          color:var(--ih-muted); background:var(--ih-page); border:1px solid var(--ih-line);}
.ih-rev {display:inline-block; font-size:.78rem; font-weight:600; padding:3px 9px; margin:2px 6px 2px 0;
         border-radius:4px; color:var(--ih-navy); background:#EAF1F9; border:1px solid #C5D7EC;}
</style>
"""


def main() -> None:
    st.set_page_config(page_title="Inbox Helper", page_icon=":material/inbox:", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(HEADER, unsafe_allow_html=True)

    state = st.session_state
    state.setdefault("decisions", {})
    state.setdefault("approved_at", {})      # msg_id -> ISO time it was last approved
    state.setdefault("run_no", 0)
    state.setdefault("revisions", {})        # msg_id -> [{"comment", "previous"}]
    state.setdefault("feedback_open", set())  # msg_ids with the "Request changes" box open
    state.setdefault("pending_replies", {})  # msg_id -> revised text to load into the text area
    state.setdefault("templates", {})        # reschedule msg_id -> {"offer": ..., "decline": ...}
    state.setdefault("rendered", {})         # msg_id -> the reply as the tool last built it
    state.setdefault("kind_text", {})        # reschedule msg_id -> {"offer": text, "decline": text}

    sidebar()
    result = state.get("result")
    if result is None:
        empty_state()
        return

    ensure_run_saved(result)  # once drafting is done: same files as a terminal run
    for message in result["unique"]:
        if message.id in result["drafts"]:  # still-drafting messages get their card next wave
            ensure_card_state(message, result)
    refresh_flags(result)  # flags always match what's in the reply boxes right now

    drafting_banner(result)
    metrics(result)
    intent_tabs(result)
    st.divider()
    outbox_section(result)
    save_decisions(result)  # outbox.json and on_hold.json, kept current as you review

    # Everything above is already on screen, so finished cards can be reviewed while the
    # next wave is sorted and drafted. Then rerun to show the new cards.
    if not result["progress"].done:
        advance(result)
        st.rerun()


def drafting_banner(result: dict) -> None:
    if st.session_state.get("draft_error"):
        st.error(st.session_state.draft_error)
    if st.session_state.get("save_error"):
        st.warning(st.session_state.save_error)
    progress = result["progress"]
    if progress.done:
        return
    total, ready = len(progress.unique), len(progress.drafts)
    st.progress(ready / total if total else 1.0,
                text=f"Sorting and drafting: {ready} of {total} replies ready. You can start reviewing these now.")


def advance(result: dict) -> None:
    """One more wave of sorting and drafting (see inbox/pipeline.py). Never raises: on failure the
    rest become needs_human with an empty draft (written by hand or put on hold) and an error shows."""
    progress = result["progress"]
    try:
        with provider_env(result["mock"]):
            pipeline.step(progress)
    except Exception as exc:
        traceback.print_exc()
        st.session_state.draft_error = friendly_error(exc, result["source"], result["mock"])
        pipeline.give_up(progress)
    result["groups"] = group_by_intent(progress.sorted_messages, progress.classifications)
    result["usage"] = llm.get_usage()


def ensure_run_saved(result: dict) -> None:
    """Save the run like main.py does as soon as sorting and drafting are done, so
    eval/day1_report.py and eval/judge.py work on review-screen runs too. Tried once per run."""
    if "run_dir" in result or not result["progress"].done:
        return
    result["run_dir"] = None  # a failure shows a warning instead of retrying on every rerun
    gmail_ids = result.get("gmail_ids") or {}
    try:
        run_dir = make_run_dir(suffix="_gmail" if gmail_ids else "")
        save_pipeline_output(
            run_dir, messages=result["messages"], rejected=result["rejected"],
            renumbered=list(result["renumbered"].values()), empty=result["empty"],
            no_reply=result["no_reply"], duplicates=result["duplicates"],
            classifications=result["classifications"], drafts=result["drafts"],
            flags=check_drafts(result["unique"], result["drafts"], []), ai_calls=result["usage"])
        result["run_dir"] = run_dir
        if gmail_ids:
            gmail_source.mark_seen(gmail_ids.values())  # only now, so a failed run skips nobody
    except OSError as exc:
        st.session_state.save_error = f"Couldn't save this run: {exc.strerror or exc}."


def save_decisions(result: dict) -> None:
    """Approved and held replies go into the run folder whenever they change. No button."""
    run_dir = result.get("run_dir")
    if not run_dir:
        return
    state = st.session_state
    held = [{"id": m.id, "to": m.from_email, "draft": state.get(text_key(m.id), result["drafts"][m.id].reply)}
            for m in result["unique"] if state.decisions.get(m.id) == "On hold"]
    files = {"outbox": outbox_items(result), "on_hold": held}
    snapshot = (str(run_dir), json.dumps(files, sort_keys=True, ensure_ascii=False))
    if state.get("saved_snapshot") == snapshot:
        return
    try:
        save(run_dir, **files)
        state.saved_snapshot = snapshot
    except OSError as exc:
        state.save_error = f"Couldn't save your decisions: {exc.strerror or exc}."


# --- sidebar ---------------------------------------------------------------------


def sidebar() -> None:
    state = st.session_state
    with st.sidebar:
        st.subheader("New run")
        path, load = choose_source()
        mock = st.toggle("Mock mode", value=True, key="mock",
                         help="Replay recorded model responses instead of calling Gemini/Groq.")
        if mock and state.get("source") != SOURCES[0]:
            st.caption("Mock mode only replays saved runs. Turn it off to draft replies for your own messages.")
        st.toggle("Suggest times from Google Calendar", key="use_calendar",
                  help="Reschedule cards show free slots from your calendar (free/busy only). "
                       "You pick; the AI never sees the calendar. Needs the Google sign-in (README).")

        if st.button("Run pipeline", type="primary", width="stretch", disabled=load is None):
            with st.spinner("Removing duplicates and sorting messages…"):
                try:
                    state.result = run_pipeline(load, path, mock)
                    state.decisions = {}
                    state.approved_at = {}
                    state.revisions = {}
                    state.feedback_open = set()
                    state.pending_replies = {}
                    state.templates = {}
                    state.rendered = {}
                    state.kind_text = {}
                    state.run_no += 1
                    state.pop("save_error", None)
                    state.pop("draft_error", None)
                except Exception as exc:
                    traceback.print_exc()  # full detail in the terminal, friendly text in the UI
                    st.error(friendly_error(exc, path, mock))

        if state.get("result"):
            st.divider()
            st.subheader("Progress")
            counts = decision_counts(state.result)
            total = sum(counts.values())
            st.progress(counts["Approved"] / total if total else 0.0,
                        text=f"{counts['Approved']} of {total} approved")
            st.markdown(f"{counts['On hold']} on hold, {counts['Pending']} waiting for review")
            st.caption(f"Source: {state.result['source']}")
            usage_panel(state.result)


SOURCES = ("Sample file", "Upload a file", "Paste messages", "Gmail")
PASTE_EXAMPLE = ("Riya Sharma <riya@example.com>: Can we move my interview to Friday?\n"
                 "arjun@example.com: Any update on my application?")


def choose_source():
    """Where the messages come from. Returns (label, loader) or (label, None) if nothing to load yet."""
    source = st.radio("Messages from", SOURCES, key="source")
    if source == SOURCES[0]:
        files = sorted(str(p) for p in DATA_DIR.glob("*.json") if not p.name.endswith("expected.json"))
        if not files:
            st.warning(f"No message files found. Add a .json file to `{DATA_DIR}/` or upload one.")
            return "", None
        default = files.index(str(DATA_DIR / "messages.json")) if str(DATA_DIR / "messages.json") in files else 0
        path = st.selectbox("Input file", files, index=default)
        return path, lambda: load_report(path)
    if source == SOURCES[1]:
        upload = st.file_uploader("JSON or CSV", type=["json", "csv"])
        st.caption("CSV columns: name, email, message. Date and id are optional.")
        if upload is None:
            return "", None
        name, raw = upload.name, upload.getvalue()
        return name, lambda: load_upload(name, raw)
    if source == SOURCES[3]:
        return gmail_source_controls()
    text = st.text_area("One message per line", height=140, placeholder=PASTE_EXAMPLE)
    st.caption("Format: Name <email>: message")
    if not text.strip():
        return "", None
    return "pasted messages", lambda: load_pasted(text)


def gmail_source_controls():
    """Read candidate emails from Gmail (read-only), like n8n's Gmail trigger: a search picks the
    emails, and ones already loaded are skipped."""
    query = st.text_input("Gmail search", value=gmail_source.default_query(), key="gmail_query",
                          help="Same as the Gmail search box. A Gmail filter that labels candidate "
                               "mail makes this the trigger. Use test emails: drafts go to the AI.")
    only_new = st.toggle("Skip emails already loaded", value=True, key="gmail_only_new")
    if st.toggle("Check for new emails every minute", key="gmail_watch"):
        gmail_watch(query)
    st.caption("Read-only: nothing is sent, marked or deleted in Gmail.")
    return f"Gmail ({query})", lambda: gmail_source.load_gmail(query, only_new=only_new)


@st.fragment(run_every="60s")
def gmail_watch(query: str) -> None:
    """Every minute, count new matching emails. It never starts a run by itself, so a review in
    progress is never replaced."""
    checked = datetime.now().strftime("%H:%M")
    on_screen = ((st.session_state.get("result") or {}).get("gmail_ids") or {}).values()
    try:
        waiting = gmail_source.count_new(query, exclude=on_screen)
    except Exception as exc:  # not signed in, offline, API off: say so, keep the screen working
        st.caption(f"Couldn't check Gmail ({checked}): {exc}")
        return
    if waiting:
        st.info(f"{waiting} new email{'s' if waiting != 1 else ''} (checked {checked}). "
                "Press Run pipeline to draft replies.")
    else:
        st.caption(f"No new emails (checked {checked}).")


def usage_panel(result: dict) -> None:
    u = llm.usage_summary(result.get("usage", []))
    st.subheader("AI usage")
    if result["mock"]:
        st.caption(f"{u['calls']} replayed calls (mock mode: no tokens spent)")
        return
    st.markdown(f"{u['calls']} calls, slowest {u['slowest_call_seconds']}s")
    st.caption(f"Tokens: {u['input_tokens']:,} in, {u['output_tokens']:,} out, "
               f"{u['thinking_tokens']:,} thinking. Batches of {draft.BATCH_SIZE}, "
               f"{draft.PARALLEL_BATCHES} at a time.")


@contextmanager
def provider_env(mock: bool):
    """Mock applies to one call site only; the .env/shell value comes back afterwards."""
    previous = os.environ.get("LLM_PROVIDER")
    if mock:
        os.environ["LLM_PROVIDER"] = "mock"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("LLM_PROVIDER", None)
        else:
            os.environ["LLM_PROVIDER"] = previous


def run_pipeline(load, path: str, mock: bool) -> dict:
    with provider_env(mock):
        loaded = load()
        messages = loaded.messages
        if not messages and not loaded.rejected:
            raise ValueError("empty input")
        usable, empty = split_empty(messages)  # no text = nothing to sort or answer, no AI call
        usable, no_reply = split_no_reply(usable)  # auto-replies and bounces: nobody to answer
        llm.reset_usage()
        # Duplicates are removed from the whole file first; batching only starts after this.
        unique, duplicates = dedupe(usable)
        context = load_context()  # status/salary facts from config, if present
        # Sort and draft in waves: the first cards show after one wave, whatever the inbox size.
        progress = pipeline.start(unique, context)
        pipeline.step(progress)
    return {
        "source": path,
        "unique": unique,
        "messages": messages,
        "duplicates": duplicates,
        "empty": empty,
        "no_reply": no_reply,
        "rejected": loaded.rejected,
        "renumbered": {r["new_id"]: r for r in loaded.renumbered},
        "no_timezone": loaded.no_timezone,
        "classifications": progress.classifications,  # same dicts the pipeline keeps filling
        "drafts": progress.drafts,
        "progress": progress,
        "flags": {},  # filled by refresh_flags as cards appear
        "mock": mock,
        "usage": llm.get_usage(),
        "context": context,
        "groups": group_by_intent(progress.sorted_messages, progress.classifications),
        "gmail_ids": loaded.source_ids,  # marked as loaded only once the run is saved
    }


def friendly_error(exc: Exception, path: str, mock: bool) -> str:
    if isinstance(exc, FileNotFoundError):
        return f"Couldn't find `{path}`. Pick another file or check the path."
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "file"
        return (f"`{path}` isn't a valid message list ({exc.error_count()} problem(s)). "
                f"First one: **{where}**: {first['msg']}. Each message needs id, from_name, "
                "from_email, received_at and text.")
    if isinstance(exc, json.JSONDecodeError):
        return f"`{path}` isn't valid JSON (line {exc.lineno}): {exc.msg}. Fix the file and run again."
    if isinstance(exc, ValueError) and str(exc) == "not a list":
        return f"`{path}` should be a list of messages: [ {{...}}, {{...}} ]."
    if isinstance(exc, MockResponseMissing):
        return ("Mock mode has no recorded response for this input. Turn mock mode off and run once "
                "with `RECORD=1` set, or pick a file you've already recorded.")
    if isinstance(exc, GoogleSetupError):
        return str(exc)
    if isinstance(exc, ValueError) and str(exc) == "empty input" and path.startswith("Gmail"):
        return ("No new emails match the search. Send a test email under the label, or turn off "
                "\"Skip emails already loaded\".")
    if type(exc).__name__ == "HttpError":
        return f"Google returned an error: {exc}"
    if isinstance(exc, ValueError) and str(exc) == "empty input":
        return f"`{path}` has no messages in it."
    if isinstance(exc, ValueError) and "LLM_PROVIDER" in str(exc):
        return "`LLM_PROVIDER` in `.env` must be gemini, groq or mock. Fix it or turn on mock mode."
    return f"Something went wrong while running the pipeline: {exc}. Details are in the terminal."


# --- main area ---------------------------------------------------------------------


def empty_state() -> None:
    with st.container(border=True):
        st.subheader("No run yet")
        st.markdown(
            "1. Choose an input file in the sidebar\n"
            "2. Keep **Mock mode** on to replay recorded responses, or turn it off to use the live model\n"
            "3. Press **Run pipeline**, then approve each draft, request changes, or put it on hold\n\n"
            "Nothing is sent from this tool. Approved replies open in Gmail for you to send."
        )


def metrics(result: dict) -> None:
    intents = [c.intent for c in result["classifications"].values()]
    with st.container(border=True, key="metrics"):
        metric_row(result, intents)
    rejected_panel(result)
    duplicates_panel(result)
    empty_panel(result)
    no_reply_panel(result)


def no_reply_panel(result: dict) -> None:
    skipped = result.get("no_reply", {})
    if not skipped:
        return
    by_id = {m.id: m for m in result["messages"]}
    with st.expander(f"Show {len(skipped)} message(s) that need no reply"):
        st.caption("Auto-replies and bounces. Nobody is waiting for an answer, so no AI call was made.")
        for msg_id, reason in skipped.items():
            m = by_id[msg_id]
            st.markdown(f"**#{m.id} · {html.escape(m.from_name) or '(no name)'}** · "
                        f"`{html.escape(m.from_email)}` · {html.escape(reason)}"
                        f'<div class="ih-msg">{html.escape(m.text)}</div>', unsafe_allow_html=True)


def rejected_panel(result: dict) -> None:
    """Records that couldn't be read: shown as proof, never drafted. The rest of the file still runs."""
    rejected = result.get("rejected", [])
    if not rejected:
        return
    with st.expander(f"Show {len(rejected)} record(s) that couldn't be read", expanded=True):
        st.caption("These were skipped, so no reply was drafted. Every other message in the file was "
                   "processed as normal. Fix the record in the file to include it next run.")
        for r in rejected:
            with st.container(border=True):
                where = r.get("label") or f"Record {r['position']} in the file"
                st.markdown(f"**{html.escape(where)}** · {html.escape(r['reason'])}")
                st.code(json.dumps(r["record"], indent=2, ensure_ascii=False), language="json")


def empty_panel(result: dict) -> None:
    empty = result.get("empty", [])
    if not empty:
        return
    with st.expander(f"Show {len(empty)} empty message(s) skipped"):
        st.caption("These arrived with no text, so there was nothing to sort or reply to. "
                   "No AI call was made for them.")
        for m in empty:
            st.markdown(f"#{m.id} · **{html.escape(m.from_name) or '(no name)'}** · "
                        f"`{html.escape(m.from_email)}` · {m.received_at.strftime('%d %b %Y, %H:%M')}")


def metric_row(result: dict, intents: list[str]) -> None:
    cols = st.columns(6)
    cols[0].metric("Messages", len(result["messages"]) + len(result.get("rejected", [])),
                   help="Total messages in the file. Copies, empty messages, auto-replies and broken records "
                        "don't get a card; they're listed below with the reason.")
    cols[1].metric("Duplicates removed", len(result["duplicates"]))
    cols[2].metric("Categories", len(result["groups"]))
    cols[3].metric("Flagged drafts", len(result["flags"]),
                   help="Drafts with something worth a second look (a date, salary, offer wording or "
                        "another candidate's name). Open the card to see what matched.")
    cols[4].metric("Needs human", intents.count("needs_human"))
    cols[5].metric("On hold", decision_counts(result)["On hold"])


def duplicates_panel(result: dict) -> None:
    if result["duplicates"]:
        with st.expander(f"Show {len(result['duplicates'])} removed duplicate(s)"):
            by_id = {m.id: m for m in result["messages"]}
            for dup, original in result["duplicates"].items():
                duplicate_evidence(by_id[original], by_id[dup])


def duplicate_evidence(original: Message, copy: Message) -> None:
    """Show both messages side by side, with the same test dedupe.py used to match them."""
    a, b = normalize(original.text), normalize(copy.text)
    if a == b:
        verdict = "Exact match after normalising (case, punctuation and spaces ignored)"
    else:
        ratio = SequenceMatcher(None, a, b, autojunk=False).ratio()
        verdict = (f"Near match: {ratio:.0%} similar "
                   f"(threshold {NEAR_DUPLICATE_RATIO:.0%})")

    with st.container(border=True):
        st.markdown(f"**#{copy.id} removed as a copy of #{original.id}** · same sender "
                    f"`{html.escape(copy.from_email)}`  \n{verdict}")
        kept, removed = st.columns(2)
        for col, label, m in ((kept, "Kept", original), (removed, "Removed", copy)):
            col.markdown(
                f"<small><b>{label}</b> · #{m.id} · {m.received_at.strftime('%d %b %Y, %H:%M')}</small>"
                f'<div class="ih-msg">{html.escape(m.text)}</div>',
                unsafe_allow_html=True,
            )


def ordered_groups(result: dict) -> list[tuple[str, list[Message]]]:
    groups = result["groups"]
    first = [("needs_human", groups["needs_human"])] if "needs_human" in groups else []
    return first + [(k, v) for k, v in groups.items() if k != "needs_human"]


def intent_tabs(result: dict) -> None:
    groups = ordered_groups(result)
    if not groups:
        st.info("Nothing to review: every message was a duplicate, empty, or couldn't be read. See above for why.")
        return

    # Held messages leave their category tab and wait in "On hold" (last, so other tabs don't shift).
    decisions = st.session_state.decisions
    held = [m for _, msgs in groups for m in msgs if decisions.get(m.id) == "On hold"]
    active = [(intent, [m for m in msgs if decisions.get(m.id) != "On hold"]) for intent, msgs in groups]
    active = [(intent, msgs) for intent, msgs in active if msgs]

    labels = [f"{INTENT_LABELS.get(intent, intent)} ({len(msgs)})" for intent, msgs in active]
    labels.append(f"On hold ({len(held)})")
    tabs = st.tabs(labels)

    for tab, (_, messages) in zip(tabs, active):
        with tab:
            for message in messages:
                if message.id in result["drafts"]:
                    card(message, result)
                else:
                    drafting_card(message)

    with tabs[-1]:
        if not held:
            st.caption("Nothing on hold. Use Hold on any message that needs a human to handle it later.")
        for message in held:
            card(message, result, show_intent=True)


def drafting_card(message: Message) -> None:
    with st.container(border=True, key=f"card_pending_wait_{st.session_state.run_no}_{message.id}"):
        st.markdown(f'<div class="ih-name">{html.escape(message.from_name) or "(no name)"}</div>'
                    '<div class="ih-meta"><span>Drafting reply…</span></div>', unsafe_allow_html=True)


def card(message: Message, result: dict, show_intent: bool = False) -> None:
    state = st.session_state
    run_no = state.run_no
    original = result["drafts"][message.id].reply
    status = state.decisions.get(message.id, "Pending")
    history = state.revisions.get(message.id, [])

    key = text_key(message.id)  # seeded by ensure_card_state before anything renders

    # The key puts the status in the card's CSS class, which colours its left edge.
    with st.container(border=True, key=f"card_{STATUS_CLASS[status]}_{run_no}_{message.id}"):
        st.markdown(card_header(message, status), unsafe_allow_html=True)

        flags = result["flags"].get(message.id, [])
        labels = "".join(flag_badge(f, reply_for_flags(message.id, result), result, message)
                         for f in flags)
        if show_intent:
            intent = result["classifications"][message.id].intent
            labels = f'<span class="ih-note">{html.escape(INTENT_LABELS.get(intent, intent))}</span>' + labels
        moved = result.get("renumbered", {}).get(message.id)
        if moved:
            labels += (f'<span class="ih-note" title="Id {moved["old_id"]} was already used by '
                       f'{html.escape(moved["kept_by"], quote=True)}. The reply still goes to this sender.">'
                       f'Id changed from {moved["old_id"]} (already used)</span>')
        if message.id in result.get("no_timezone", []):
            labels += '<span class="ih-note">No timezone in the file, read as IST</span>'
        if history:
            labels += f'<span class="ih-rev">Revision {len(history)}</span>'
        if not original.strip() and not history:
            labels += '<span class="ih-note">No draft generated — write one or put it on hold</span>'
        if labels:
            st.markdown(labels, unsafe_allow_html=True)
        if flags:
            st.caption("⚠ Flags are warnings for you to check, not errors. Hover a flag to see why.")

        if is_reschedule(message.id, result):
            reschedule_controls(message, result)

        reply = st.text_area("Draft reply", key=key, placeholder="Write a reply…", height=120,
                             on_change=reply_edited, args=(message.id,))
        offering = is_reschedule(message.id, result) and current_kind(message.id) == "offer"
        blocker = approval_blocker(reply, offering)
        if blocker:
            st.caption(blocker + " Approve turns on once it's done.")
        words = len(reply.split())
        note = f"{words} words"
        if is_edited(message.id, reply, original):
            note += " · edited"
        if words > MAX_WORDS:
            note += f" · over the {MAX_WORDS}-word limit"
        st.caption(note)

        if history:
            revision_history(history)

        approve, changes, hold, send, _ = st.columns([1, 1.3, 1, 1.3, 1.5])
        approve.button("Approve", key=f"approve_{run_no}_{message.id}", type="primary",
                       disabled=status == "Approved" or not reply.strip() or bool(blocker),
                       on_click=decide, args=(message.id, "Approved"), width="stretch")
        changes.button("Request changes", key=f"changes_{run_no}_{message.id}",
                       on_click=toggle_feedback, args=(message.id,), width="stretch")
        hold.button("Hold", key=f"hold_{run_no}_{message.id}",
                    disabled=status == "On hold",
                    on_click=decide, args=(message.id, "On hold"), width="stretch")
        if status == "Approved" and reply.strip():
            send.link_button("Open in Gmail", gmail_url(message.from_email, reply.strip()),
                             width="stretch")

        if len(history) >= REVISION_NOTE_AFTER:
            st.caption("Several revisions so far. You can also edit the reply directly.")
        if message.id in state.feedback_open:
            feedback_box(message, result, reply, len(history))


def reschedule_controls(message: Message, result: dict) -> None:
    """Pick the version. Both were written in the first AI call, so switching never calls
    the AI again. Slots are typed straight into the reply."""
    run_no, msg_id = st.session_state.run_no, message.id
    st.radio("Can the interview be moved?", [OFFER, DECLINE], horizontal=True,
                      key=f"choice_{run_no}_{msg_id}", on_change=rebuild_reply, args=(msg_id,))
    if st.session_state.get("use_calendar") and current_kind(msg_id) == "offer":
        calendar_picker(message, result)


def calendar_picker(message: Message, result: dict) -> None:
    """Free slots from the recruiter's calendar, the day the candidate asked for first. The
    recruiter picks; the picks fill the blank. The AI never sees any of this."""
    state, run_no = st.session_state, st.session_state.run_no
    if SLOTS_TOKEN not in state.get(text_key(message.id), ""):
        return  # blank already filled or typed over
    slots, rules = calendar_free_slots(result)
    if slots is None:
        return
    options = [calendar_slots.label(s) for s in calendar_slots.suggest(slots, message.text, rules)]
    if not options:
        st.caption("No free slots in your calendar over the next working days.")
        return
    picked = st.pills("Free in your calendar", options, selection_mode="multi",
                      key=f"slots_{run_no}_{message.id}")
    st.button("Put these times in the reply", key=f"fill_{run_no}_{message.id}", disabled=not picked,
              on_click=fill_slots, args=(message.id,))


def calendar_free_slots(result: dict):
    """One free/busy read per run, shared by every reschedule card. (slots or None, rules)"""
    if "calendar" not in result:
        rules = calendar_slots.SlotRules.from_env()
        try:
            result["calendar"] = {"slots": calendar_slots.get_free_slots(rules=rules), "rules": rules, "error": ""}
        except Exception as exc:  # not signed in, wrong calendar id, offline: the card still works
            result["calendar"] = {"slots": None, "rules": rules, "error": str(exc)}
    cal = result["calendar"]
    if cal["error"]:
        st.caption(f"Calendar not available: {cal['error']}")
    return cal["slots"], cal["rules"]


def fill_slots(msg_id: int) -> None:
    """Picked times replace the blank. Picking times isn't an edit, same as picking a version."""
    state = st.session_state
    key = text_key(msg_id)
    text = state.get(key, "")
    filled = calendar_slots.fill_blank(text, list(state.get(f"slots_{state.run_no}_{msg_id}") or []))
    if filled == text:
        return
    if text == state.rendered.get(msg_id):
        state.rendered[msg_id] = filled
    state[key] = filled
    reopen_if_approved(msg_id)


def revision_history(history: list[dict]) -> None:
    with st.expander(f"Revision history ({len(history)})"):
        for number, rev in enumerate(history, 1):
            previous = html.escape(rev["previous"]) or "<i>(empty)</i>"
            st.markdown(
                f"**Revision {number}**  \nComment: {html.escape(rev['comment'])}  \n"
                f"<small>Draft before this change:</small>"
                f'<div class="ih-msg">{previous}</div>',
                unsafe_allow_html=True,
            )


def feedback_box(message: Message, result: dict, reply: str, revision_no: int) -> None:
    state = st.session_state
    suffix = f"{state.run_no}_{message.id}_{revision_no}"  # new key per revision = empty box
    comment = st.text_area("What should change?", key=f"feedback_{suffix}", height=80,
                           placeholder="e.g. Make it shorter and ask for two time slots")
    st.caption("Tip: a few words on what to change works best, e.g. \"Make it shorter\" "
               "or \"Offer 1pm or 3pm\".")
    revise, cancel, _ = st.columns([1.3, 1, 4])
    cancel.button("Cancel", key=f"cancel_{suffix}", on_click=toggle_feedback,
                  args=(message.id,), width="stretch")
    if not revise.button("Revise draft", key=f"revise_{suffix}", type="primary", width="stretch"):
        return
    problem = feedback_problem(comment)
    if problem:
        st.warning(problem)  # nothing is sent to the AI until the comment is usable
        return

    # On an untouched reschedule card, revise the version itself (with the blank) so the
    # chosen slots are filled back in afterwards. A hand-edited reply is revised as written.
    use_template = is_reschedule(message.id, result) and reply == state.rendered.get(message.id)
    kind = current_kind(message.id)
    base = state.templates[message.id][kind] if use_template else reply

    with st.spinner("Revising draft…"), provider_env(state.get("mock", True)):
        intent = result["classifications"][message.id].intent
        revised = draft.revise_reply(message, intent, base, comment.strip(),
                                     result["context"]).reply.strip()

    if not revised or revised == base.strip():
        if state.get("mock", True):
            st.info("The draft stayed the same because Mock mode only replays saved responses. "
                    "Turn Mock mode off in the sidebar to revise with the live model.")
        else:
            st.info("The draft stayed the same. The comment may not have been clear enough to act on. "
                    "You could try rephrasing it, or edit the reply directly above.")
        return

    state.revisions.setdefault(message.id, []).append({"comment": comment.strip(), "previous": reply})
    if use_template:
        state.templates[message.id][kind] = revised
        revised = render_reply(message.id, result)
    state.pending_replies[message.id] = revised
    state.decisions.pop(message.id, None)  # back to Pending
    state.approved_at.pop(message.id, None)
    state.feedback_open.discard(message.id)
    st.rerun()


def card_header(message: Message, status: str) -> str:
    received = message.received_at.strftime("%d %b %Y, %H:%M")
    if message.received_at.tzinfo:
        received += f" {message.received_at.strftime('%Z')}"
    return (
        '<div class="ih-head"><div>'
        f'<div class="ih-name">{html.escape(message.from_name)}</div>'
        f'<div class="ih-meta"><span>{html.escape(message.from_email)}</span><span>{received}</span></div>'
        f'</div><span class="ih-chip {STATUS_CLASS[status]}">{status}</span></div>'
        f'<div class="ih-msg">{html.escape(message.text)}</div>'
    )


def outbox_section(result: dict) -> None:
    st.subheader("Outbox")
    if result.get("run_dir"):
        st.caption(f"This run is saved to `{result['run_dir']}`. Approved replies go to outbox.json "
                   "and held ones to on_hold.json as you review.")
    counts = decision_counts(result)
    waiting = counts["Pending"] + counts["On hold"]
    if waiting:
        st.warning(f"{waiting} message{'s' if waiting != 1 else ''} still need{'' if waiting != 1 else 's'} "
                   "a reply.")
    items = outbox_items(result)
    if not items:
        st.info("No approved replies yet. Approve drafts above and they'll show up here.")
        return

    st.caption("To change a reply, edit it on its card above and approve again.")
    # st.table is static: no checkboxes, sorting or editing.
    st.table(
        [{"To": f'{i["name"]} <{i["to"]}>', "Category": INTENT_LABELS.get(i["intent"], i["intent"]),
          "Reply": i["reply"], "Edited": "Yes" if i["edited"] else "No",
          "Approved": i["approved_at"][11:16]} for i in items],
        hide_index=True,
    )
    st.caption("Nothing has been sent. Use “Open in Gmail” on each card to send it yourself.")

    download, _ = st.columns([1, 4])
    download.download_button("Download outbox.json", data=json.dumps(items, indent=2, ensure_ascii=False),
                             file_name="outbox.json", mime="application/json", width="stretch")


# --- reply building ----------------------------------------------------------------------


def is_reschedule(msg_id: int, result: dict) -> bool:
    return result["classifications"][msg_id].intent == "reschedule_interview"


def current_kind(msg_id: int) -> str:
    choice = st.session_state.get(f"choice_{st.session_state.run_no}_{msg_id}", OFFER)
    return "offer" if choice == OFFER else "decline"


def render_reply(msg_id: int, result: dict) -> str:
    """The reply as the tool builds it: the draft, or the chosen reschedule version."""
    if not is_reschedule(msg_id, result):
        return result["drafts"][msg_id].reply
    return st.session_state.templates[msg_id][current_kind(msg_id)]


def ensure_card_state(message: Message, result: dict) -> None:
    """Seed the reply box once per run, and load any fresh revision, before anything renders."""
    state, msg_id = st.session_state, message.id
    if is_reschedule(msg_id, result) and msg_id not in state.templates:
        draft_ = result["drafts"][msg_id]
        first = first_name(message.from_name)
        state.templates[msg_id] = {"offer": draft_.reply,
                                   "decline": draft_.alt_reply or fallback_decline(first)}
    key = text_key(msg_id)
    if msg_id in state.pending_replies:
        state[key] = state.rendered[msg_id] = state.pending_replies.pop(msg_id)
    elif key not in state:
        state[key] = state.rendered[msg_id] = render_reply(msg_id, result)


def rebuild_reply(msg_id: int) -> None:
    """Version switched: keep what was typed in the old one, show the new one. No AI call."""
    state = st.session_state
    texts = state.kind_text.setdefault(msg_id, {})
    new_kind = current_kind(msg_id)
    old_kind = "decline" if new_kind == "offer" else "offer"
    texts[old_kind] = state.get(text_key(msg_id), "")
    state.rendered[msg_id] = render_reply(msg_id, state.result)
    state[text_key(msg_id)] = texts.get(new_kind) or state.rendered[msg_id]
    reopen_if_approved(msg_id)


def allowed_times(msg_id: int, result: dict) -> list[str] | None:
    """Times a reply may mention. A reschedule offer: whatever the approver typed (no check).
    Every other reply: none, since the AI is told never to write a time."""
    if is_reschedule(msg_id, result) and current_kind(msg_id) == "offer":
        return None
    return []


def refresh_flags(result: dict) -> None:
    for message in result["unique"]:
        if message.id not in result["drafts"]:  # not sorted/drafted yet: nothing to check
            continue
        reply = st.session_state.get(text_key(message.id), "")
        found = run_checks(reply, other_names(result["unique"], message),
                           allowed_times(message.id, result)) if reply.strip() else []
        if found:
            result["flags"][message.id] = found
        else:
            result["flags"].pop(message.id, None)


# --- flag evidence ---------------------------------------------------------------------


def reply_for_flags(msg_id: int, result: dict) -> str:
    """The reply the flags were computed on: the current text area value if any, else the draft."""
    return st.session_state.get(text_key(msg_id), result["drafts"][msg_id].reply)


def flag_evidence(flag: str, reply: str, result: dict, message: Message) -> str:
    """The words in the reply that tripped this flag, using the same patterns as checks.py."""
    if flag == "unlisted_time":
        return ", ".join(unlisted_times(reply, allowed_times(message.id, result) or []))
    if flag == "other_candidate":
        for name in other_names(result["unique"], message):
            hit = re.search(rf"(?<!\w){re.escape(name)}(?!\w)", reply, re.IGNORECASE)
            if hit:
                return hit.group(0)
        return ""
    pattern = dict(FIXED_FLAGS).get(flag)
    hit = pattern.search(reply) if pattern else None
    return " ".join(hit.group(0).split()) if hit else ""


def flag_badge(flag: str, reply: str, result: dict, message: Message) -> str:
    label = FLAG_LABELS.get(flag, flag)
    evidence = flag_evidence(flag, reply, result, message)
    if evidence:
        label += f": \u201c{evidence}\u201d"
    tip = html.escape(FLAG_HELP.get(flag, ""), quote=True)
    return f'<span class="ih-flag" title="{tip}">{html.escape(label)}</span>'


# --- state helpers ---------------------------------------------------------------------


def outbox_items(result: dict) -> list[dict]:
    """Approved replies, ready to send. Starts with review.py's {id, to, reply, edited} and adds
    what you need to read the outbox on its own: who, what they asked, when it was approved."""
    state = st.session_state
    items = []
    for intent, messages in ordered_groups(result):
        for message in messages:
            if state.decisions.get(message.id) != "Approved":
                continue
            original = result["drafts"][message.id].reply
            reply = state.get(text_key(message.id), original).strip()
            if not reply:
                continue
            items.append({
                "id": message.id,
                "to": message.from_email,
                "reply": reply,
                "edited": is_edited(message.id, reply, original),
                "name": message.from_name,
                "subject": SUBJECT,
                "intent": intent,
                "original_message": message.text,
                "flags": result["flags"].get(message.id, []),
                "revisions": len(state.revisions.get(message.id, [])),
                "reschedule": current_kind(message.id) if is_reschedule(message.id, result) else "",
                "approved_at": state.approved_at.get(message.id, ""),
                "status": "ready_to_send",
            })
    return items


def is_edited(msg_id: int, reply: str, original: str) -> bool:
    """Changed by hand or revised by the model. Picking slots or a version doesn't count."""
    built = st.session_state.rendered.get(msg_id, original)
    return reply.strip() != built.strip() or bool(st.session_state.revisions.get(msg_id))


def decision_counts(result: dict) -> dict[str, int]:
    total = sum(len(m) for m in result["groups"].values())
    values = list(st.session_state.decisions.values())
    approved, on_hold = values.count("Approved"), values.count("On hold")
    return {"Approved": approved, "On hold": on_hold, "Pending": total - approved - on_hold}


def decide(msg_id: int, status: str) -> None:
    st.session_state.decisions[msg_id] = status
    if status == "Approved":
        st.session_state.approved_at[msg_id] = datetime.now().isoformat(timespec="seconds")
    else:
        st.session_state.approved_at.pop(msg_id, None)
    st.session_state.feedback_open.discard(msg_id)


def reply_edited(msg_id: int) -> None:
    """Reviewer applied an edit: tidy filled-in blanks, and re-open the card if it was approved."""
    key = text_key(msg_id)
    st.session_state[key] = unwrap_filled_blanks(st.session_state.get(key, ""))
    reopen_if_approved(msg_id)


def reopen_if_approved(msg_id: int) -> None:
    """Editing an approved reply sends it back to Pending, so it has to be approved again."""
    if st.session_state.decisions.get(msg_id) == "Approved":
        st.session_state.decisions.pop(msg_id)
        st.session_state.approved_at.pop(msg_id, None)


def toggle_feedback(msg_id: int) -> None:
    st.session_state.feedback_open ^= {msg_id}


def text_key(msg_id: int) -> str:
    # run_no in the key so a new run doesn't inherit the last run's edits.
    return f"draft_{st.session_state.run_no}_{msg_id}"


def gmail_url(to: str, body: str) -> str:
    query = urlencode({"view": "cm", "fs": "1", "to": to, "su": SUBJECT, "body": body}, quote_via=quote)
    return f"https://mail.google.com/mail/?{query}"


main()
