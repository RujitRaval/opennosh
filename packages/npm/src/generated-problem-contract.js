// Generated from web/lib/generated/openapi.json. Do not edit.
export const PROBLEM_SCHEMAS = Object.freeze({
  "FieldError": {
    "additionalProperties": false,
    "properties": {
      "code": {
        "maxLength": 80,
        "minLength": 1,
        "pattern": "^[a-z0-9_]+$",
        "title": "Code",
        "type": "string"
      },
      "message": {
        "maxLength": 240,
        "minLength": 1,
        "title": "Message",
        "type": "string"
      },
      "pointer": {
        "pattern": "^/(?:[^/~]|~[01])+(?:/(?:[^/~]|~[01])+)*$",
        "title": "Pointer",
        "type": "string"
      }
    },
    "required": [
      "pointer",
      "code",
      "message"
    ],
    "title": "FieldError",
    "type": "object"
  },
  "LatestStateReference": {
    "additionalProperties": false,
    "properties": {
      "resource_id": {
        "maxLength": 160,
        "minLength": 1,
        "title": "Resource Id",
        "type": "string"
      },
      "resource_type": {
        "maxLength": 80,
        "minLength": 1,
        "pattern": "^[a-z][a-z0-9_]*$",
        "title": "Resource Type",
        "type": "string"
      },
      "version": {
        "maxLength": 80,
        "minLength": 1,
        "title": "Version",
        "type": "string"
      }
    },
    "required": [
      "resource_type",
      "resource_id",
      "version"
    ],
    "title": "LatestStateReference",
    "type": "object"
  },
  "ProblemCode": {
    "enum": [
      "invalid_request",
      "authentication_required",
      "authorization_denied",
      "resource_not_found",
      "conflict",
      "validation_failed",
      "rate_limited",
      "upstream_unavailable",
      "service_unavailable",
      "database_capacity_exhausted",
      "internal_error",
      "search_cursor_invalid",
      "search_cursor_restart",
      "evidence_upload_conflict",
      "evidence_upload_expired",
      "evidence_upload_unavailable"
    ],
    "title": "ProblemCode",
    "type": "string"
  },
  "ProblemDetails": {
    "additionalProperties": false,
    "properties": {
      "code": {
        "$ref": "#/components/schemas/ProblemCode"
      },
      "detail": {
        "maxLength": 500,
        "minLength": 1,
        "title": "Detail",
        "type": "string"
      },
      "field_errors": {
        "anyOf": [
          {
            "items": {
              "$ref": "#/components/schemas/FieldError"
            },
            "maxItems": 100,
            "type": "array"
          },
          {
            "type": "null"
          }
        ],
        "title": "Field Errors"
      },
      "latest_state": {
        "anyOf": [
          {
            "$ref": "#/components/schemas/LatestStateReference"
          },
          {
            "type": "null"
          }
        ]
      },
      "recovery_actions": {
        "anyOf": [
          {
            "items": {
              "$ref": "#/components/schemas/RecoveryAction"
            },
            "maxItems": 8,
            "type": "array"
          },
          {
            "type": "null"
          }
        ],
        "title": "Recovery Actions"
      },
      "request_id": {
        "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        "title": "Request Id",
        "type": "string"
      },
      "retry_after": {
        "anyOf": [
          {
            "maximum": 86400,
            "minimum": 1,
            "type": "integer"
          },
          {
            "type": "null"
          }
        ],
        "title": "Retry After"
      },
      "schema_version": {
        "const": "1.0",
        "default": "1.0",
        "title": "Schema Version",
        "type": "string"
      },
      "status": {
        "maximum": 599,
        "minimum": 400,
        "title": "Status",
        "type": "integer"
      },
      "title": {
        "maxLength": 120,
        "minLength": 1,
        "title": "Title",
        "type": "string"
      },
      "type": {
        "pattern": "^https://opennosh\\.org/problems/[a-z0-9-]+$",
        "title": "Type",
        "type": "string"
      }
    },
    "required": [
      "type",
      "title",
      "status",
      "detail",
      "code",
      "request_id"
    ],
    "title": "ProblemDetails",
    "type": "object"
  },
  "RecoveryAction": {
    "additionalProperties": false,
    "properties": {
      "href": {
        "anyOf": [
          {
            "pattern": "^/(?:$|[^/\\x00][^\\x00]*)$",
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "Href"
      },
      "id": {
        "enum": [
          "retry",
          "sign_in",
          "reload",
          "review_fields",
          "restart_search"
        ],
        "title": "Id",
        "type": "string"
      },
      "label": {
        "maxLength": 120,
        "minLength": 1,
        "title": "Label",
        "type": "string"
      }
    },
    "required": [
      "id",
      "label"
    ],
    "title": "RecoveryAction",
    "type": "object"
  }
});
