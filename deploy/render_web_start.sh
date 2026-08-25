#!/bin/sh
set -eu

: "${API_HOSTPORT:?Render must provide API_HOSTPORT from the private API service}"

export API_URL="http://${API_HOSTPORT}"
exec node server.js
