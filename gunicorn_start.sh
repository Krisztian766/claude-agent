#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
mkdir -p logs
exec gunicorn payment_server:app \
  --bind 127.0.0.1:8000 \
  --workers 2 \
  --access-logfile logs/gunicorn-access.log \
  --error-logfile logs/gunicorn-error.log
