// Generated from the developer compatibility manifest and OpenAPI. Do not edit.
export const PUBLIC_OPERATION_POLICIES = Object.freeze({
  "/api/v1/foods/capabilities": {
    "acceptedMediaTypes": [
      "application/json"
    ],
    "mediaType": "application/json",
    "maxResponseBytes": 2097152,
    "pathParameters": {}
  },
  "/api/v1/foods/search": {
    "acceptedMediaTypes": [
      "application/json"
    ],
    "mediaType": "application/json",
    "maxResponseBytes": 2097152,
    "pathParameters": {}
  },
  "/api/v1/public/commons-snapshot": {
    "acceptedMediaTypes": [
      "application/json"
    ],
    "mediaType": "application/json",
    "maxResponseBytes": 24576,
    "pathParameters": {}
  },
  "/api/v1/public/foods/{source}/{source_id}": {
    "acceptedMediaTypes": [
      "application/json"
    ],
    "mediaType": "application/json",
    "maxResponseBytes": 524288,
    "pathParameters": {
      "source": {
        "enum": [
          "usda",
          "community"
        ],
        "title": "Source",
        "type": "string"
      },
      "source_id": {
        "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$",
        "title": "Source Id",
        "type": "string"
      }
    }
  },
  "/api/v1/public/missions": {
    "acceptedMediaTypes": [
      "application/json"
    ],
    "mediaType": "application/json",
    "maxResponseBytes": 2097152,
    "pathParameters": {}
  },
  "/api/v1/public/missions/activity": {
    "acceptedMediaTypes": [
      "application/json"
    ],
    "mediaType": "application/json",
    "maxResponseBytes": 2097152,
    "pathParameters": {}
  },
  "/api/v1/public/reuse": {
    "acceptedMediaTypes": [
      "application/json"
    ],
    "mediaType": "application/json",
    "maxResponseBytes": 2097152,
    "pathParameters": {}
  },
  "/api/v1/public/reuse/{declaration_id}": {
    "acceptedMediaTypes": [
      "application/json"
    ],
    "mediaType": "application/json",
    "maxResponseBytes": 524288,
    "pathParameters": {
      "declaration_id": {
        "format": "uuid",
        "title": "Declaration Id",
        "type": "string"
      }
    }
  },
  "/api/v1/public/releases/{release_version}/foods/{source}/{source_id}": {
    "acceptedMediaTypes": [
      "application/json"
    ],
    "mediaType": "application/json",
    "maxResponseBytes": 524288,
    "pathParameters": {
      "release_version": {
        "pattern": "^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+$",
        "title": "Release Version",
        "type": "string"
      },
      "source": {
        "enum": [
          "usda",
          "community"
        ],
        "title": "Source",
        "type": "string"
      },
      "source_id": {
        "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$",
        "title": "Source Id",
        "type": "string"
      }
    }
  },
  "/api/v1/public/releases/{release_version}/foods/{source}/{source_id}/provenance": {
    "acceptedMediaTypes": [
      "text/html"
    ],
    "mediaType": "text/html",
    "maxResponseBytes": 2097152,
    "pathParameters": {
      "release_version": {
        "pattern": "^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+$",
        "title": "Release Version",
        "type": "string"
      },
      "source": {
        "enum": [
          "usda",
          "community"
        ],
        "title": "Source",
        "type": "string"
      },
      "source_id": {
        "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$",
        "title": "Source Id",
        "type": "string"
      }
    }
  },
  "/api/v1/public/releases/{release_version}/manifest": {
    "acceptedMediaTypes": [
      "application/vnd.opennosh.release+json"
    ],
    "mediaType": "application/vnd.opennosh.release+json",
    "maxResponseBytes": 8388608,
    "pathParameters": {
      "release_version": {
        "pattern": "^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+$",
        "title": "Release Version",
        "type": "string"
      }
    }
  },
  "/api/v1/public/releases/{release_version}/packs/{pack_id}/{pack_version}/download": {
    "acceptedMediaTypes": [
      "application/vnd.opennosh.pack+zip",
      "application/zip"
    ],
    "mediaType": "application/zip",
    "maxResponseBytes": 67108864,
    "pathParameters": {
      "release_version": {
        "pattern": "^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+$",
        "title": "Release Version",
        "type": "string"
      },
      "pack_id": {
        "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$",
        "title": "Pack Id",
        "type": "string"
      },
      "pack_version": {
        "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$",
        "title": "Pack Version",
        "type": "string"
      }
    }
  }
});
