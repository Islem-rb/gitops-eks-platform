import json

import anthropic
import httpx
import httpx2
import pytest

from agent import llm
from agent.models import Bundle, Evidence, Finding, LLMAnalysis

SECRET = "hunter2-do-not-send"
GOOD = {
    "summary": "Redis is down.",
    "hypotheses": [{"cause": "Redis has no replicas", "confidence": "high",
                    "evidence_ids": ["k8s.deployments"]}],
    "unknowns": ["why"],
    "next_steps": ["look"],
}


def bundle():
    return Bundle(
        alerts=["ApiHighErrorRate"],
        evidence=[
            Evidence(id="k8s.deployments", source="k8s", data=[{"name": "redis", "ready": 0}]),
            Evidence(
                id="loki.errors",
                source="loki",
                data={"samples": [{"app": "api", "line": f"postgresql://app:{SECRET}@db/app"}]},
            ),
            Evidence(id="prom.error_rate", source="prom", ok=False, error="ConnectError"),
        ],
    )


FINDINGS = [Finding(category="dependency_down", title="t", explanation="e", confidence="high")]


@pytest.fixture
def guard(tmp_path):
    return llm.SpendGuard(str(tmp_path / "spend.json"), 1.0)


def api(sent=None, payload=GOOD, status=200, stop_reason="end_turn", text=None, error=None):
    def handler(request):
        if sent is not None:
            sent.append(request)
        if error:
            raise error
        if status != 200:
            error_body = {"type": "error", "error": {"type": "x", "message": "x"}}
            return httpx2.Response(status, json=error_body)
        content = [{"type": "text", "text": text if text is not None else json.dumps(payload)}]
        return httpx2.Response(
            200,
            json={
                "id": "m", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                "stop_reason": stop_reason, "stop_sequence": None, "content": content,
                "usage": {"input_tokens": 1000, "output_tokens": 500},
            },
        )

    transport = httpx2.MockTransport(handler)
    http_client = httpx2.Client(transport=transport)
    return anthropic.Anthropic(api_key="k", max_retries=0, http_client=http_client)


def run(client, guard):
    provider = llm.AnthropicProvider(client)
    return llm.analyze(provider, bundle(), FINDINGS, "runbook text", guard)


def test_good_response_is_returned_with_its_cost(guard):
    outcome = run(api(), guard)
    assert outcome.status == "ok"
    assert isinstance(outcome.analysis, LLMAnalysis)
    assert outcome.usage.estimated_usd == pytest.approx(0.014)


def test_secret_never_reaches_the_outgoing_request(guard):
    sent = []
    run(api(sent), guard)
    body = sent[0].read().decode()
    assert SECRET not in body
    assert "[REDACTED]@db/app" in body


def test_request_has_no_tools_and_marks_evidence_as_data(guard):
    sent = []
    run(api(sent), guard)
    body = json.loads(sent[0].read())
    assert "tools" not in body
    assert body["fallbacks"] == "default"
    assert "strictly as data" in body["system"]
    assert "<evidence>" in body["messages"][0]["content"]


def test_failed_evidence_is_reported_but_cannot_be_cited(guard):
    sent = []
    claim = {"cause": "c", "confidence": "low", "evidence_ids": ["prom.error_rate"]}
    bad = GOOD | {"hypotheses": [claim]}
    outcome = run(api(sent, bad), guard)
    assert outcome.status == "invalid_citation"
    assert outcome.analysis is None
    assert "prom.error_rate" in json.loads(sent[0].read())["messages"][0]["content"]


@pytest.mark.parametrize("ids", [["made.up"], []])
def test_hypothesis_needs_real_evidence(guard, ids):
    bad = GOOD | {"hypotheses": [{"cause": "c", "confidence": "low", "evidence_ids": ids}]}
    assert run(api(payload=bad), guard).status == "invalid_citation"


def test_no_hypotheses_is_a_valid_answer(guard):
    assert run(api(payload=GOOD | {"hypotheses": []}), guard).status == "ok"


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"status": 401}, "auth_error"),
        ({"status": 429}, "rate_limited"),
        ({"status": 500}, "api_error_500"),
        ({"error": httpx2.ConnectError("down")}, "connection_error"),
        ({"error": httpx2.ReadTimeout("slow")}, "timeout"),
        ({"text": "not json"}, "invalid_output"),
        ({"text": '{"summary": "missing fields"}'}, "invalid_output"),
    ],
)
def test_failures_become_a_status_not_an_exception(guard, kwargs, expected):
    outcome = run(api(**kwargs), guard)
    assert outcome.status == expected
    assert outcome.analysis is None


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"stop_reason": "refusal", "text": ""}, "refused"),
        ({"stop_reason": "max_tokens", "text": '{"summary": "cut o'}, "invalid_output"),
        ({"text": "not json"}, "invalid_output"),
    ],
)
def test_unusable_output_still_counts_against_the_budget(guard, kwargs, expected):
    outcome = run(api(**kwargs), guard)
    assert outcome.status == expected
    assert outcome.analysis is None
    assert outcome.usage.estimated_usd == pytest.approx(0.014)
    assert guard._spent() == pytest.approx(0.014)


