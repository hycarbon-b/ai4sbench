"""Independent OpenAPI contracts for the versioned API areas."""

from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute


def install_scoped_docs(
    app: FastAPI,
    *,
    ec2_routes: list[APIRoute],
    v2_routes: list[APIRoute],
    community_routes: list[APIRoute],
    mailing_routes: list[APIRoute],
) -> None:
    scopes = {
        "ec2/v1": ("AI4S-Bench EC2 Control Panel v1 API", "1.0.0", ec2_routes),
        "ec2/v2": ("AI4S-Bench Harbor EC2 v2 API", "2.0.0", v2_routes),
        "community/v1": ("AI4S-Bench Community v1 API", "1.0.0", community_routes),
        "mailing/v1": ("AI4S-Bench Mailing v1 API", "1.0.0", mailing_routes),
    }
    for scope, (title, version, routes) in scopes.items():
        _install_scope(app, scope, title, version, routes)


def _install_scope(app: FastAPI, scope: str, title: str, version: str, routes: list[APIRoute]) -> None:
    schema_path = f"/openapi/{scope}.json"
    docs_path = f"/docs/{scope}"
    cache: dict[str, object] = {}

    def schema() -> JSONResponse:
        if "value" not in cache:
            used_tags = {tag for route in routes for tag in (route.tags or [])}
            cache["value"] = get_openapi(
                title=title,
                version=version,
                description=f"Version {version.split('.')[0]} API contract.",
                routes=routes,
                tags=[tag for tag in app.openapi_tags or [] if tag["name"] in used_tags],
            )
        return JSONResponse(cache["value"])

    def docs():
        return get_swagger_ui_html(
            openapi_url=schema_path,
            title=f"{title} - Swagger UI",
            swagger_ui_parameters=app.swagger_ui_parameters,
        )

    app.add_api_route(schema_path, schema, methods=["GET"], include_in_schema=False)
    app.add_api_route(docs_path, docs, methods=["GET"], include_in_schema=False)
