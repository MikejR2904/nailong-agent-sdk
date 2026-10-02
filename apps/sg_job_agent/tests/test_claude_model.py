"""ClaudeAgentModel through a real BaseAgent run, with a fake Anthropic client."""

from __future__ import annotations

import asyncio
import copy
import json

import pytest
from anthropic.types.beta import BetaMessage

from job_agent.claude_model import ClaudeAgentModel
from job_agent.pipeline import JobAgentPipeline

PROFILE = {
    "status": "complete",
    "candidate": {"headline": "ML engineer", "years_experience": 4, "core_skills": ["Python"]},
    "roles": [
        {
            "title": "Machine Learning Engineer",
            "seniority": "mid",
            "fit_rationale": "PyTorch in production",
            "title_keywords": ["machine learning engineer"],
            "search_queries": ["machine learning engineer singapore"],
        }
    ],
}


def message(content, stop_reason="tool_use", **extra):
    return BetaMessage.model_validate(
        {
            "id": f"msg_{len(content)}",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-5",
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 80},
            **extra,
        }
    )


class FakeStream:
    def __init__(self, reply):
        self.reply = reply

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_final_message(self):
        return self.reply


class FakeClient:
    """Mimics ``client.beta.messages.stream(**params)`` and records each request."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests: list[dict] = []
        self.beta = self
        self.messages = self

    def stream(self, **params):
        self.requests.append(copy.deepcopy(params))
        return FakeStream(self.replies.pop(0))


def run_profile(tmp_path, config, sources, resume_file, replies):
    client = FakeClient(replies)
    config.model.provider = "anthropic"
    config.model.model = "claude-opus-5-5"
    pipeline = JobAgentPipeline(
        config,
        tmp_path / "ws",
        model_factory=lambda _d: ClaudeAgentModel(client, model="claude-opus-5-5"),
        sources=sources,
        log=lambda _: None,
    )
    pipeline.import_resume(resume_file)
    return client, pipeline


THINK = {"type": "thinking", "thinking": "", "signature": "sig-abc"}


def test_append_only_transcript_and_final_via_tool(tmp_path, config, sources, resume_file):
    invalid = {k: v for k, v in PROFILE.items() if k != "roles"}  # schema violation
    client, pipeline = run_profile(
        tmp_path,
        config,
        sources,
        resume_file,
        [
            message(
                [
                    THINK,
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "read_file",
                        "input": {"path": "base_resume.tex"},
                    },
                ]
            ),
            message(
                [{"type": "tool_use", "id": "toolu_2", "name": "submit_result", "input": invalid}]
            ),
            message(
                [{"type": "tool_use", "id": "toolu_3", "name": "submit_result", "input": PROFILE}]
            ),
        ],
    )
    profile = asyncio.run(pipeline.profile())
    assert profile["roles"][0]["title"] == "Machine Learning Engineer"

    first, second, third = client.requests
    # Request shape for Claude Opus 5.5.
    assert first["model"] == "claude-opus-5-5" and "temperature" not in first
    assert first["thinking"] == {"type": "adaptive"}
    assert first["output_config"] == {"effort": "high"}
    assert first["fallbacks"] == "default" and first["betas"] == ["server-side-fallback-2026-07-01"]
    names = [tool["name"] for tool in first["tools"]]
    assert names == [
        "read_file",
        "search_experience",
        "read_experience",
        "submit_result",
        "report_blocked",
    ]
    assert all(tool["eager_input_streaming"] for tool in first["tools"])
    assert first["tools"] == second["tools"] == third["tools"]  # stable tool set

    # Append-only: every request extends the previous one byte-for-byte.
    assert second["messages"][: len(first["messages"])] == first["messages"]
    assert third["messages"][: len(second["messages"])] == second["messages"]
    assistant = second["messages"][1]
    assert assistant["role"] == "assistant" and assistant["content"][0] == THINK
    tool_result = second["messages"][2]["content"][0]
    assert tool_result["tool_use_id"] == "toolu_1" and "Acme AI Pte Ltd" in tool_result["content"]

    # The schema rejection of the first submit comes back as an error result for it.
    rejection = third["messages"][-1]["content"][0]
    assert rejection["tool_use_id"] == "toolu_2" and rejection["is_error"] is True
    assert "roles" in rejection["content"]


def test_refusal_becomes_blocked_and_truncation_fails(tmp_path, config, sources, resume_file):
    client, pipeline = run_profile(
        tmp_path,
        config,
        sources,
        resume_file,
        [
            message(
                [{"type": "text", "text": ""}],
                stop_reason="refusal",
                stop_details={"type": "refusal", "category": "cyber", "explanation": None},
            )
        ],
    )
    with pytest.raises(RuntimeError, match="declined"):
        asyncio.run(pipeline.profile())

    client, pipeline = run_profile(
        tmp_path,
        config,
        sources,
        resume_file,
        [
            message(
                [{"type": "tool_use", "id": "t", "name": "read_file", "input": {"path": "x"}}],
                stop_reason="max_tokens",
            )
        ],
    )
    with pytest.raises(RuntimeError, match="pop from empty list"):
        asyncio.run(pipeline.profile())  # replies exhausted after the retry request
    retry = client.requests[1]["messages"][-1]["content"][0]
    assert retry["tool_use_id"] == "t" and "max_tokens" in retry["content"]


def test_text_reply_is_nudged_by_appending(tmp_path, config, sources, resume_file):
    client, pipeline = run_profile(
        tmp_path,
        config,
        sources,
        resume_file,
        [
            message(
                [{"type": "text", "text": "Let me think about roles."}], stop_reason="end_turn"
            ),
            message([{"type": "tool_use", "id": "t9", "name": "submit_result", "input": PROFILE}]),
        ],
    )
    asyncio.run(pipeline.profile())
    first, second = client.requests
    assert second["messages"][: len(first["messages"])] == first["messages"]
    assert "submit_result" in json.dumps(second["messages"][-1])


def test_bad_api_key_ends_run_after_one_request(tmp_path, config, sources, resume_file):
    import anthropic
    import httpx2

    class Unauthorized(FakeClient):
        def stream(self, **params):
            self.requests.append(params)
            request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
            raise anthropic.AuthenticationError(
                "invalid x-api-key",
                response=httpx2.Response(401, request=request),
                body=None,
            )

    client = Unauthorized([])
    config.model.model = "claude-opus-5-5"
    pipeline = JobAgentPipeline(
        config,
        tmp_path / "ws",
        model_factory=lambda _d: ClaudeAgentModel(client, model="claude-opus-5-5"),
        sources=sources,
        log=lambda _: None,
    )
    pipeline.import_resume(resume_file)
    with pytest.raises(RuntimeError, match="401"):
        asyncio.run(pipeline.profile())
    assert len(client.requests) == 1
