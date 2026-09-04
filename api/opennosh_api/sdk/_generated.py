"""Generated from the developer compatibility manifest and OpenAPI. Do not edit."""

from __future__ import annotations

from typing import Any, Final

PUBLIC_OPERATION_POLICIES: Final[dict[str, dict[str, Any]]] = {
    "/api/v1/foods/capabilities": {
        "accepted_media_types": ["application/json"],
        "max_response_bytes": 2097152,
        "media_type": "application/json",
        "path_parameters": {},
    },
    "/api/v1/foods/search": {
        "accepted_media_types": ["application/json"],
        "max_response_bytes": 2097152,
        "media_type": "application/json",
        "path_parameters": {},
    },
    "/api/v1/public/commons-snapshot": {
        "accepted_media_types": ["application/json"],
        "max_response_bytes": 24576,
        "media_type": "application/json",
        "path_parameters": {},
    },
    "/api/v1/public/foods/{source}/{source_id}": {
        "accepted_media_types": ["application/json"],
        "max_response_bytes": 524288,
        "media_type": "application/json",
        "path_parameters": {
            "source": {"enum": ["usda", "community"], "title": "Source", "type": "string"},
            "source_id": {
                "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$",
                "title": "Source Id",
                "type": "string",
            },
        },
    },
    "/api/v1/public/impact": {
        "accepted_media_types": ["application/json"],
        "max_response_bytes": 524288,
        "media_type": "application/json",
        "path_parameters": {},
    },
    "/api/v1/public/incidents": {
        "accepted_media_types": ["application/json"],
        "max_response_bytes": 524288,
        "media_type": "application/json",
        "path_parameters": {},
    },
    "/api/v1/public/missions": {
        "accepted_media_types": ["application/json"],
        "max_response_bytes": 2097152,
        "media_type": "application/json",
        "path_parameters": {},
    },
    "/api/v1/public/missions/activity": {
        "accepted_media_types": ["application/json"],
        "max_response_bytes": 2097152,
        "media_type": "application/json",
        "path_parameters": {},
    },
    "/api/v1/public/releases/{release_version}/foods/{source}/{source_id}": {
        "accepted_media_types": ["application/json"],
        "max_response_bytes": 524288,
        "media_type": "application/json",
        "path_parameters": {
            "release_version": {
                "pattern": "^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+$",
                "title": "Release Version",
                "type": "string",
            },
            "source": {"enum": ["usda", "community"], "title": "Source", "type": "string"},
            "source_id": {
                "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$",
                "title": "Source Id",
                "type": "string",
            },
        },
    },
    "/api/v1/public/releases/{release_version}/foods/{source}/{source_id}/provenance": {
        "accepted_media_types": ["text/html"],
        "max_response_bytes": 2097152,
        "media_type": "text/html",
        "path_parameters": {
            "release_version": {
                "pattern": "^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+$",
                "title": "Release Version",
                "type": "string",
            },
            "source": {"enum": ["usda", "community"], "title": "Source", "type": "string"},
            "source_id": {
                "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$",
                "title": "Source Id",
                "type": "string",
            },
        },
    },
    "/api/v1/public/releases/{release_version}/manifest": {
        "accepted_media_types": ["application/vnd.opennosh.release+json"],
        "max_response_bytes": 8388608,
        "media_type": "application/vnd.opennosh.release+json",
        "path_parameters": {
            "release_version": {
                "pattern": "^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+$",
                "title": "Release Version",
                "type": "string",
            }
        },
    },
    "/api/v1/public/releases/{release_version}/packs/{pack_id}/{pack_version}/download": {
        "accepted_media_types": ["application/vnd.opennosh.pack+zip", "application/zip"],
        "max_response_bytes": 67108864,
        "media_type": "application/zip",
        "path_parameters": {
            "pack_id": {
                "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$",
                "title": "Pack Id",
                "type": "string",
            },
            "pack_version": {
                "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$",
                "title": "Pack Version",
                "type": "string",
            },
            "release_version": {
                "pattern": "^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+$",
                "title": "Release Version",
                "type": "string",
            },
        },
    },
    "/api/v1/public/reuse": {
        "accepted_media_types": ["application/json"],
        "max_response_bytes": 2097152,
        "media_type": "application/json",
        "path_parameters": {},
    },
    "/api/v1/public/reuse/dependencies": {
        "accepted_media_types": ["application/json"],
        "max_response_bytes": 524288,
        "media_type": "application/json",
        "path_parameters": {},
    },
    "/api/v1/public/reuse/{declaration_id}": {
        "accepted_media_types": ["application/json"],
        "max_response_bytes": 524288,
        "media_type": "application/json",
        "path_parameters": {
            "declaration_id": {"format": "uuid", "title": "Declaration Id", "type": "string"}
        },
    },
    "/api/v1/public/status": {
        "accepted_media_types": ["application/json"],
        "max_response_bytes": 524288,
        "media_type": "application/json",
        "path_parameters": {},
    },
}
