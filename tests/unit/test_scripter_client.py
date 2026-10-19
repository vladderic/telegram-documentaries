"""Unit tests for the Scripter's Gemini client (Phase 5).

The real Gemini API is never contacted: responses are mocked with
httpx.MockTransport and parsed into the typed ``Script`` contract at the
boundary (TECH.md). All keys below are placeholders.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from telegram_documentaries.gemini import GeminiApiError
from telegram_documentaries.models import BehaviouralProfile
from telegram_documentaries.scripter import (
    MAX_WORDS,
    SCRIPT_RULES,
    SCRIPTER_MODEL,
    SCRIPTER_PERSONA,
    ScripterClient,
    build_prompt,
)

PLACEHOLDER_API_KEY = "PLACEHOLDER-GEMINI-API-KEY"

PROFILE = BehaviouralProfile(
    profile=(
        "The subject patrols the kitchen at midnight, hoards tea cups and "
        "greets strangers with suspicion."
    ),
    suggested_animal="red fox",
    animal_description="A lone hunter of the small hours.",
)

Handler = Callable[[httpx.Request], httpx.Response]


def gemini_response(payload: dict[str, Any]) -> dict[str, Any]:
    """Wrap a payload in a realistic Gemini generateContent envelope."""
    return {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"text": json.dumps(payload)}],
                },
                "finishReason": "STOP",
            }
        ],
        "modelVersion": SCRIPTER_MODEL,
    }


def make_client(handler: Handler) -> ScripterClient:
    return ScripterClient(
        api_key=PLACEHOLDER_API_KEY,
        transport=httpx.MockTransport(handler),
    )


def request_body(seen: list[httpx.Request]) -> dict[str, Any]:
    return json.loads(seen[0].content)


def prompt_text(body: dict[str, Any]) -> str:
    parts = body["contents"][0]["parts"]
    return next(part["text"] for part in parts if "text" in part)


def system_instruction(body: dict[str, Any]) -> str:
    return body["systemInstruction"]["parts"][0]["text"]


def narration(words: int) -> str:
    return " ".join(f"word{number}" for number in range(1, words + 1))


def script_handler(text: str) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=gemini_response({"text": text}))

    return handler


class TestBuildPrompt:
    def test_carries_the_dossier_facts_only(self) -> None:
        """The persona and the rules are the system instruction, not the task."""
        prompt = build_prompt(PROFILE)

        assert PROFILE.profile in prompt
        assert PROFILE.suggested_animal in prompt
        assert PROFILE.animal_description in prompt
        assert SCRIPTER_PERSONA not in prompt
        assert SCRIPT_RULES not in prompt

    def test_names_the_species_the_subject_was_cast_as(self) -> None:
        prompt = build_prompt(PROFILE).lower()

        assert "red fox" in prompt


class TestVoice:
    """The narrator the user asked for: documentarian, not a fact sheet."""

    def test_persona_is_a_revered_british_documentary_narrator(self) -> None:
        persona = SCRIPTER_PERSONA.lower()

        assert "british" in persona
        assert "wildlife" in persona
        assert "documentary" in persona
        assert "narrator" in persona

    def test_persona_frames_a_human_subject_as_the_creature(self) -> None:
        persona = SCRIPTER_PERSONA.lower()

        assert "human" in persona
        assert "animal" in persona

    def test_rules_bound_the_output_for_the_microphone(self) -> None:
        rules = SCRIPT_RULES.lower()

        assert "one paragraph" in rules
        assert str(MAX_WORDS) in rules
        assert "markdown" in rules
        assert "no line breaks" in rules

    def test_rules_demand_grounded_prose_that_names_the_species(self) -> None:
        rules = SCRIPT_RULES.lower()

        assert "species" in rules
        assert "nothing" in rules and "invent" in rules


class TestWriteScript:
    @pytest.mark.asyncio
    async def test_sends_the_instruction_as_a_system_instruction(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(
                200,
                json=gemini_response({"text": "Our subject wakes at midnight."}),
            )

        client = make_client(handler)
        try:
            await client.write_script(PROFILE)
        finally:
            await client.close()

        request = seen[0]
        assert request.url.path.endswith(f"/models/{SCRIPTER_MODEL}:generateContent")
        assert PLACEHOLDER_API_KEY not in str(request.url)

        body = request_body(seen)
        instruction = system_instruction(body)
        assert SCRIPTER_PERSONA in instruction
        assert SCRIPT_RULES in instruction
        assert prompt_text(body) == build_prompt(PROFILE)
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        schema = body["generationConfig"]["responseSchema"]
        assert set(schema["properties"]) == {"text"}
        assert schema["required"] == ["text"]

    @pytest.mark.asyncio
    async def test_returns_a_typed_single_paragraph_script(self) -> None:
        client = make_client(script_handler("Our subject wakes.\n\nThe hunt   begins."))
        try:
            script = await client.write_script(PROFILE)
        finally:
            await client.close()

        assert script.text == "Our subject wakes. The hunt begins."

    @pytest.mark.asyncio
    async def test_empty_narration_is_a_typed_failure(self) -> None:
        client = make_client(script_handler("   "))
        try:
            with pytest.raises(GeminiApiError):
                await client.write_script(PROFILE)
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_missing_text_field_is_a_typed_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=gemini_response({"narration": "Nope."}))

        client = make_client(handler)
        try:
            with pytest.raises(GeminiApiError):
                await client.write_script(PROFILE)
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_http_error_is_typed_and_sanitised(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": {"message": PLACEHOLDER_API_KEY}})

        client = make_client(handler)
        try:
            with pytest.raises(GeminiApiError) as caught:
                await client.write_script(PROFILE)
        finally:
            await client.close()

        assert "500" in str(caught.value)
        assert PLACEHOLDER_API_KEY not in str(caught.value)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "response_body",
        [
            pytest.param({"candidates": []}, id="no-candidates"),
            pytest.param(
                {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]},
                id="not-json",
            ),
            pytest.param(
                {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]},
                id="empty-object",
            ),
        ],
    )
    async def test_untrusted_response_shapes_raise_typed_errors(
        self, response_body: dict[str, Any]
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=response_body)

        client = make_client(handler)
        try:
            with pytest.raises(GeminiApiError):
                await client.write_script(PROFILE)
        finally:
            await client.close()


class TestWordLimit:
    """Decision: over-length is reported, never silently dropped or cut."""

    @pytest.mark.asyncio
    async def test_over_the_limit_is_still_returned_and_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        over = narration(MAX_WORDS + 1)

        client = make_client(script_handler(over))
        with caplog.at_level(logging.WARNING):
            try:
                script = await client.write_script(PROFILE)
            finally:
                await client.close()

        assert script.text == over
        warnings = [record for record in caplog.records if record.levelno >= 30]
        assert any(
            str(MAX_WORDS + 1) in record.getMessage()
            and str(MAX_WORDS) in record.getMessage()
            for record in warnings
        )

    @pytest.mark.asyncio
    async def test_at_the_limit_logs_no_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = make_client(script_handler(narration(MAX_WORDS)))
        with caplog.at_level(logging.WARNING):
            try:
                script = await client.write_script(PROFILE)
            finally:
                await client.close()

        assert len(script.text.split()) == MAX_WORDS
        assert [record for record in caplog.records if record.levelno >= 30] == []
