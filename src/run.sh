#! /bin/bash
GUNICORN="../.venv/bin/gunicorn"

echo "Launching on port 5475"
echo "${GUNICORN} App:app --bind 0.0.0.0:5475"
${GUNICORN} App:app --bind 0.0.0.0:5475

