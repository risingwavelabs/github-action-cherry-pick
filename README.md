# Cherry-Pick Github Action

Github Action to Cherry Pick commits from a branch (generally, master) and create a PR 
on another branch (Release branch).

Most of the projects have Release branches and master branch. Master branch is where
developers works on but we want to push the changes to the Release branches too. 


## Action

* Push to the monitored branch X
* Get the last commit SHA
* Checkout the other branch, Y
* Create a new pr branch Z on the branch Y
* Cherry Pick the commits from X into Z
* If the cherry-pick succeeds, publish the branch through the Git Data API and create the PR on base Y
* If the cherry-pick conflicts, create an issue with conflict-resolution instructions and a trusted marker that Jarvis can discover
* PR title will be prefixed with `AUTO`

#### Conditions:
* If base branch commit contains `AUTO`, it wont recreate the PR.

## Inputs

#### `pr_branch`

**Required** The branch name of on which PR should be created from the cherry-pick commit. 

#### `pr_labels`

CSV Labels to apply on the PR created. Default: `autocreated`

#### `commit_sha`

The specific commit SHA to cherry-pick. If not provided, it defaults to the triggering commit (`GITHUB_SHA`).

#### `source_pr_number`

The original pull request number. It is written into the conflict issue's Jarvis marker. For compatibility, the action can also extract it from a `pr_body` containing `Cherry picking #N onto branch ...`. If neither is available, the action creates a human-only conflict issue without a Jarvis marker.

Jarvis discovery also requires the issue creator and configured label to match its trusted policy. The action intentionally does not assign the Jarvis GitHub App as an issue assignee; a regular GitHub App bot is not an assignable coding agent.

## Jarvis rollout

For RisingWave, conflict issues must be authored by `risingwave-ci` and carry the
`cherry-pick` label. Deploy the Jarvis conflict consumer before enabling
`JARVIS_CHERRY_PICK_AUTO_PR_ENABLED=true` in its runtime configuration. The consumer
also recognizes the legacy conflict issue template, so existing open issues do not
need to be recreated. It skips issues with linked open or merged backport PRs.

A successful action run confirms that the backport PR or conflict issue was created;
it does not confirm that Jarvis accepted or completed the task. Verify consumer
discovery logs, the task acceptance comment, and the resulting release-branch PR
separately.

## Tokens

`GITHUB_TOKEN` is used to create pull requests and issues. Branches are published with
the Git Data REST API using `GIT_DATA_TOKEN` when it is set, or `GITHUB_TOKEN` as a
fallback. The publication token needs `contents: write` permission. If the cherry-pick
changes a workflow file, it also needs permission to create or update workflows.

`GITBOT_EMAIL` optionally overrides the committer email. When it is unset, the action
uses the triggering actor's GitHub noreply address.

Using the Git Data API avoids GitHub's server-side workflow scan on `git push` while
preserving the exact tree produced by the local cherry-pick. The action verifies the
remote tree SHA before it creates the branch reference.

## Tests

Run `make test` to build the action image and run its unit and entrypoint regression
tests in a disposable container with networking disabled. The entrypoint tests mock
GitHub and Git commands; they never create remote issues, PRs, or branches. They are
skipped outside the test container because the real entrypoint writes Git credentials.

## Example usage

In this example, all the merges to the branch `0.1.0` will create a PR on `0.1.X` branch too. 

```yaml
name: PR for release branch
on:
  push:
    branches:
      - 0.1.0
jobs:
  release_pull_request:
    runs-on: ubuntu-latest
    name: release_pull_request
    steps:
    - name: checkout
      uses: actions/checkout@v1
    - name: Create PR to branch
      uses: gorillio/github-action-cherry-pick@master
      with:
        pr_branch: '0.1.x'
      env:
        GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        # Optional: use a separate token for publishing Git objects and the branch.
        GIT_DATA_TOKEN: ${{ github.token }}
        # Optional: override the committer email.
        GITBOT_EMAIL: <BOT_EMAIL>
        DRY_RUN: false
```
