#!/usr/bin/env bash
# Fail if anything secret-shaped is committed.
#
# A cheap guard, not a replacement for a real scanner (gitleaks, trufflehog).
# It catches the mistakes that actually happen: a committed .env, a pasted API
# key, a private key file, a credential left in .env.example.
#
#   ./scripts/scan_secrets.sh
#
# Portable to bash 3.2 (macOS), so no mapfile and no associative arrays.

set -uo pipefail

RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; NC=$'\033[0m'
failures=0

fail() { printf '%sFAIL%s %s\n' "$RED" "$NC" "$1"; failures=$((failures + 1)); }
pass() { printf '%s ok %s %s\n' "$GREEN" "$NC" "$1"; }

# Files git would actually publish. Falls back to find outside a repo.
if git rev-parse --git-dir >/dev/null 2>&1; then
  FILE_LIST=$(git ls-files)
else
  FILE_LIST=$(find . -type f -not -path "./.git/*" -not -path "./.venv/*" | sed 's|^\./||')
fi

# The scanner's own patterns, and CI's example keys, would match themselves.
SELF_EXCLUDE='^(scripts/scan_secrets\.sh|\.github/workflows/)'
SCANNABLE=$(printf '%s\n' "$FILE_LIST" | grep -vE "$SELF_EXCLUDE")

# --- 1. No environment file may be tracked --------------------------------
hits=$(printf '%s\n' "$FILE_LIST" | grep -E '(^|/)\.env($|\.)' | grep -v '\.env\.example$')
if [ -n "$hits" ]; then
  fail "an environment file is tracked:"$'\n'"$hits"
else
  pass "no environment file is tracked"
fi

# --- 2. No key or certificate material may be tracked ---------------------
hits=$(printf '%s\n' "$FILE_LIST" | grep -E '\.(pem|key|p12|pfx|jks|keystore)$')
if [ -n "$hits" ]; then
  fail "key or certificate files are tracked:"$'\n'"$hits"
else
  pass "no key or certificate files are tracked"
fi

# --- 3. No secret-shaped strings in file contents -------------------------
# Patterns target credential *formats*, not keywords, so prose that merely
# mentions an API key does not trip them.
PATTERNS='sk-[A-Za-z0-9_-]{20,}
AKIA[0-9A-Z]{16}
ASIA[0-9A-Z]{16}
AIza[0-9A-Za-z_-]{35}
ghp_[A-Za-z0-9]{36}
glpat-[A-Za-z0-9_-]{20}
xox[baprs]-[A-Za-z0-9-]{10,}
GOCSPX-[A-Za-z0-9_-]{20,}
-----BEGIN [A-Z ]*PRIVATE KEY-----
eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.
(postgres|postgresql|mysql|mongodb)(\+[a-z]+)?://[^:/@[:space:]{$]+:[^@[:space:]{$]+@'

content_failures=0
while IFS= read -r pattern; do
  [ -z "$pattern" ] && continue
  hits=""
  while IFS= read -r file; do
    [ -z "$file" ] && continue
    [ -f "$file" ] || continue
    match=$(grep -HInE "$pattern" "$file" 2>/dev/null)
    [ -n "$match" ] && hits="${hits}${match}"$'\n'
  done <<EOF
$SCANNABLE
EOF
  if [ -n "$hits" ]; then
    fail "secret-shaped string matching /$pattern/:"$'\n'"$hits"
    content_failures=$((content_failures + 1))
  fi
done <<EOF
$PATTERNS
EOF
[ "$content_failures" -eq 0 ] && pass "no secret-shaped strings in tracked files"

# --- 4. Secret-named variables in .env.example must be empty --------------
# Non-secret defaults (ports, model names, localhost URLs) are fine and useful;
# only variables whose *name* says "credential" must ship blank.
if [ -f .env.example ]; then
  # MAX_TOKENS and friends are counts, not credentials.
  hits=$(grep -E '^[A-Z][A-Z0-9_]*(KEY|SECRET|PASSWORD|TOKEN|CREDENTIAL|PASSWD)[A-Z0-9_]*=.+' .env.example \
    | grep -vE '^[A-Z0-9_]*MAX_TOKENS=')
  if [ -n "$hits" ]; then
    fail ".env.example ships a value for a credential variable:"$'\n'"$hits"
  else
    pass ".env.example ships no credential values"
  fi
fi

echo
if [ "$failures" -gt 0 ]; then
  printf '%s%d check(s) failed.%s\n' "$RED" "$failures" "$NC"
  exit 1
fi
printf '%sAll secret checks passed.%s\n' "$GREEN" "$NC"
