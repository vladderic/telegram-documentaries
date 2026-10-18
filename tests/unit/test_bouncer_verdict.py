import pytest
from pydantic import ValidationError

from telegram_documentaries.models import BouncerVerdict, RejectionReason


def test_accept_verdict_parses() -> None:
    v = BouncerVerdict(
        accepted=True,
        observed_subject="a single person",
        reason=None,
        message="Nice to meet you. Let's begin.",
    )
    assert v.accepted is True
    assert v.observed_subject == "a single person"
    assert v.message == "Nice to meet you. Let's begin."


def test_reject_verdict_parses() -> None:
    v = BouncerVerdict(
        accepted=False,
        observed_subject="a Honda Civic",
        reason=RejectionReason.NOT_A_REAL_PHOTO,
        message="Nice ride, but I need to see YOU...",
    )
    assert v.accepted is False
    assert v.reason == RejectionReason.NOT_A_REAL_PHOTO
    assert "Honda Civic" in v.observed_subject


def test_missing_field_fails() -> None:
    with pytest.raises(ValidationError):
        BouncerVerdict.model_validate({"accepted": True, "message": "hi"})


def test_wrong_types_fail() -> None:
    with pytest.raises(ValidationError):
        BouncerVerdict.model_validate(
            {
                "accepted": "yes",
                "observed_subject": 123,
                "reason": "BAD",
                "message": None,
            }
        )


def test_junk_payload_fails_loudly() -> None:
    with pytest.raises(ValidationError):
        BouncerVerdict.model_validate({"foo": "bar", "baz": [1, 2, 3]})


def test_reject_verdict_requires_a_reason() -> None:
    """A rejection without a reason could dodge explaining why (spec guardrail)."""
    with pytest.raises(ValidationError):
        BouncerVerdict(
            accepted=False,
            observed_subject="a group of people",
            message="Nope.",
        )


def test_accept_verdict_must_not_carry_a_reason() -> None:
    with pytest.raises(ValidationError):
        BouncerVerdict(
            accepted=True,
            observed_subject="a single person",
            reason=RejectionReason.NOT_SOLO_HUMAN,
            message="Come in.",
        )
