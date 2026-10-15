"""Facts the recruiter provides so drafts can answer instead of stalling.

The AI has no HR policy of its own. Without these facts it can only say "we'll get back
to you" or invent details. Optional: if the file is missing, drafts just don't use them.
Interview slots are not here: the approver types them on each reschedule card.
"""

import json
from pathlib import Path

from pydantic import BaseModel

DEFAULT_PATH = Path("config/recruiter_context.json")


class RecruiterContext(BaseModel):
    status_update: str = ""            # e.g. "We share an update within 5 working days."
    salary_policy: str = ""            # e.g. "The CTC range is shared after the final round."


def load_context(path: str | Path = DEFAULT_PATH) -> RecruiterContext:
    """Read the facts file; a missing file means no facts."""
    path = Path(path)
    if not path.exists():
        return RecruiterContext()
    data = json.loads(path.read_text(encoding="utf-8"))
    for old_key in ("interview_slots", "reschedule_allowed"):  # older files had these
        data.pop(old_key, None)
    return RecruiterContext.model_validate(data)


def facts_block(context: RecruiterContext | None) -> str:
    """The facts the AI may use, as a tagged block."""
    data = (context or RecruiterContext()).model_dump()
    return "<recruiter_facts>\n" + json.dumps(data, indent=2, ensure_ascii=False) + "\n</recruiter_facts>"
