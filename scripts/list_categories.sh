#!/usr/bin/env bash

curl -X 'GET' \
  'http://127.0.0.1:8000/api/v1/content/categories?include_archived=false&archived_only=false' \
  -H 'accept: application/json' \
  -H "Authorization: Bearer $KIDDO_WORDS_TOKEN" | jq
