# Feedback rules

`learnradar/feedback.py` is the single source for values, display text, and prompt blocks.

| Key | Meaning | Options |
| --- | --- | --- |
| `understood_what` | Did the explanation clarify what the project does? | yes / uncertain / no / unanswered |
| `understood_highlight` | Did at least one concrete strength become understandable? | yes / uncertain / no / unanswered |
| `want_more` | Do you want to learn more? | yes / uncertain / no / unanswered |

Notes are optional; declining interest does not require explanation. Feedback binds to a normalized repository and a specific run. SQLite feedback is keyed by run_id. Repeated submissions update the same row, preserving first answered_at and recording updated_at. Original choices/notes remain private and unscored.

Reuse only the latest verbatim feedback for the same target and current comparators, with time/run context. Display it to the user and label report appendix text as user speech. It is not evidence, never evidence_ids, and cannot change a claim's type. Do not infer long-term interests/language preferences, create exclusion lists, treat comprehension as endorsement, infer cross-project preferences, or add a feedback-analysis model.

Published reports show content and forms. Rejected but renderable reports show their marked unpublished drafts and errors and accept feedback. Unrenderable damaged payloads show diagnostics only and do not accept feedback.
