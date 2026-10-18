# Inbox Helper

![tests](https://github.com/Anam-Sale-em/Inbox-helper/actions/workflows/tests.yml/badge.svg)

A recruiter gets a pile of candidate messages. This sorts them by what the person wants, drafts a short reply for each, and lets the recruiter review every draft. **Nothing is sent automatically.** Approved replies go to `outbox.json`.

All data here is synthetic. No real candidate messages.

**[PROOF.md](PROOF.md)**: one page mapping what I unlearned, how AI was used and how the load was raised to the code and tests that show it.

## How it works
```
file / upload / paste → skip empty, broken and auto-replies → remove duplicates → in waves: sort (50 per call) + draft (5 per call), 3 calls at a time → checks → review → outbox.json
```
- **Duplicates**: same sender, exact or ≥90% similar text with the same days and times ("Friday" vs "Monday" is not a copy). The earliest is kept; the review screen shows both with the reason.
- **8 intents**: reschedule_interview, application_status, salary_question, offer_response, withdrawal, document_submission, other, needs_human
- **Reliability**: every AI response is validated (Pydantic). Bad JSON is retried twice with the error sent back; 429/5xx back off 2/4/8s, then switch provider (Gemini ↔ Groq). If both are down, the message goes to `needs_human` and the run carries on.
- **Speed and cost**: messages are sorted 50 per call and drafted 5 per call, 3 calls at a time, in waves, so the first cards show after one wave whatever the inbox size. Every call's time and tokens are shown in the sidebar and saved with the run.
- **Prompt injection**: message text is treated as data and wrapped in escaped tags. Attempts to instruct the AI skip it and get a holding reply. The check cleans hidden and look-alike characters first.
- **No reply needed**: auto-replies and bounces are skipped before the AI and listed with the reason.
- **Checks**: drafts are flagged (not blocked) for salary figures, date commitments, offer/result language, other candidates' names and specific times. Each flag shows the words that set it off.

## Review screen (Streamlit)
- **Messages from**: a sample file, an upload (JSON, or CSV with columns like `name, email, message`), or pasted lines (`Name <email>: message`). Anything unreadable is listed with the reason; the rest still runs.
- **Approve / Request changes / Hold** on each draft. You can request changes as many times as you need; each comment is checked first (min 3 words, not "ok", not vague, not cut off, not gibberish), so only real requests reach the AI.
- **Reschedule**: the AI writes two versions once. Yes = offer with a blank `[add time slots]` that you fill in; No = the time can't change. Switching doesn't call the AI. Approve stays off until the blank is filled with a real time.
- Tabs per group, plus **On hold**. Approved replies are saved to `outbox.json` with the original message, flags, revisions and time.

## Setup (Windows, PowerShell)
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```
Fill in `.env` with your own free keys. **No keys are included in this repo.**
```
LLM_PROVIDER=gemini          # gemini | groq | mock
GEMINI_API_KEY=...           # https://aistudio.google.com/apikey
GEMINI_MODEL=gemini-2.5-flash
GROQ_API_KEY=...             # https://console.groq.com/keys (fallback)
GROQ_MODEL=openai/gpt-oss-120b
GEMINI_THINKING_BUDGET=0     # optional; 0 = thinking off (faster, cheaper)
GROQ_REASONING_EFFORT=low    # optional
GEMINI_RPM=5                 # optional; requests per minute (free tier is 5), 0 = no pacing
GROQ_RPM=30                  # optional
```

Tested on Windows (PowerShell) with Python 3.12, streamlit 1.66, google-genai 2.30, groq 1.7, pydantic 2.14, pytest 9.1, and on Linux by GitHub Actions.

## Run
```powershell
streamlit run app.py              # review screen in the browser
python main.py                    # same pipeline, review in the terminal
python main.py --input data/messages.json --context config/recruiter_context.json
```

**Mock mode (no key needed):** replays AI responses saved from a real run.
```powershell
$env:RECORD="1"; python main.py   # real run that also saves the responses
python main.py --mock             # replay them
```
In the UI, Mock mode is a sidebar toggle (on by default). It only works for messages that were recorded; any prompt change needs a new recording.

## Input format
`data/messages.json`, a list of:
```json
{"id": 1, "from_name": "Riya Sharma", "from_email": "riya@example.com",
 "received_at": "2026-10-14T09:00:00", "text": "Can we move my interview to Friday?"}
```

## Test data
All synthetic.
- `data/messages.json`: 82 tricky messages: same sender with different asks, real and fake duplicates, 14 prompt-injection attempts (some disguised), promise bait, attempts to get other candidates' details, sensitive cases, other languages, odd names
- `data/expected.json`: the answer key (intent, duplicate, attack type, and why each one is tricky)
- `data/edge_*.json`: format edge cases: odd text, repeated ids, mixed timezones, broken records. Bad records are skipped and shown with the reason; the rest of the file still runs.

- `data/holdout_attacks.json` + `holdout_expected.json`: attacks the injection check was never tuned on, plus ordinary look-alikes that must not be flagged

## Checking the results
```powershell
python eval/offline_report.py                     # no API key: duplicates, injection check, names, no-reply
$env:RECORD="1"; python main.py --input data/messages.json
python eval/day1_report.py                        # intents, duplicates, attacks, against the answer key
python eval/judge.py                              # a second model (Groq) checks every draft; spot-check 10 by hand
python eval/stress.py                             # load test, 82 to 5,000 messages, simulated model (no key)
python eval/stress.py --old                       # same, with the Day 4 flow, for comparison
```

| Offline check (Day 4) | Before | After |
|---|---|---|
| False duplicate merges | 1 | 0 |
| Injection pre-check, main set | 4/13 | 12/13 |
| Injection pre-check, hold-out (never tuned on) | 0/10 | 8/10, 0/4 false alarms |
| First names right | 4/8 | 8/8 |
| Auto-replies/bounces skipped | 0/3 | 2/3 (spam still gets a draft) |

| Load test (Day 5, simulated model) | Before | After |
|---|---|---|
| First cards at 1,000 messages | 106 s | 9.3 s |
| First cards at 5,000 messages | 1,323 s | 9.6 s |
| Messages lost at 5,000 (output cap hit) | 4,749 | 0 |

## Recruiter facts (optional)
`config/recruiter_context.json`:
```json
{"status_update": "We share an update within 5 working days of each interview round.",
 "salary_policy": "The CTC range is shared after the final interview round."}
```
Used for status and salary replies. There are deliberately no interview slots in here; the recruiter types times into the reply.

## Tests
```powershell
python -m pytest -q     # 193 tests, no API calls; also run on every push by GitHub Actions
```

## Known limits
- Injection pre-check misses base64-hidden text, Spanish and Hinglish attacks. The prompt rules are the main defence; the live attack numbers come from `eval/day1_report.py` and `eval/judge.py`
- The hold-out attacks were written by the same AI chat as the fixes, so they aren't fully independent
- The answer key was drafted by AI; the log lists the labels I changed after reviewing it
- Spam still gets a draft
- Date check misses "around 3 PM" and false-alarms on "Friday and will confirm"
- `main.py --mock` crashes if a response wasn't recorded; recordings go stale whenever a prompt changes
- A vowel-heavy keyboard mash ("qwe rty uio") still passes the comment check
- A reschedule offer only needs *a* time; there's no check that it's a sensible date
- On Gemini's free tier (5 requests/minute) a live run of the 82 messages takes about 4 minutes; calls are paced and fall back to Groq when Gemini's quota is used up
- The terminal review still offers [r]eject; the web UI uses Hold
- No automated tests for `app.py` itself (checked by a scripted browser click-through)
- The load test uses a simulated model, so its times and costs are estimates
- Runs locally only