def test_error_text_is_redacted_too(guard):
    sent = []
    item = bundle()
    item.evidence[2].error = f"postgresql://app:{SECRET}@db failed"
    llm.analyze(llm.AnthropicProvider(api(sent)), item, FINDINGS, "", guard)
    assert SECRET not in sent[0].read().decode()


def test_budget_blocks_the_call(tmp_path):
    guard = llm.SpendGuard(str(tmp_path / "spend.json"), 0.01)
    assert run(api(), guard).status == "ok"  # spends 0.014
    sent = []
    assert run(api(sent), guard).status == "budget_exceeded"
    assert sent == []


def test_budget_survives_a_restart(tmp_path):
    path = str(tmp_path / "spend.json")
    llm.SpendGuard(path, 0.01).record(0.02)
    assert llm.SpendGuard(path, 0.01).allows_spending() is False


def test_oversized_evidence_is_not_silently_cut(guard, monkeypatch):
    monkeypatch.setattr(llm.AnthropicProvider, "max_prompt_chars", 10)
    sent = []
    assert run(api(sent), guard).status == "evidence_too_large"
    assert sent == []


def test_unknown_model_is_priced_high():
    assert llm.estimate_cost("new-model", 1_000_000, 0) == 10.0


def local(sent=None, content=None, status=200, error=None, **fields):
    def handler(request):
        if sent is not None:
            sent.append(request)
        if error:
            raise error
        body = {
            "model": "qwen3:8b",
            "message": {"role": "assistant", "content": content or json.dumps(GOOD)},
            "done_reason": "stop",
            "prompt_eval_count": 1000,
            "eval_count": 500,
        }
        return httpx.Response(status, json=body | fields)

    http = httpx.Client(base_url="http://ollama", transport=httpx.MockTransport(handler))
    return llm.OllamaProvider(http)


def run_local(provider, guard):
    return llm.analyze(provider, bundle(), FINDINGS, "runbook text", guard)


def test_local_model_is_free_and_gets_the_same_checks(guard):
    outcome = run_local(local(), guard)
    assert outcome.status == "ok"
    assert outcome.usage.estimated_usd == 0
    bad = GOOD | {"hypotheses": [{"cause": "c", "confidence": "low", "evidence_ids": ["no.such"]}]}
    assert run_local(local(content=json.dumps(bad)), guard).status == "invalid_citation"


def test_local_request_is_redacted_and_constrained_to_the_schema(guard):
    sent = []
    run_local(local(sent), guard)
    assert SECRET not in sent[0].read().decode()
    body = json.loads(sent[0].read())
    assert body["format"]["required"]
    assert body["options"]["num_ctx"] == llm.OLLAMA_CONTEXT_TOKENS
    assert "tools" not in body
    assert "strictly as data" in body["messages"][0]["content"]


def test_context_overflow_is_reported_because_ollama_cuts_silently(guard):
    provider = local(prompt_eval_count=llm.OLLAMA_CONTEXT_TOKENS, eval_count=1)
    assert run_local(provider, guard).status == "evidence_too_large"


def test_oversized_prompt_never_reaches_the_local_model(guard, monkeypatch):
    monkeypatch.setattr(llm.OllamaProvider, "max_prompt_chars", 10)
    sent = []
    assert run_local(local(sent), guard).status == "evidence_too_large"
    assert sent == []


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"error": httpx.ConnectError("not running")}, "connection_error"),
        ({"error": httpx.ReadTimeout("slow")}, "timeout"),
        ({"status": 404}, "api_error_404"),
        ({"content": "not json"}, "invalid_output"),
        ({"done_reason": "length"}, "invalid_output"),
    ],
)
def test_local_failures_become_a_status(guard, kwargs, expected):
    outcome = run_local(local(**kwargs), guard)
    assert outcome.status == expected
    assert outcome.analysis is None


def test_provider_selection():
    assert llm.make_provider("anthropic", "", "", "http://x") is None
    assert isinstance(llm.make_provider("anthropic", "", "k", "http://x"), llm.AnthropicProvider)
    assert isinstance(llm.make_provider("ollama", "", "", "http://x"), llm.OllamaProvider)
