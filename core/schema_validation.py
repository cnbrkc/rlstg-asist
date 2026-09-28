"""Runtime validation for structured model responses.

Gemini receives the same schema in its request configuration, but validating
locally makes the application boundary explicit and protects downstream code
from syntactically valid JSON with the wrong shape.
"""
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError


class StructuredOutputValidationError(ValueError):
    """Raised when a parsed model response does not match its JSON schema."""


def validate_structured_output(value, schema, label="Model"):
    """Validate a parsed JSON value without including model content in errors.

    Only the first validation error is reported. Its path and rule are enough
    for diagnostics, while omitting the rejected value avoids leaking generated
    or user-provided text into logs.
    """
    if not isinstance(schema, dict):
        raise TypeError("response_schema bir JSON Schema sözlüğü olmalı.")

    try:
        validator = Draft202012Validator(schema)
        validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError("Uygulama tarafındaki response_schema geçersiz.") from exc

    error = next(validator.iter_errors(value), None)
    if error is not None:
        path = ".".join(str(part) for part in error.absolute_path) or "$"
        rule = str(error.validator or "schema")
        raise StructuredOutputValidationError(
            f"{label} JSON yanıtı şemaya uymuyor: alan={path}, kural={rule}."
        )
    return value
