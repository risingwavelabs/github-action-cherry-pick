#!/bin/sh -l

git_setup() {
  cat <<- EOF > $HOME/.netrc
		machine github.com
		login $GITHUB_ACTOR
		password $GITHUB_TOKEN
		machine api.github.com
		login $GITHUB_ACTOR
		password $GITHUB_TOKEN
EOF
  chmod 600 $HOME/.netrc

  git config --global user.email "$GITBOT_EMAIL"
  git config --global user.name "$GITHUB_ACTOR"
  git config --global --add safe.directory /github/workspace
}

git_cmd() {
  if [[ "${DRY_RUN:-false}" == "true" ]]; then
    echo "This is a dry run. We just output the command:"
    echo $@
  else
    echo "This is NOT a dry run. We output and execute the command:"
    echo $@
    eval $@
  fi
}

echo "work around permission issue"
git config --global --add safe.directory /github/workspace

echo "INPUT_PR_BRANCH:$INPUT_PR_BRANCH"
echo "GITHUB_SHA:$GITHUB_SHA"

PR_BRANCH="auto-$INPUT_PR_BRANCH-$GITHUB_SHA"
echo "PR_BRANCH:$PR_BRANCH"
MESSAGE=$(git log -1 $GITHUB_SHA | grep "AUTO" | wc -l)
echo "MESSAGE:$MESSAGE"

if [[ $MESSAGE -gt 0 ]]; then
  echo "Autocommit, NO ACTION"
  exit 0
fi

LAST_COMMIT=$(git log -1)
echo "LAST COMMIT:$LAST_COMMIT"

PR_TITLE=$(git log -1 --format="%s" $GITHUB_SHA)
echo "PR_TITLE:$PR_TITLE"
echo "INPUT_PR_BODY:${INPUT_PR_BODY}"

# Add GITHUB_SHA to the PR/issue body
INPUT_PR_BODY="${INPUT_PR_BODY}\n\nThis PR/issue was created by cherry-pick action from commit ${GITHUB_SHA}."

git_setup
git_cmd git remote update
git_cmd git fetch --all
git_cmd git checkout -b "${PR_BRANCH}" origin/"${INPUT_PR_BRANCH}"
git_cmd git cherry-pick "${GITHUB_SHA}"
# Check the exit code of `git cherry-pick`
if [ $? -eq 0 ]; then
  echo "git cherry-pick succeeded. We will create a pull request for it."
  git_cmd git push -u origin "${PR_BRANCH}"
  git_cmd hub pull-request -b "${INPUT_PR_BRANCH}" -h "${PR_BRANCH}" -l "${INPUT_PR_LABELS}" -a "${GITHUB_ACTOR}" -m "'${PR_TITLE}'" -m "'${INPUT_PR_BODY}'" -r "${GITHUB_ACTOR}"
else
  echo "git cherry-pick failed. We will create an issue for it."
  git_cmd hub issue create -m "'cherrypick ${PR_TITLE} to branch ${INPUT_PR_BRANCH}'" -m "'${INPUT_PR_BODY}'" -a "${GITHUB_ACTOR}" -l "${INPUT_PR_LABELS}"
fi
