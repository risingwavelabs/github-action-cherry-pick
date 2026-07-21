#!/bin/bash -l

git_setup() {
  cat <<- EOF > "$HOME"/.netrc
		machine github.com
		login $GITHUB_ACTOR
		password $GITHUB_TOKEN
		machine api.github.com
		login $GITHUB_ACTOR
		password $GITHUB_TOKEN
EOF
  chmod 600 "$HOME"/.netrc

  git config --global user.email "$GITBOT_EMAIL"
  git config --global user.name "$GITHUB_ACTOR"
  git config --global --add safe.directory /github/workspace
}

quote() { printf %s\\n "$1" | sed "s/'/'\\\\''/g;1s/^/'/;\$s/\$/'/" ; }

print_cmd() {
  for cmd in "$@"; do
    # If the quoted (%q) command contains a backslash, it means there
    # are characters needs to be escaped. In this case, we quote the string
    # with single quotes.
    if printf "%q" "$cmd" | grep -q "\\\\"; then
      printf "%s " "$(quote "$cmd")"
    else
      printf "%s " "$cmd"
    fi
  done
  printf "\n"
}

git_cmd() {
  # shellcheck disable=SC3010
  if [[ "${DRY_RUN:-false}" == "true" ]]; then
    echo "This is a dry run. We just output the command:" >&2
    print_cmd "$@" >&2
  else
    echo "This is NOT a dry run. We output and execute the command:" >&2
    print_cmd "$@" >&2
    "$@"
  fi
}

echo "work around permission issue"
git config --global --add safe.directory /github/workspace

# Determine which commit SHA to use - custom or default
if [[ -n "${INPUT_COMMIT_SHA}" ]]; then
  COMMIT_SHA="${INPUT_COMMIT_SHA}"
  echo "Using provided commit SHA: ${COMMIT_SHA}"
else
  COMMIT_SHA="${GITHUB_SHA}"
  echo "Using default GITHUB_SHA: ${COMMIT_SHA}"
fi

echo "INPUT_PR_BRANCH:$INPUT_PR_BRANCH"

PR_BRANCH="auto-$INPUT_PR_BRANCH-$COMMIT_SHA-$(date +%s)"
echo "PR_BRANCH:$PR_BRANCH"
MESSAGE=$(git log -1 "$COMMIT_SHA" | grep -c "AUTO")
echo "MESSAGE:$MESSAGE"

if [[ "$MESSAGE" -gt 0 ]]; then
  echo "Autocommit, NO ACTION"
  exit 0
fi

LAST_COMMIT=$(git log -1)
echo "LAST COMMIT:$LAST_COMMIT"

# fetch the commit in case of shallow clone
git_cmd git fetch origin "${COMMIT_SHA}" --depth=2

PR_TITLE=$(git log -1 --format="%s" "$COMMIT_SHA")
echo "PR_TITLE:$PR_TITLE"
echo "INPUT_PR_BODY:${INPUT_PR_BODY}"

# Prefer the explicit source PR input. Keep compatibility with existing callers
# whose body uses "Cherry picking #N onto branch ...".
SOURCE_PR_NUMBER="${INPUT_SOURCE_PR_NUMBER:-}"
# shellcheck disable=SC3010
if [[ -z "${SOURCE_PR_NUMBER}" && "${INPUT_PR_BODY}" =~ Cherry[[:space:]]picking[[:space:]]#([0-9]+)[[:space:]]onto[[:space:]]branch ]]; then
  SOURCE_PR_NUMBER="${BASH_REMATCH[1]}"
fi

# Add GITHUB_SHA to the PR/issue body
INPUT_PR_BODY=$(printf "%s\n\nThis PR/issue was created by cherry-pick action from commit %s.", "${INPUT_PR_BODY}", "${COMMIT_SHA}")

git_setup
git_cmd git remote update
git_cmd git fetch --all

# Check if the commit is already in the target branch
if git_cmd git branch -a --contains "${COMMIT_SHA}" | grep -q "remotes/origin/${INPUT_PR_BRANCH}$"; then
  echo "Commit ${COMMIT_SHA} already exists in branch ${INPUT_PR_BRANCH}, nothing to do"
  exit 0
fi

git_cmd git checkout -b "${PR_BRANCH}" origin/"${INPUT_PR_BRANCH}"
git_cmd git cherry-pick "${COMMIT_SHA}"

# Check the exit code of `git cherry-pick`
# shellcheck disable=SC2181
if [ $? -eq 0 ]; then
  echo "git cherry-pick succeeded. We will create a pull request for it."
  git_cmd git push -u origin "${PR_BRANCH}"
  git_cmd hub pull-request -b "${INPUT_PR_BRANCH}" -h "${PR_BRANCH}" -l "${INPUT_PR_LABELS}" -a "${GITHUB_ACTOR}" -m "${PR_TITLE}" -m "${INPUT_PR_BODY}" -r "${GITHUB_ACTOR}"
else
  echo "git cherry-pick failed. We will create an issue for it."
  CONFLICT_INSTRUCTIONS="## Instructions for Jarvis

Run \`git cherry-pick ${COMMIT_SHA}\` first, then resolve any conflicts. Avoid unnecessary improvements and keep the diff as close to the original commit as possible. Target branch: \`${INPUT_PR_BRANCH}\`."
  ISSUE_BODY=$(printf "%s\n\n%s" "${INPUT_PR_BODY}" "${CONFLICT_INSTRUCTIONS}")
  if [[ "${SOURCE_PR_NUMBER}" =~ ^[0-9]+$ ]]; then
    JARVIS_MARKER="<!-- jarvis-cherry-pick-conflict:v1 source_pr=${GITHUB_REPOSITORY}#${SOURCE_PR_NUMBER} commit=${COMMIT_SHA} target=${INPUT_PR_BRANCH} -->"
    ISSUE_BODY=$(printf "%s\n\n%s" "${ISSUE_BODY}" "${JARVIS_MARKER}")
  else
    echo "No source PR number was supplied or found in pr_body; creating a human-only conflict issue." >&2
  fi
  ISSUE_URL=$(git_cmd hub issue create -m "cherry-pick ${PR_TITLE} to branch ${INPUT_PR_BRANCH}" -m "${ISSUE_BODY}" -a "${GITHUB_ACTOR}" -l "${INPUT_PR_LABELS}")
  echo "$ISSUE_URL"
fi
