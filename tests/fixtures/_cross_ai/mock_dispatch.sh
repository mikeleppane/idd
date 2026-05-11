#!/usr/bin/env bash
# Mock CLI for cross-AI dispatch tests. Behavior selected via MOCK_CLI_RESPONSE env.
set -euo pipefail
case "${MOCK_CLI_RESPONSE:-clean}" in
  clean)         cat "$(dirname "$0")/canned_response_clean.md" ;;
  with-findings) cat "$(dirname "$0")/canned_response_with_findings.md" ;;
  timeout)       sleep 999 ;;
  fail)          exit 1 ;;
  *)             echo "unknown MOCK_CLI_RESPONSE: ${MOCK_CLI_RESPONSE}" >&2; exit 2 ;;
esac
