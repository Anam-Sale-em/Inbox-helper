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

from inbox import classify, draft
import re

from inbox.checks import FIXED_FLAGS, run_checks
from difflib import SequenceMatcher

from inbox.dedupe import NEAR_DUPLICATE_RATIO, dedupe, normalize
from inbox.llm import MockResponseMissing
from inbox.schema import Message
from main import check_drafts, group_by_intent, load_messages, make_run_dir, other_names, save

DATA_DIR = Path("data")
SUBJECT = "Re: your application"
MAX_WORDS = 80
REVISION_NOTE_AFTER = 3  # soft note only; revising is never blocked

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
    "salary": "A salary figure appears in the reply. Approve only if you meant to share it.",
    "date_commitment": "A day/date appears close to a word like 'confirm'. Check the reply doesn't promise a date you haven't agreed.",
    "offer_or_outcome": "The reply sounds like an offer or a result. Check that's been decided.",
    "other_candidate": "Another candidate's name appears in the reply. Remove it before sending.",
}
FLAG_LABELS = {
    "salary": "Salary mentioned",
    "date_commitment": "Date commitment",
    "offer_or_outcome": "Offer/outcome",
    "other_candidate": "Other candidate named",
}
STATUS_CLASS = {"Pending": "pending", "Approved": "approved", "On hold": "hold"}

CSS = """
<style>
.ih-head {display:flex; justify-content:space-between; align-items:flex-start; gap:12px;}
.ih-name {font-weight:600; font-size:1.02rem;}
.ih-meta {opacity:.65; font-size:.85rem; margin-top:2px;}
.ih-chip {font-size:.75rem; font-weight:600; padding:3px 10px; border-radius:999px;
          white-space:nowrap; border:1px solid transparent;}
.ih-chip.pending  {color:#8a8f98; background:rgba(138,143,152,.12); border-color:rgba(138,143,152,.35);}
.ih-chip.approved {color:#2f9e5b; background:rgba(47,158,91,.12);  border-color:rgba(47,158,91,.4);}
.ih-chip.hold     {color:#b7791f; background:rgba(214,158,46,.14); border-color:rgba(214,158,46,.45);}
.ih-msg {white-space:pre-wrap; padding:10px 14px; margin:10px 0 6px; border-radius:6px;
         background:rgba(128,128,128,.08); border-left:3px solid rgba(128,128,128,.35);}
.ih-flag {display:inline-block; font-size:.75rem; font-weight:600; padding:2px 8px; margin:2px 6px 2px 0;
          border-radius:4px; color:#e5484d; background:rgba(229,72,77,.1); border:1px solid rgba(229,72,77,.35);}
.ih-note {display:inline-block; font-size:.75rem; padding:2px 8px; margin:2px 6px 2px 0; border-radius:4px;
          opacity:.75; border:1px dashed rgba(128,128,128,.5);}
.ih-rev {display:inline-block; font-size:.75rem; font-weight:600; padding:2px 8px; margin:2px 6px 2px 0;
         border-radius:4px; color:#3b82f6; background:rgba(59,130,246,.1); border:1px solid rgba(59,130,246,.35);}
</style>
"""


