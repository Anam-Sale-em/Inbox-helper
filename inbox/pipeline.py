"""Sort and draft in waves, so the first cards show in seconds whatever the inbox size.

Each step: sort the next 3 batches of 50, plan their replies, then draft the next 3 batches of 5.
The review screen calls step() once per rerun; tests and eval/stress.py loop until done.
Duplicates and empty/auto-reply messages are removed before start(), on the whole inbox.
"""

from dataclasses import dataclass, field

from inbox import classify, draft
from inbox.context import RecruiterContext
from inbox.schema import Classification, Draft, Message


@dataclass
class Progress:
    unique: list[Message]
    context: RecruiterContext | None
    classifications: dict[int, Classification] = field(default_factory=dict)
    drafts: dict[int, Draft] = field(default_factory=dict)
    to_classify: list[list[Message]] = field(default_factory=list)
    to_draft: list[list[Message]] = field(default_factory=list)

    @property
    def done(self) -> bool:
        return not self.to_classify and not self.to_draft

    @property
    def sorted_messages(self) -> list[Message]:
        return [m for m in self.unique if m.id in self.classifications]


def start(unique: list[Message], context: RecruiterContext | None) -> Progress:
    """No AI calls yet: attacks get needs_human and a holding reply straight away."""
    decided, rest = classify.precheck(unique)
    progress = Progress(unique=unique, context=context, classifications=dict(decided),
                        to_classify=classify.make_batches(rest))
    _plan(progress, [m for m in unique if m.id in decided])
    return progress


def step(progress: Progress) -> Progress:
    """One wave of sorting, then one wave of drafting."""
    if progress.to_classify:
        wave = progress.to_classify[: classify.PARALLEL_BATCHES]
        progress.to_classify = progress.to_classify[classify.PARALLEL_BATCHES:]
        for done in classify.classify_batches(wave):
            progress.classifications.update(done)
        _plan(progress, [m for batch in wave for m in batch])
    if progress.to_draft:
        wave = progress.to_draft[: draft.PARALLEL_BATCHES]
        progress.to_draft = progress.to_draft[draft.PARALLEL_BATCHES:]
        for done in draft.draft_batches(wave, progress.classifications, progress.context):
            progress.drafts.update(done)
    return progress


def give_up(progress: Progress) -> None:
    """After an error: everything still waiting becomes needs_human with an empty draft (the
    reviewer writes it or puts it on hold). Nothing is left half-done."""
    for batch in progress.to_classify:
        for m in batch:
            progress.classifications[m.id] = Classification(id=m.id, intent="needs_human", confidence=0.0)
    for m in progress.unique:
        if m.id in progress.classifications and m.id not in progress.drafts:
            progress.drafts[m.id] = Draft(id=m.id, reply="")
    progress.to_classify, progress.to_draft = [], []


def _plan(progress: Progress, messages: list[Message]) -> None:
    ready, batches = draft.plan_drafts(messages, progress.classifications)
    progress.drafts.update(ready)
    progress.to_draft.extend(batches)
