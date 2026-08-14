"""Tests for user-readable request-validation errors.

Regression cover for the production bug where a user on the login screen was
shown "body.password: string should have at least 8 characters" - FastAPI's
raw Pydantic error rendered verbatim. The global RequestValidationError
handler must return a sentence written for an end user, and must never leak
a `loc` path or framework prose into the text a client renders.
"""

import pytest

from app.common.exceptions import humanize_validation_errors

# Substrings that betray a raw framework error reaching a client: a `loc`
# path joined into the message, or Pydantic's own prose.
LEAK_MARKERS = (
    "body.",
    "query.",
    "String should",
    "Field required",
    "Value error,",
    "value is not a valid",
)


def assert_no_leak(text: str) -> None:
    """Assert a user-facing string carries no framework detail."""
    for marker in LEAK_MARKERS:
        assert marker not in text, f"leaked {marker!r} in: {text}"


# The unit under test: the error -> sentence mapper


class TestHumanizeValidationErrors:
    """humanize_validation_errors() maps Pydantic errors to sentences."""

    def test_reported_bug_password_min_length(self):
        """The exact error from the production report reads as a sentence."""
        (error,) = humanize_validation_errors(
            [
                {
                    "type": "string_too_short",
                    "loc": ("body", "password"),
                    "msg": "String should have at least 8 characters",
                    "ctx": {"min_length": 8},
                }
            ]
        )

        assert error["message"] == "Password must be at least 8 characters."
        assert error["field"] == "password"
        assert_no_leak(error["message"])

    def test_missing_field(self):
        (error,) = humanize_validation_errors(
            [{"type": "missing", "loc": ("body", "email"), "msg": "Field required"}]
        )

        assert error["message"] == "Email address is required."
        assert error["field"] == "email"

    def test_invalid_email_replaces_pydantic_prose(self):
        (error,) = humanize_validation_errors(
            [
                {
                    "type": "value_error",
                    "loc": ("body", "email"),
                    "msg": (
                        "value is not a valid email address: "
                        "An email address must have an @-sign."
                    ),
                }
            ]
        )

        assert error["message"] == "Email address must be a valid email address."
        assert_no_leak(error["message"])

    def test_nested_list_field_labels_the_leaf(self):
        """`lines[0].quantity` is labelled by the value that failed."""
        (error,) = humanize_validation_errors(
            [
                {
                    "type": "greater_than_equal",
                    "loc": ("body", "lines", 0, "quantity"),
                    "msg": "Input should be greater than or equal to 1",
                    "ctx": {"ge": 1},
                }
            ]
        )

        assert error["field"] == "lines[0].quantity"
        assert error["message"] == "Quantity must be at least 1."

    def test_singular_character_is_not_pluralized(self):
        (error,) = humanize_validation_errors(
            [
                {
                    "type": "string_too_short",
                    "loc": ("body", "full_name"),
                    "msg": "String should have at least 1 character",
                    "ctx": {"min_length": 1},
                }
            ]
        )

        assert error["message"] == "Full name must be at least 1 character."

    def test_custom_validator_text_is_spelled_out(self):
        """A hand-raised RequestValidationError keeps the author's wording.

        purchase_orders' calculate endpoint raises this shape directly, with
        no `ctx` - the mapper must not depend on one.
        """
        (error,) = humanize_validation_errors(
            [
                {
                    "type": "value_error",
                    "loc": ("query", "vatRate"),
                    "msg": (
                        "Value error, vat_rate is required when vat_enabled is true"
                    ),
                    "input": None,
                }
            ]
        )

        assert error["field"] == "vatRate"
        assert error["message"] == ("Vat rate is required when vat enabled is true.")
        assert_no_leak(error["message"])

    def test_unmapped_type_falls_back_to_generic_sentence(self):
        """An error type the mapper does not know must still be readable."""
        (error,) = humanize_validation_errors(
            [
                {
                    "type": "some_future_pydantic_type",
                    "loc": ("body", "widget_id"),
                    "msg": "Some raw framework prose that must not be shown",
                }
            ]
        )

        assert error["message"] == "Widget id is not valid."
        assert "raw framework prose" not in error["message"]

    def test_raw_loc_and_msg_are_preserved(self):
        """The change is additive: existing consumers keep working."""
        raw = {
            "type": "missing",
            "loc": ("body", "email"),
            "msg": "Field required",
        }
        (error,) = humanize_validation_errors([raw])

        assert error["loc"] == ("body", "email")
        assert error["msg"] == "Field required"
        assert error["type"] == "missing"

    def test_input_errors_are_not_mutated(self):
        """Humanizing copies rather than editing the caller's dicts."""
        raw = {"type": "missing", "loc": ("body", "email"), "msg": "Field required"}
        humanize_validation_errors([raw])

        assert "message" not in raw
        assert "field" not in raw

    @pytest.mark.parametrize(
        "error_type,ctx,expected",
        [
            ("string_too_long", {"max_length": 64}, "must be at most 64 characters."),
            ("too_short", {"min_length": 1}, "must have at least 1 item."),
            ("int_parsing", {}, "must be a whole number."),
            ("float_parsing", {}, "must be a number."),
            ("bool_parsing", {}, "must be true or false."),
            ("date_parsing", {}, "must be a valid date."),
            ("uuid_parsing", {}, "must be a valid identifier."),
            ("string_pattern_mismatch", {}, "is not in the expected format."),
            ("less_than_equal", {"le": 1}, "must be at most 1."),
        ],
    )
    def test_common_constraint_types(self, error_type, ctx, expected):
        (error,) = humanize_validation_errors(
            [
                {
                    "type": error_type,
                    "loc": ("body", "amount"),
                    "msg": "raw",
                    "ctx": ctx,
                }
            ]
        )

        assert error["message"] == f"Amount {expected}"
        assert_no_leak(error["message"])


