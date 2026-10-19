# Inbox Helper

![tests](https://github.com/Anam-Sale-em/Inbox-helper/actions/workflows/tests.yml/badge.svg)

A recruiter gets a pile of candidate messages. This sorts them by what the person wants, drafts a short reply for each, and lets the recruiter review every draft. **Nothing is sent automatically.** Approved replies go to `outbox.json`.

All data here is synthetic. No real candidate messages.

**[PROOF.md](PROOF.md)**: one page mapping what I unlearned, how AI was used and how the load was raised to the code and tests that show it.

## Highlights
- **Runs without an API key.** My live run of the 82 test messages is recorded, so `--mock` replays it in the terminal or the review screen.
- **218 tests**, run on every push with the offline scorer and a load test (GitHub Actions, no keys).
- **Two layers against prompt injection.** In the live run, 12 of 13 attacks were stopped before reaching the AI and the model sent the 13th to a human. None obeyed.
- **Load-tested to 5,000 messages** (simulated model). One sorting call for everything lost 4,749 messages at the output cap; sorting in waves loses none and shows the first cards in about 9 s at any size.
- **Live-tested on the free tier.** The first live run found Gemini's 5 requests/minute limit; calls are now paced per provider and fall back to Groq.
- **AI checking AI.** A second model judges every draft against the rules and the recruiter facts, and picks 10 verdicts for a human to check. Its first live run showed its own blind spot: 13 of its 16 fails were our approved status line. Fixed.
- **Gmail in, calendar times out (optional).** Reads candidate emails from a Gmail label, read-only, checking every minute like n8n's Gmail trigger. Reschedule cards show free slots from Google Calendar, the day the candidate asked for first; the recruiter picks and the picks fill the blank. The AI never sees the calendar. Tested against a fake Gmail and calendar; your own account needs the one-time setup below.

## How it works
```
file / upload / paste / Gmail → skip empty, broken and auto-replies → remove duplicates → in waves: sort (50 per call) + draft (5 per call), 3 calls at a time → checks → review → outbox.json
```
- **Duplicates**: same sender, exact or ≥90% similar text with the same days and times ("Friday" vs "Monday" is not a copy). The earliest is kept; the review screen shows both with the reason.
- **8 intents**: reschedule_interview, application_status, salary_question, offer_response, withdrawal, document_submission, other, needs_human
- **Reliability**: every AI response is validated (Pydantic). Bad JSON is retried twice with the error sent back; 429/5xx back off 2/4/8s, then switch provider (Gemini ↔ Groq). If both are down, the message goes to `needs_human` and the run carries on.
- **Speed and cost**: messages are sorted 50 per call and drafted 5 per call, 3 calls at a time, in waves, so the first cards show after one wave whatever the inbox size. Every call's time and tokens are shown in the sidebar and saved with the run.
- **Prompt injection**: message text is treated as data and wrapped in escaped tags. Attempts to instruct the AI skip it and get a holding reply. The check cleans hidden and look-alike characters first.
- **No reply needed**: auto-replies and bounces are skipped before the AI and listed with the reason.
- **Checks**: drafts are flagged (not blocked) for salary figures, date commitments, offer/result language, other candidates' names and specific times. Each flag shows the words that set it off.

## Review screen (Streamlit)
- **Messages from**: a sample file, an upload (JSON, or CSV with columns like `name, email, message`), pasted lines (`Name <email>: message`), or Gmail (see below). Anything unreadable is listed with the reason; the rest still runs.
- **Approve / Request changes / Hold** on each draft. You can request changes as many times as you need; each comment is checked first (min 3 words, not "ok", not vague, not cut off, not gibberish), so only real requests reach the AI.
- **Reschedule**: the AI writes two versions once. Yes = offer with a blank `[add time slots]` that you fill in; No = the time can't change. Switching doesn't call the AI. Approve stays off until the blank is filled with a real time. With Google Calendar on, the card shows your free slots to click instead of typing.
- Tabs per group, plus **On hold**.
- **Every run is saved** to `runs/<time>/`, the same files as a terminal run, so `eval/day1_report.py` and `eval/judge.py` work on it. Decisions are saved as you make them: approved replies to `outbox.json` (with the original message, flags, revisions and time), held ones to `on_hold.json`.

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

**Try it without an API key.** My live run of the 82 test messages is recorded in `data/mock_responses/`, so this replays it:
```powershell
python main.py --mock             # terminal review: a = approve, e = edit, h = hold
streamlit run app.py              # keep Mock mode on, pick data/messages.json, press Run pipeline
```
Revising a draft needs a real key (revisions weren't recorded).

**With your own key:**
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
python eval/judge.py                              # a second model (Groq) checks every draft against the rules and recruiter facts; spot-check 10 by hand
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

| Live run (Gemini + Groq fallback, 82 messages) | Result |
|---|---|
| Intent accuracy | 65/78 (83%) |
| Injections obeyed | 0/13 (12 stopped before the AI, the 13th sent to a human by the model) |
| Drafts that promised salary/date/result (read one by one) | 0/7 |
| Other candidates' details leaked | 0/4 |
| Replies drafted that shouldn't exist | 1 (phishing, #66; a recruiter approves every reply) |
| AI judge (Groq), first run | 60/76 passed. 13 of the 16 fails were our approved status line: the judge hadn't been given the recruiter facts. Fixed |

These numbers are from the saved run `runs/2026-10-18_231721`. I also ran it live in the review screen several times; the screen didn't save runs then, and it does now.

| Load test (Day 5, simulated model) | Before | After |
|---|---|---|
| First cards at 1,000 messages | 106 s | 9.3 s |
| First cards at 5,000 messages | 1,323 s | 9.6 s |
| Messages lost at 5,000 (output cap hit) | 4,749 | 0 |

## Gmail and Google Calendar (optional)
Off by default; everything above works without them.
- **Gmail** is one more source for the same loader and rules. A Gmail search picks the emails (`GMAIL_QUERY`, default `label:inbox-helper newer_than:14d`); a Gmail filter that labels candidate mail makes it the trigger. Emails already loaded are skipped, and only once their run is saved. The review screen can check every minute and tells you when new emails are waiting; it never starts a run by itself, so a review in progress isn't replaced.
- **Google Calendar** fills reschedule offers. One free/busy read per run; slots of 45 minutes between 10:00 and 18:00 IST on the next 5 working days, not sooner than 12 hours, the weekday the candidate asked for first. You pick the slots; they replace the blank. Settings: `CAL_*` in `.env.example`.
- **Read-only.** The sign-in asks for `gmail.readonly` and `calendar.freebusy` only: nothing can be sent, changed or deleted, and event titles and guests are never read (a test enforces both). The AI never sees the calendar.
- **Stays on your machine:** `credentials.json`, `token.json`, `.gmail_seen.json` and Gmail run folders (`runs/*_gmail`) are git-ignored, so real email never reaches GitHub. Use test emails: drafts go to the AI provider, and Gemini's free tier may use inputs for training.

**Setup (about 15 minutes, once):**
1. [Google Cloud console](https://console.cloud.google.com): new project → enable the **Gmail API** and the **Google Calendar API**.
2. **Google Auth Platform**: Branding (app name, your email) → Audience: External, add your Gmail under Test users → Clients: Create client → Desktop app → download the JSON as `credentials.json` in this folder.
3. Then:
```powershell
pip install -r requirements-google.txt
python -m inbox.google_auth        # opens the browser once; saves token.json
python -m inbox.gmail_source       # lists the emails your search matches
python -m inbox.calendar_slots     # lists your next free slots
python main.py --gmail             # new emails, terminal review
streamlit run app.py               # Messages from: Gmail; toggle "Suggest times from Google Calendar"
```
Test-mode sign-ins expire after 7 days; run `python -m inbox.google_auth` again.

## Recruiter facts (optional)
`config/recruiter_context.json`:
```json
{"status_update": "We share an update within 5 working days of each interview round.",
 "salary_policy": "The CTC range is shared after the final interview round."}
```
Used for status and salary replies. There are deliberately no interview slots in here; the recruiter types times into the reply.

## Tests
```powershell
python -m pytest -q     # 218 tests, no API calls (Gmail and Calendar are faked); also run on every push by GitHub Actions
```

## Known limits and next steps
- **Injection pre-check** is a regex: it misses base64-hidden text, Spanish and Hinglish. Those reach the AI, where the prompt rules take over (the live Hinglish attack went to a human). Next: a small AI classifier for what regex can't read.
- **Phishing** aimed at the recruiter can be sorted as `other` and get a draft (live run, #66). A recruiter approves every reply, so nothing goes out. Next: "verify your account / log in here" messages go straight to a human.
- **Spam** still gets a draft; no rule I tried was free of false alarms.
- **Drafts can state something nobody approved**, e.g. #44 "We cannot guarantee a fully remote arrangement", #7 "you remain in the process". No flag catches these yet; the reviewer does, and the judge now has the recruiter facts to check against.
- **Reschedule replies** don't confirm a time the candidate already gave; the recruiter types it into the blank. Next: a "confirm this time" reply. Offers also only check that a time is present, not that it's a sensible date.
- **Messages with two asks** sometimes fall into `other` (#60, #61).
- **Test data:** the answer key was drafted by AI from my brief and several live "misses" are judgement calls (#3, #38, #39, #42). The hold-out attacks were written by the same AI chat as the fixes. Next: a label-by-label review and attacks written by someone else.
- **Date check** misses "around 3 PM" and false-alarms on "Friday and will confirm".
- **Mock mode** only replays what a live run recorded (the 82-message set is). Anything else stops with a clear message, and recordings go stale whenever a prompt changes.
- A vowel-heavy keyboard mash ("qwe rty uio") still passes the comment check.
- **Free tier:** at Gemini's 5 requests/minute a live run of the 82 messages needs at least 3.6 minutes (18 calls); Groq takes over when Gemini's quota runs out.
- **`app.py`** is about 900 lines, against my own small-files rule. One automated test drives the review screen (run, approve, hold, save); the rest is checked by browser click-throughs. Next: split it into views.
- **Gmail and Calendar:** the every-minute check runs only while the review screen is open (no server-side push), and reads up to 50 emails per check. Free slots come from one calendar on the hour or half hour; picked slots aren't booked in the calendar (that needs write access, left out on purpose). The test suite fakes Google, so CI never signs in.
- The load test uses a simulated model, so its times and costs are estimates.
- Runs locally only.
