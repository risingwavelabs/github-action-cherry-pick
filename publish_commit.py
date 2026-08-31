#!/usr/bin/env python3

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request


API_VERSION = "2022-11-28"


def run_git(*args):
    return subprocess.check_output(("git", *args))


def decode_git_text(value, description):
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RuntimeError(f"{description} is not valid UTF-8") from error


def strip_output_terminator(value):
    return value[:-1] if value.endswith(b"\n") else value


def resolve_commit(revision):
    return run_git("rev-parse", "--verify", f"{revision}^{{commit}}").decode().strip()


def tree_entries(revision):
    entries = {}
    output = run_git("ls-tree", "-rz", revision)
    for record in output.rstrip(b"\0").split(b"\0") if output else ():
        metadata, path = record.split(b"\t", 1)
        mode, object_type, sha = metadata.split(b" ", 2)
        entries[path] = {
            "mode": mode.decode(),
            "type": object_type.decode(),
            "sha": sha.decode(),
        }
    return entries


def changed_paths(parent, commit):
    output = run_git(
        "diff-tree",
        "--no-commit-id",
        "--name-only",
        "--no-renames",
        "-r",
        "-z",
        parent,
        commit,
    )
    return output.rstrip(b"\0").split(b"\0") if output else []


def git_blob_sha(content):
    header = f"blob {len(content)}\0".encode()
    return hashlib.sha1(header + content).hexdigest()


def commit_identity(commit, prefix):
    placeholders = {
        "author": ("%an", "%ae", "%aI"),
        "committer": ("%cn", "%ce", "%cI"),
    }
    name_format, email_format, date_format = placeholders[prefix]

    def field(format_string):
        value = run_git("show", "-s", f"--format={format_string}", commit)
        return decode_git_text(strip_output_terminator(value), f"commit {prefix}")

    return {
        "name": field(name_format),
        "email": field(email_format),
        "date": field(date_format),
    }


class GitHubApi:
    def __init__(self, api_url, repository, token):
        owner, separator, repo = repository.partition("/")
        if not separator or not owner or not repo:
            raise RuntimeError("GITHUB_REPOSITORY must use the owner/repository format")

        quoted_repository = "/".join(
            urllib.parse.quote(part, safe="") for part in (owner, repo)
        )
        self.repository_url = f"{api_url.rstrip('/')}/repos/{quoted_repository}"
        self.token = token

    def request(self, method, endpoint, payload):
        request = urllib.request.Request(
            f"{self.repository_url}{endpoint}",
            data=json.dumps(payload).encode(),
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "github-action-cherry-pick",
                "X-GitHub-Api-Version": API_VERSION,
            },
        )
        try:
            with urllib.request.urlopen(request) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"GitHub API {method} {endpoint} failed with HTTP {error.code}: {body}"
            ) from error
        except urllib.error.URLError as error:
            raise RuntimeError(
                f"GitHub API {method} {endpoint} failed: {error.reason}"
            ) from error

    def create_blob(self, content):
        return self.request(
            "POST",
            "/git/blobs",
            {"content": base64.b64encode(content).decode(), "encoding": "base64"},
        )["sha"]

    def create_tree(self, base_tree, entries):
        return self.request(
            "POST", "/git/trees", {"base_tree": base_tree, "tree": entries}
        )["sha"]

    def create_commit(self, message, tree, parent, author, committer):
        return self.request(
            "POST",
            "/git/commits",
            {
                "message": message,
                "tree": tree,
                "parents": [parent],
                "author": author,
                "committer": committer,
            },
        )["sha"]

    def create_ref(self, branch, sha):
        return self.request(
            "POST", "/git/refs", {"ref": f"refs/heads/{branch}", "sha": sha}
        )


def publish_commit(api, revision, branch):
    run_git("check-ref-format", "--branch", branch)

    commit = resolve_commit(revision)
    parent = resolve_commit(f"{commit}^")
    base_tree = run_git("rev-parse", f"{parent}^{{tree}}").decode().strip()
    expected_tree = run_git("rev-parse", f"{commit}^{{tree}}").decode().strip()
    before = tree_entries(parent)
    after = tree_entries(commit)
    entries = []

    for raw_path in changed_paths(parent, commit):
        path = decode_git_text(raw_path, "repository path")
        current = after.get(raw_path)
        if current is None:
            previous = before[raw_path]
            entries.append({"path": path, **previous, "sha": None})
            continue

        sha = current["sha"]
        if current["type"] == "blob":
            content = run_git("cat-file", "blob", sha)
            uploaded_sha = api.create_blob(content)
            if uploaded_sha != git_blob_sha(content) or uploaded_sha != sha:
                raise RuntimeError(
                    f"GitHub returned unexpected blob SHA for {path}: {uploaded_sha}"
                )
            sha = uploaded_sha

        entries.append({"path": path, **current, "sha": sha})

    created_tree = api.create_tree(base_tree, entries)
    if created_tree != expected_tree:
        raise RuntimeError(
            "GitHub returned a tree different from the local cherry-pick: "
            f"expected {expected_tree}, got {created_tree}"
        )

    raw_commit = run_git("cat-file", "commit", commit)
    _, message_bytes = raw_commit.split(b"\n\n", 1)
    message = decode_git_text(message_bytes, "commit message")
    remote_commit = api.create_commit(
        message,
        created_tree,
        parent,
        commit_identity(commit, "author"),
        commit_identity(commit, "committer"),
    )
    if remote_commit != commit:
        raise RuntimeError(
            "GitHub returned a commit different from the local cherry-pick: "
            f"expected {commit}, got {remote_commit}"
        )
    api.create_ref(branch, remote_commit)
    return remote_commit


def parse_args():
    parser = argparse.ArgumentParser(
        description="Publish a local commit as a GitHub branch using the Git Data API"
    )
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--branch", required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    token = os.environ.get("GIT_DATA_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GIT_DATA_TOKEN or GITHUB_TOKEN must be set")

    repository = os.environ.get("GITHUB_REPOSITORY")
    if not repository:
        raise RuntimeError("GITHUB_REPOSITORY must be set")

    api = GitHubApi(
        os.environ.get("GITHUB_API_URL", "https://api.github.com"),
        repository,
        token,
    )
    sha = publish_commit(api, args.commit, args.branch)
    print(f"Published {sha} as refs/heads/{args.branch}")


if __name__ == "__main__":
    try:
        main()
    except (KeyError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