# The behaviour a client actually sees, over HTTP


class TestValidationResponseShape:
    """The 422 envelope carries user-readable text end to end."""

    def test_login_short_password_is_readable(self, client):
        """The reported bug, reproduced against the real login endpoint."""
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "frank@mail.com", "password": "short"},
        )

        assert response.status_code == 422
        body = response.json()

        # The summary a client renders when it shows `error` verbatim.
        assert body["error"] == "Password must be at least 8 characters."
        assert body["error_code"] == "VALIDATION_ERROR"
        assert_no_leak(body["error"])

        (error,) = body["details"]["errors"]
        assert error["field"] == "password"
        assert error["message"] == "Password must be at least 8 characters."
        # Raw Pydantic values still available for debugging.
        assert error["loc"] == ["body", "password"]

    def test_login_invalid_email_is_readable(self, client):
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "not-an-email", "password": "Securepass123!"},
        )

        assert response.status_code == 422
        body = response.json()
        assert body["error"] == "Email address must be a valid email address."
        assert_no_leak(body["error"])

    def test_weak_password_keeps_the_policy_wording(self, client):
        """A long-but-weak password fails the policy validator, not min_length.

        `validate_password_strength` already raises sentences written for
        users; Pydantic surfaces them as `value_error` with a "Value error, "
        prefix, which the handler strips rather than replaces.
        """
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "frank@mail.com", "password": "passwordonly"},
        )

        assert response.status_code == 422
        body = response.json()
        assert body["error"] == "Password must contain at least one number."
        assert_no_leak(body["error"])

    def test_missing_field_is_readable(self, client):
        response = client.post("/api/v1/auth/login", json={})

        assert response.status_code == 422
        body = response.json()
        assert_no_leak(body["error"])

        messages = [e["message"] for e in body["details"]["errors"]]
        assert "Email address is required." in messages
        assert "Password is required." in messages

    def test_every_error_carries_field_and_message(self, client):
        """No entry may be missing the keys clients now depend on."""
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "not-an-email", "password": "x"},
        )

        assert response.status_code == 422
        errors = response.json()["details"]["errors"]
        assert len(errors) == 2

        for error in errors:
            assert error["field"]
            assert error["message"]
            assert error["message"].endswith(".")
            assert_no_leak(error["message"])

    def test_request_id_is_still_returned(self, client):
        """The envelope's existing keys are unchanged."""
        response = client.post("/api/v1/auth/login", json={})
        body = response.json()

        assert body["request_id"]
        assert body["status_code"] == 422
        assert set(body) == {
            "error",
            "error_code",
            "status_code",
            "request_id",
            "details",
        }
