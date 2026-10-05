#!/usr/bin/env bash
#
# The one command that decides whether a commit may ship.
#
# GitHub Actions runs exactly what this script runs (see .github/workflows/
# ci.yml), so "green locally" and "green in CI" are the same statement rather
# than two similar-looking lines in two files that slowly drift apart.
# tests/test_ci_pipeline.py fails the build if that pairing ever breaks.
#
#   bash tools/ci_check.sh                 # verify before pushing
#   bash tools/ci_check.sh --require-browser
#                                          # ...and refuse to skip the browser
#                                          # checks (what CI always does)
#   bash tools/ci_check.sh --ci            # the invocation CI uses
#
# Exit status is pytest's: 0 only when the whole suite passed.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

REQUIRE_BROWSER="${JA_REQUIRE_BROWSER:-0}"
for arg in "$@"; do
  case "$arg" in
    --ci|--require-browser) REQUIRE_BROWSER=1 ;;
    -h|--help)
      sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "ci_check.sh: unknown argument '$arg'" >&2
      exit 2
      ;;
  esac
done

# The interpreter the suite runs under. CI has no .venv-test; the sandbox does
# (system pip is PEP-668 blocked there), so prefer an explicit $PYTHON, then
# the scratch venv, then the ambient python3.
PY="${PYTHON:-}"
if [ -z "$PY" ] && [ -x "$ROOT/.venv-test/bin/python" ]; then
  PY="$ROOT/.venv-test/bin/python"
fi
if [ -z "$PY" ]; then
  PY="$(command -v python3 || command -v python)"
fi
if [ -z "$PY" ]; then
  echo "ci_check.sh: no python interpreter found" >&2
  exit 2
fi

if ! command -v node >/dev/null 2>&1; then
  echo "ci_check.sh: WARNING - node is not installed; the JS harness tests will skip." >&2
fi

export JA_REQUIRE_BROWSER="$REQUIRE_BROWSER"

echo "ci_check.sh: interpreter ${PY}"
echo "ci_check.sh: browser checks $([ "$REQUIRE_BROWSER" = "1" ] && echo required || echo optional)"
echo "ci_check.sh: python -m pytest tests/ -q -rs --durations=15 --junitxml=pytest-results.xml"
echo

# -rs prints why anything skipped: a silent skip is how a suite goes green
# while measuring nothing. --durations=15 keeps the slowest tests visible so a
# creeping timeout shows up as a number before it shows up as a failure.
set +e
"$PY" -m pytest tests/ -q -rs --durations=15 --junitxml=pytest-results.xml
STATUS=$?
set -e

echo
if [ "$STATUS" -eq 0 ]; then
  echo "ci_check.sh: PASS - the suite is green, this commit may ship."
else
  echo "ci_check.sh: FAIL (pytest exit $STATUS) - do not push; see pytest-results.xml." >&2
fi
exit "$STATUS"
