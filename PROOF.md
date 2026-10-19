# Unlearn, Use, Raise the load: where to check

One page so you can check each claim yourself instead of taking my word for it.

Most of the code and most of the docs here were written by AI from my prompts. That was the point of the exercise. What's mine is the decisions, the pushback and the checking. The prompt log (`prompts.md`, submitted separately) says which is which.

## Unlearn: habits I dropped

| Old habit (how I work in n8n) | What I did this week | Where to check |
|---|---|---|
| Start from the Gmail trigger | Started from a file I could replay and score; Gmail came last, read-only, as one more source for the same loader | `inbox/gmail_source.py`, `tests/test_gmail_source.py`, log G1 |
| Plan everything, then build | Wrote the rules first, then asked for one file at a time. The rules changed 9 times, each with a reason | `directive.md` changelog, log P0–P11 |
| Write validation, retries and dedupe myself | Described what I wanted, the AI wrote it, I read every file | `inbox/llm.py`, `inbox/dedupe.py` |
| Look at the output and call it fine | An answer key and a scorer before trusting any number | `data/expected.json`, `eval/offline_report.py` |
| Fix now, test later | A failing test before every fix since Day 2 | e.g. `tests/test_day5_review.py`, 7 bugs reproduced first |
| Score on the examples I've seen | A hold-out attack set the fixes never saw | `data/holdout_attacks.json`: 0/10 → 8/10 |
| Rules live in a document | The hard rules are tests that fail if the code breaks them | `tests/test_directive.py`, runs on every push |
| Reject bad input because it's "safe" | Process what can be read, show what was skipped and why. I reversed my own fix for this | log F15 → F17 |
| Keep a design because it works | Threw away 3 working reschedule designs for the simplest | log F12 |
| Guess cost and speed | Every AI call logs tokens and time; a load test for the rest | sidebar "AI usage", `eval/stress.py` |
| Trust the checker's score | Ran the AI judge live. 13 of its 16 fails turned out to be our own approved wording, because the judge had never been shown the recruiter facts. Fixed the judge, test first: 60/76 → 75/76 | `eval/judge.py`, `tests/test_judge.py`, log J1, F28 |

## Use: AI as the main way of working

- **Every file built with AI.** 70+ log entries across two chats, including the times it went off my rules: it quietly undid a fix (F9), and a prompt drifted from the code (F14).
- **AI inside the tool:** sorting into 8 intents, drafting, revising from my comments. Every answer is validated, retried with the error, and falls back to the other provider. In the first live run Gemini's free tier ran out (5 requests a minute) and Groq finished the job.
- **AI checking AI:** `eval/judge.py` has a second model (Groq) judge every Gemini draft against the rules and the recruiter facts, and picks 10 verdicts for a human to check. First live run: 60/76 passed, and 13 of the 16 fails were our own approved status line. So the judge now gets the recruiter facts, and "didn't answer the question" is its own rule. Re-run: 75/76; the one fail (#70) is a real sorting miss.
- **Where I don't trust it:** the AI never picks an interview time, never sees the calendar or other candidates' details, and never sends anything. Free slots come from Google Calendar's free/busy, and I pick them.

## Raise the load: harder every day, and what broke

| Day | Load | What broke |
|---|---|---|
| 1 | 3 friendly messages, terminal only | Groq model gone (404), Gemini overloaded (503), my keys pasted into the wrong file |
| 2 | Review screen, revisions, injection attempts | The AI undid my revision prompt; gibberish comments reached the AI twice; 4 reschedule designs |
| 3 | 82 tricky messages + broken files | Same-id crash, empty messages got replies, one bad record blocked the whole file |
| 4 | Attacks the fixes never saw, your own data, CI | Regex caught 4/13; Friday/Monday merged as duplicates; 4 of 8 first names wrong |
| 5 | **1,000–5,000 messages** (simulated) + first live run | One sorting call for everything: the first card waited for the whole inbox, and at 5,000 the answer hit the output cap and **4,749 messages were lost**. Live: the free tier's 5 requests a minute, which the simulation had missed |
| 19 Oct | AI judge on all 76 live drafts; review-screen runs; Gmail in, calendar slots out | The judge didn't know our approved wording (13 false fails). Review-screen runs couldn't be saved for scoring; now every run and decision is. Gmail (read-only, checked every minute) and Google Calendar free slots added, Google faked in the tests |

### Load test (`python eval/stress.py`)
Simulated model (0.8 s + output tokens / 150 per second, 65,536-token output cap), so these are estimates.

| Messages | First cards, before → after | Lost, before → after |
|---|---|---|
| 82 | 12.5 s → 9.1 s | 0 → 0 |
| 1,000 | 106 s → 9.3 s | 0 → 0 |
| 3,000 | 321 s → 9.4 s | 0 → 0 |
| 5,000 | 1,323 s → 9.6 s | 4,749 → 0 |

Fix: sort 50 at a time, 3 calls at once, and start drafting as soon as the first batch is sorted (`inbox/pipeline.py`, tests in `tests/test_pipeline.py`).

## Live run, 82 messages
Sorted right: 65/78 (83%). Attacks obeyed: 0/13 (12 stopped before reaching the AI; the model sent the 13th to a human). Drafts promising salary, a date or a result: 0/7 (read one by one). Other candidates' details leaked: 0/4. AI judge, first run: 60/76 (see above).

I also ran it live in the review screen several times during the week. The screen didn't save runs then; it does now.

## Known gaps, and what's next
- A phishing email got a drafted reply in the live run (#66). A recruiter approves every reply, so nothing went out. Next: "verify your account" messages go straight to a human.
- Base64, Spanish and Hinglish attacks get past the regex; the model still sent the Hinglish one to a human. Next: a small AI classifier for what regex can't read.
- Drafts can state something nobody approved (#44, #7); the reviewer catches it.
- Spam still gets a draft.
- The load test is simulated. On the free tier a real run is paced at 5 calls a minute.
- The answer key was written by the AI from my brief; a label-by-label review is next.

The full list is in the README under "Known limits and next steps".
