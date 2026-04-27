#!/bin/sh
# Container entrypoint:
#   1. Ensures /data/db and /data/results exist (volume is mounted at runtime,
#      so the dirs we made at build time get shadowed).
#   2. Materializes the auth config from the AUTH_CONFIG_YAML secret if set.
#   3. Launches Streamlit (or whatever command was passed in).
set -e

mkdir -p /data/db /data/results

if [ -n "$AUTH_CONFIG_YAML" ]; then
  echo "$AUTH_CONFIG_YAML" > /app/auth_config.yaml
  export MP_AUTH_CONFIG=/app/auth_config.yaml
  echo "[entrypoint] wrote auth config to $MP_AUTH_CONFIG"
fi

# If args were passed (e.g. `fly ssh console -C "/app/entrypoint.sh python ..."`),
# run those instead of streamlit. Otherwise default to the dashboard.
if [ "$#" -gt 0 ]; then
  exec "$@"
else
  exec streamlit run stock_screener/dashboard/app.py
fi
