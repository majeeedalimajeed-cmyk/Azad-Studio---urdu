#!/usr/bin/env bash
# Configure Git HTTPS authentication from an environment variable or a 600-mode file.
# Never pass a token as a command-line argument or write it into the repository.
set -euo pipefail

REPO_DIR="${REPO_DIR:-/home/user/nr_news}"
CRED_FILE="${CRED_FILE:-/home/user/.git-credentials}"
REMOTE_URL="${REMOTE_URL:-https://github.com/majeeedalimajeed-cmyk/Azad-Studio---urdu.git}"

token="${GITHUB_TOKEN:-${GH_PAT:-}}"
if [[ -z "$token" && -n "${GITHUB_TOKEN_FILE:-}" ]]; then
  token="$(<"$GITHUB_TOKEN_FILE")"
fi
if [[ -z "$token" && -n "${GH_PAT_FILE:-}" ]]; then
  token="$(<"$GH_PAT_FILE")"
fi
if [[ -z "$token" ]]; then
  echo "Set GITHUB_TOKEN or GH_PAT, or GITHUB_TOKEN_FILE / GH_PAT_FILE." >&2
  exit 2
fi

cd "$REPO_DIR"
mkdir -p "$(dirname "$CRED_FILE")"
umask 077
printf 'https://x-access-token:%s@github.com\n' "$token" > "$CRED_FILE"
chmod 600 "$CRED_FILE"

git config credential.helper "store --file=$CRED_FILE"
git config user.name "AZAD STUDIO newsroom"
git config user.email "newsroom@azadstudio.local"
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$REMOTE_URL"
else
  git remote add origin "$REMOTE_URL"
fi

echo "Configured Git HTTPS authentication from a secret source. Credential file: $CRED_FILE (mode 600)."
echo "Remote: $(git remote get-url origin)"
