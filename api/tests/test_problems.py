from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from fastapi.testclient import TestClient
from opennosh_api.contracts import common_problem_responses, install_openapi_contract
from opennosh_api.main import create_app
from opennosh_api.problems import RequestIdMiddleware, install_problem_handlers
from opennosh_api.problems.schemas import ProblemCode
from opennosh_api.settings import Settings


def contract_app() -> FastAPI:
    application = FastAPI(responses=common_problem_responses())
    application.add_middleware(RequestIdMiddleware)
    install_problem_handlers(application)

    @application.get("/protected")
    async def protected() -> None:
        raise HTTPException(status_code=401, detail="Authentication required")

    @application.get("/validate")
    async def validate(value: int = Query(ge=1)) -> dict[str, int]:
        return {"value": value}

    @application.get("/busy")
    async def busy() -> None:
        raise HTTPException(
            status_code=503,
            detail="Try again later.",
            headers={"Retry-After": "60"},
        )

    @application.get("/long-detail")
    async def long_detail() -> None:
        raise HTTPException(status_code=400, detail="x" * 800)

    @application.get("/boom")
    async def boom() -> None:
        raise RuntimeError("database password must never be returned")

    install_openapi_contract(application)
    return application


def test_http_exception_uses_problem_json_and_request_reference() -> None:
    with TestClient(contract_app()) as client:
        response = client.get("/protected", headers={"X-Request-ID": "caller-controlled"})

    body = response.json()
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["x-request-id"] == body["request_id"]
    assert body == {
        "type": "https://opennosh.org/problems/authentication-required",
        "title": "Authentication required",
        "status": 401,
        "detail": "Authentication required",
        "code": "authentication_required",
        "schema_version": "1.0",
        "request_id": body["request_id"],
        "recovery_actions": [{"id": "sign_in", "label": "Sign in", "href": "/tracker"}],
    }
    assert body["request_id"] != "caller-controlled"


def test_validation_problem_has_safe_field_errors() -> None:
    secret_input = "private-token-value"
    with TestClient(contract_app()) as client:
        response = client.get("/validate", params={"value": secret_input})

    body = response.json()
    assert response.status_code == 422
    assert body["code"] == "validation_failed"
    assert body["field_errors"] == [
        {
            "pointer": "/query/value",
            "code": "int_parsing",
            "message": "Enter a whole number.",
        }
    ]
    assert secret_input not in response.text


def test_retry_after_is_typed_and_recoverable() -> None:
    with TestClient(contract_app()) as client:
        response = client.get("/busy")

    body = response.json()
    assert body["code"] == "rate_limited"
    assert body["retry_after"] == 60
    assert body["recovery_actions"] == [{"id": "retry", "label": "Try again"}]
    assert response.headers["retry-after"] == "60"


def test_oversized_expected_detail_is_capped_to_the_schema() -> None:
    with TestClient(contract_app()) as client:
        response = client.get("/long-detail")

    assert response.status_code == 400
    assert len(response.json()["detail"]) == 500


def test_unexpected_exception_never_leaks_internal_details() -> None:
    with TestClient(contract_app(), raise_server_exceptions=False) as client:
        response = client.get("/boom")

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert "password" not in response.text


def test_every_problem_code_has_a_documented_type_and_title() -> None:
    schema = contract_app().openapi()
    enum_values = schema["components"]["schemas"]["ProblemCode"]["enum"]

    assert set(enum_values) == {code.value for code in ProblemCode}
    for operation in schema["paths"].values():
        for description in operation.values():
            if not isinstance(description, dict) or "responses" not in description:
                continue
            for status, response in description["responses"].items():
                if status in {
                    "400",
                    "401",
                    "403",
                    "404",
                    "409",
                    "422",
                    "429",
                    "500",
                    "502",
                    "503",
                    "504",
                }:
                    assert "application/problem+json" in response["content"]


def test_application_openapi_versions_success_and_problem_contracts() -> None:
    application = create_app(
        Settings(database_url="postgresql+asyncpg://unused:unused@localhost/unused")
    )
    schema = application.openapi()

    assert schema["info"]["x-opennosh-contract-version"] == "2.0.0"
    search = schema["paths"]["/api/v1/foods/search"]["get"]
    assert "application/problem+json" in search["responses"]["422"]["content"]
    assert (
        search["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/FoodSearchResponse"
    )
    assert (
        schema["components"]["schemas"]["FoodSearchResponse"]["properties"]["schema_version"][
            "const"
        ]
        == "2.0"
    )
    assert "application/json" in schema["paths"]["/healthz"]["get"]["responses"]["503"]["content"]

    for component in ("FoodDetail", "CustomFoodResponse", "OpenFoodFactsFood"):
        component_schema = schema["components"]["schemas"][component]
        assert component_schema["properties"]["schema_version"]["const"] == "1.0"
        assert component_schema["properties"]["portions"]["items"] == {
            "$ref": "#/components/schemas/HouseholdPortion-Output"
        }
