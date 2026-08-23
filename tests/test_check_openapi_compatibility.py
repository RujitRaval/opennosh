from __future__ import annotations

import unittest

from scripts.check_openapi_compatibility import breaking_changes


def contract(
    *,
    properties: dict[str, object] | None = None,
    required: list[str] | None = None,
    enum: list[str] | None = None,
) -> dict[str, object]:
    schema: dict[str, object] = {
        "type": "object",
        "properties": properties or {"id": {"type": "string"}},
        "required": required or ["id"],
    }
    if enum is not None:
        schema["properties"] = {"state": {"type": "string", "enum": enum}}
        schema["required"] = ["state"]
    return {
        "info": {"x-opennosh-contract-version": "1.0.0"},
        "paths": {
            "/foods": {
                "get": {
                    "responses": {
                        "200": {"description": "OK"},
                        "422": {"description": "Problem"},
                    }
                }
            }
        },
        "components": {"schemas": {"Food": schema}},
    }


class CompatibilityTests(unittest.TestCase):
    def test_additive_property_is_compatible(self) -> None:
        previous = contract()
        current = contract(
            properties={"id": {"type": "string"}, "name": {"type": "string"}},
            required=["id"],
        )

        self.assertEqual(breaking_changes(previous, current), [])

    def test_removed_operation_and_response_are_breaking(self) -> None:
        previous = contract()
        current = contract()
        current["paths"] = {}

        self.assertIn("operation path removed: /foods", breaking_changes(previous, current))

    def test_new_required_property_is_breaking(self) -> None:
        previous = contract()
        current = contract(
            properties={"id": {"type": "string"}, "name": {"type": "string"}},
            required=["id", "name"],
        )

        self.assertIn(
            "schema Food: existing consumers now require property: name",
            breaking_changes(previous, current),
        )

    def test_removed_enum_member_is_breaking(self) -> None:
        previous = contract(enum=["draft", "published"])
        current = contract(enum=["published"])

        self.assertIn(
            "schema Food.state: enum value removed: draft",
            breaking_changes(previous, current),
        )


if __name__ == "__main__":
    unittest.main()
