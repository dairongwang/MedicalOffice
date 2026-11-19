#!/bin/sh
# Writes the runtime configuration (API origin) into config.js at container start.
set -e
cat > /usr/share/nginx/html/config.js <<JS
// Generated at container start from API_BASE / SHOW_DUMMY_HINT
window.ADMIN_CONFIG = { apiBase: "${API_BASE}", showDummyHint: ${SHOW_DUMMY_HINT:-false} };
JS