def main() -> None:
    st.set_page_config(page_title="Inbox Helper", page_icon="📥", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    st.title("Inbox Helper")
    st.caption("Sort candidate messages, draft replies, approve before sending")

    state = st.session_state
    state.setdefault("decisions", {})
    state.setdefault("approved_at", {})      # msg_id -> ISO time it was last approved
    state.setdefault("run_no", 0)
    state.setdefault("revisions", {})        # msg_id -> [{"comment", "previous"}]
    state.setdefault("feedback_open", set())  # msg_ids with the "Request changes" box open
    state.setdefault("pending_replies", {})  # msg_id -> revised text to load into the text area

    sidebar()
    result = state.get("result")
    if result is None:
        empty_state()
        return

    metrics(result)
    intent_tabs(result)
    st.divider()
    outbox_section(result)


# --- sidebar ---------------------------------------------------------------------


def sidebar() -> None:
    state = st.session_state
    with st.sidebar:
        st.header("Run")
        files = sorted(str(p) for p in DATA_DIR.glob("*.json"))
        if not files:
            st.warning(f"No message files found. Add a .json file to `{DATA_DIR}/` to get started.")
            return

        default = files.index("data/messages.json") if "data/messages.json" in files else 0
        path = st.selectbox("Input file", files, index=default)
        mock = st.toggle("Mock mode", value=True, key="mock",
                         help="Replay recorded model responses instead of calling Gemini/Groq.")

        if st.button("Run pipeline", type="primary", width="stretch"):
            with st.spinner("Removing duplicates, sorting and drafting replies…"):
                try:
                    state.result = run_pipeline(path, mock)
                    state.decisions = {}
                    state.approved_at = {}
                    state.revisions = {}
                    state.feedback_open = set()
                    state.pending_replies = {}
                    state.run_no += 1
                    state.pop("last_saved", None)
                except Exception as exc:
                    traceback.print_exc()  # full detail in the terminal, friendly text in the UI
                    st.error(friendly_error(exc, path, mock))

        if state.get("result"):
            st.divider()
            st.subheader("Progress")
            counts = decision_counts(state.result)
            st.markdown(f"**{counts['Approved']}** approved · **{counts['On hold']}** on hold · "
                        f"**{counts['Pending']}** pending")
            st.caption(f"Source: {state.result['source']}")


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


def run_pipeline(path: str, mock: bool) -> dict:
    with provider_env(mock):
        messages = load_messages(path)
        if not messages:
            raise ValueError("empty input")
        unique, duplicates = dedupe(messages)
        classifications = classify.classify(unique)
        drafts = draft.draft_replies(unique, classifications)
        flags = check_drafts(unique, drafts)
    return {
        "source": path,
        "unique": unique,
        "messages": messages,
        "duplicates": duplicates,
        "classifications": classifications,
        "drafts": drafts,
        "flags": flags,
        "groups": group_by_intent(unique, classifications),
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
    if isinstance(exc, MockResponseMissing):
        return ("Mock mode has no recorded response for this input. Turn mock mode off and run once "
                "with `RECORD=1` set, or pick a file you've already recorded.")
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
    cols = st.columns(6)
    cols[0].metric("Messages", len(result["messages"]))
    cols[1].metric("Duplicates removed", len(result["duplicates"]))
    cols[2].metric("Categories", len(result["groups"]))
    cols[3].metric("Flagged drafts", len(result["flags"]),
                   help="Drafts with something worth a second look (a date, salary, offer wording or "
                        "another candidate's name). Open the card to see what matched.")
    cols[4].metric("Needs human", intents.count("needs_human"))
    cols[5].metric("On hold", decision_counts(result)["On hold"])

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
        st.info("Every message was a duplicate, so there's nothing to review.")
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
                card(message, result)

    with tabs[-1]:
        if not held:
            st.caption("Nothing on hold. Use Hold on any message that needs a human to handle it later.")
        for message in held:
            card(message, result, show_intent=True)


def card(message: Message, result: dict, show_intent: bool = False) -> None:
    state = st.session_state
    run_no = state.run_no
    original = result["drafts"][message.id].reply
    status = state.decisions.get(message.id, "Pending")
    history = state.revisions.get(message.id, [])

    # The text area owns the reply. Seed it once, and load a fresh revision before it renders.
    key = text_key(message.id)
    if message.id in state.pending_replies:
        state[key] = state.pending_replies.pop(message.id)
    state.setdefault(key, original)

    with st.container(border=True):
        st.markdown(card_header(message, status), unsafe_allow_html=True)

        flags = result["flags"].get(message.id, [])
        labels = "".join(flag_badge(f, reply_for_flags(message.id, result), result, message)
                         for f in flags)
        if show_intent:
            intent = result["classifications"][message.id].intent
            labels = f'<span class="ih-note">{html.escape(INTENT_LABELS.get(intent, intent))}</span>' + labels
        if history:
            labels += f'<span class="ih-rev">Revision {len(history)}</span>'
        if not original.strip() and not history:
            labels += '<span class="ih-note">No draft generated — write one or put it on hold</span>'
        if labels:
            st.markdown(labels, unsafe_allow_html=True)
        if flags:
            st.caption("⚠ Flags are warnings for you to check, not errors. Hover a flag to see why.")

        reply = st.text_area("Draft reply", key=key, placeholder="Write a reply…", height=120,
                             on_change=reopen_if_approved, args=(message.id,))
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
                       disabled=status == "Approved" or not reply.strip(),
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
    revise, cancel, _ = st.columns([1.3, 1, 4])
    cancel.button("Cancel", key=f"cancel_{suffix}", on_click=toggle_feedback,
                  args=(message.id,), width="stretch")
    if not revise.button("Revise draft", key=f"revise_{suffix}", type="primary", width="stretch"):
        return
    if not comment.strip():
        st.warning("Write what should change first.")
        return

    with st.spinner("Revising draft…"), provider_env(state.get("mock", True)):
        intent = result["classifications"][message.id].intent
        revised = draft.revise_reply(message, intent, reply, comment.strip()).reply.strip()

    if not revised or revised == reply.strip():
        hint = (" Mock mode is on and can only replay recorded responses; turn it off in the "
                "sidebar to revise with the live model." if state.get("mock", True) else "")
        st.warning("Couldn't revise this draft, so it's unchanged. Try again or edit it manually."
                   + hint)
        return

    state.revisions.setdefault(message.id, []).append({"comment": comment.strip(), "previous": reply})
    state.pending_replies[message.id] = revised
    flags = run_checks(revised, other_names(result["unique"], message))
    if flags:
        result["flags"][message.id] = flags
    else:
        result["flags"].pop(message.id, None)
    state.decisions.pop(message.id, None)  # back to Pending
    state.approved_at.pop(message.id, None)
    state.feedback_open.discard(message.id)
    state.pop("last_saved", None)
    st.rerun()


def card_header(message: Message, status: str) -> str:
    received = message.received_at.strftime("%d %b %Y, %H:%M")
    if message.received_at.tzinfo:
        received += f" {message.received_at.strftime('%Z')}"
    return (
        '<div class="ih-head"><div>'
        f'<div class="ih-name">{html.escape(message.from_name)}</div>'
        f'<div class="ih-meta">{html.escape(message.from_email)} · {received}</div>'
        f'</div><span class="ih-chip {STATUS_CLASS[status]}">{status}</span></div>'
        f'<div class="ih-msg">{html.escape(message.text)}</div>'
    )


def outbox_section(result: dict) -> None:
    st.subheader("Outbox")
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

    download, save_col, _ = st.columns([1, 1.2, 2.8])
    download.download_button("Download outbox.json", data=json.dumps(items, indent=2, ensure_ascii=False),
                             file_name="outbox.json", mime="application/json", width="stretch")
    if save_col.button("Save outbox to project", width="stretch"):
        try:
            run_dir = make_run_dir()
            save(run_dir, outbox=items)
            st.session_state.last_saved = str((run_dir / "outbox.json").resolve())
        except OSError as exc:
            st.error(f"Couldn't save the outbox: {exc.strerror or exc}.")
    if st.session_state.get("last_saved"):
        st.success(f"Saved {len(items)} approved repl{'y' if len(items) == 1 else 'ies'} to "
                   f"`{st.session_state.last_saved}`")


# --- flag evidence ---------------------------------------------------------------------


def reply_for_flags(msg_id: int, result: dict) -> str:
    """The reply the flags were computed on: the current text area value if any, else the draft."""
    return st.session_state.get(text_key(msg_id), result["drafts"][msg_id].reply)


def flag_evidence(flag: str, reply: str, result: dict, message: Message) -> str:
    """The words in the reply that tripped this flag, using the same patterns as checks.py."""
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
                "approved_at": state.approved_at.get(message.id, ""),
                "status": "ready_to_send",
            })
    return items


def is_edited(msg_id: int, reply: str, original: str) -> bool:
    """Changed by hand or revised by the model at least once."""
    return reply.strip() != original.strip() or bool(st.session_state.revisions.get(msg_id))


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
    st.session_state.pop("last_saved", None)


def reopen_if_approved(msg_id: int) -> None:
    """Editing an approved reply sends it back to Pending, so it has to be approved again."""
    if st.session_state.decisions.get(msg_id) == "Approved":
        st.session_state.decisions.pop(msg_id)
        st.session_state.approved_at.pop(msg_id, None)
        st.session_state.pop("last_saved", None)


def toggle_feedback(msg_id: int) -> None:
    st.session_state.feedback_open ^= {msg_id}


def text_key(msg_id: int) -> str:
    # run_no in the key so a new run doesn't inherit the last run's edits.
    return f"draft_{st.session_state.run_no}_{msg_id}"


def gmail_url(to: str, body: str) -> str:
    query = urlencode({"view": "cm", "fs": "1", "to": to, "su": SUBJECT, "body": body}, quote_via=quote)
    return f"https://mail.google.com/mail/?{query}"


main()