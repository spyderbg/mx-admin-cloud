#!/usr/bin/env bash

response=$(curl -s -X 'POST' \
  'http://127.0.0.1:8000/api/v1/login' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'grant_type=password&username=admin&password=MarXman2025&scope=&client_id=string&client_secret=********')

token=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

export KIDDO_WORDS_TOKEN="$token"
echo "KIDDO_WORDS_TOKEN set"
