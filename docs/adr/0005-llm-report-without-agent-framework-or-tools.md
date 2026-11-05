# ADR 0005: LLM report with one call, no agent framework and no tools

Status: accepted

## Context
The rules in `app/ops-agent` already collect evidence and name a category, and they say "unknown" when the evidence doesn't decide. What they can't do is explain how the evidence fits together or suggest what to check next. An LLM can, but its input includes log lines and commit messages, which anyone able to send a request or push a commit can influence. A prompt injection has to be assumed possible.

## Decision
- One request per report: the rule findings, the matching runbook and the redacted evidence go in, and a structured report comes out. There is no agent framework or loop.
- The model has no tools. The backend collects a fixed set of evidence before the call, so the model can't choose queries, read more data or change anything. An injected instruction can at worst produce a wrong report.
- The rules still decide the category and run first. Any failure falls back to the rules-only report: no key, an API error or timeout, a refusal, invalid output, a citation of evidence that wasn't collected, evidence over the size limit, or a spent daily budget. The report's `llm_status` records which one.
- Evidence is redacted before the request is built, and a test checks the actual outgoing request body. Redaction matches known shapes, so it reduces the risk of sending a secret but doesn't remove it.
- The rule finding leads every report. The model's text follows as AI notes that can be wrong, because in evaluation a small local model sometimes contradicted the rules. Next steps are text for a person; nothing executes them.

## Consequences
- The model can't look further when the evidence is incomplete. It can only list what's unknown, so better evidence means changing the collectors.
- `LLM_PROVIDER` selects Anthropic or a local Ollama model. Both pass the same redaction and validation. With Anthropic, evidence and logs go to the provider; with Ollama, they stay on the machine, at the cost of a smaller model and a host that needs a GPU to be practical. With neither configured, the agent is rules-only.
- Ollama cuts prompts that overflow its context window without returning an error, so the agent limits prompt size and treats a full context as a failure.
- Citations are checked for existence only. A cited item may not support the claim, so people still have to verify the report.
- The budget is an estimate from token counts and list prices, and it is checked before each call, so one call can go over it. It is not a billing control.
- Scoring in `tests/eval/run_llm.py` is a coarse check: it can catch a missing concept or a forbidden recommendation, but not judge the quality of an explanation. Reports passed it while being wrong, so they have to be read as well (`docs/RESULTS.md`).
