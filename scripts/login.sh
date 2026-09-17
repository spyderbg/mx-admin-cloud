#!/usr/bin/env bash

: "${KIDDO_WORDS_USERNAME:?KIDDO_WORDS_USERNAME environment variable is required}"
: "${KIDDO_WORDS_PASSWORD:?KIDDO_WORDS_PASSWORD environment variable is required}"

response=$(curl -s -X 'POST' \
  'http://127.0.0.1:8000/api/v1/login' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "grant_type=password" \
  --data-urlencode "username=${KIDDO_WORDS_USERNAME}" \
  --data-urlencode "password=${KIDDO_WORDS_PASSWORD}" \
  --data-urlencode "scope=" \
  --data-urlencode "client_id=string" \
  --data-urlencode "client_secret=********")

token=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

export KIDDO_WORDS_TOKEN="$token"
echo "KIDDO_WORDS_TOKEN set"
