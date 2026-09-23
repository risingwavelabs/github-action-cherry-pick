import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(
    os.environ.get("CHERRY_PICK_TEST_CONTAINER") == "1"
    and Path("/.dockerenv").exists(),
    "Run with make test in a disposable container: entrypoint writes git credentials",
)
class EntrypointTest(unittest.TestCase):
    SHA = "a" * 40
    EVENT_SHA = "b" * 40

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.calls = self.root / "calls.jsonl"
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        # Exercise the real entrypoint, replacing only external side effects.
        stub = f"#!{sys.executable}\n" + r'''
import json, os, sys
from pathlib import Path
command = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["TEST_CALLS"], "a") as output:
    output.write(json.dumps([command, *args]) + "\n")
if command == "git":
    if args[0] == "log":
        print("fix: fixture with 'quotes' and `backticks`")
    elif args[0] == "cherry-pick":
        sys.exit(int(os.environ.get("TEST_CONFLICT", "1")))
elif command == "hub":
    if args[:2] == ["issue", "create"]:
        if os.environ.get("TEST_ISSUE_FAIL") == "1":
            sys.exit(1)
        print("https://github.com/risingwavelabs/risingwave/issues/99999")
    elif args[0] == "pull-request":
        print("https://github.com/risingwavelabs/risingwave/pull/99999")
    else:
        # The old assignee POST must never run, even if it would return 403.
        print("403 Forbidden", file=sys.stderr)
        sys.exit(1)
elif command == "python3":
    assert args[0] == "/publish_commit.py", args
'''
        for command in ["git", "hub", "python3"]:
            path = bin_dir / command
            path.write_text(stub)
            path.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
            "TEST_CALLS": str(self.calls),
            "GITHUB_TOKEN": "fixture-not-a-real-token",
            "GITHUB_ACTOR": "fixture-user",
            "GITHUB_REPOSITORY": "risingwavelabs/risingwave",
            "GITHUB_SHA": self.EVENT_SHA,
            "INPUT_COMMIT_SHA": self.SHA,
            "INPUT_PR_BRANCH": "release-3.1",
            "INPUT_PR_LABELS": "cherry-pick",
            "INPUT_PR_BODY": "Cherry picking #123 onto branch release-3.1",
            "INPUT_SOURCE_PR_NUMBER": "",
            "DRY_RUN": "false",
        }

    def run_action(self, **overrides):
        script = Path(__file__).resolve().parents[1] / "entrypoint.sh"
        result = subprocess.run(
            ["bash", str(script)],
            env={**self.env, **overrides},
            capture_output=True,
            text=True,
        )
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertFalse(any(call[:2] == ["hub", "api"] for call in calls))
        return result, calls

    def issue_body(self, calls):
        hub_calls = [call for call in calls if call[0] == "hub"]
        self.assertEqual(len(hub_calls), 1)
        args = hub_calls[0]
        self.assertEqual(args[:3], ["hub", "issue", "create"])
        return [args[i + 1] for i, value in enumerate(args[:-1]) if value == "-m"][1]

    def assert_marker(self, body, source=123, sha=None):
        # Consumer contract: jarvis-v2/src/tests/cherry-pick-conflict.test.ts.
        marker = (
            "<!-- jarvis-cherry-pick-conflict:v1 "
            f"source_pr=risingwavelabs/risingwave#{source} "
            f"commit={sha or self.SHA} target=release-3.1 -->"
        )
        self.assertEqual(body.splitlines()[-1], marker)
        self.assertEqual(body.count("jarvis-cherry-pick-conflict:v1"), 1)

    def test_legacy_body_emits_exact_contract_without_assignment_or_commas(self):
        result, calls = self.run_action()
        self.assertEqual(result.returncode, 0, result.stderr)
        body = self.issue_body(calls)
        self.assert_marker(body)
        self.assertTrue(body.startswith(
            self.env["INPUT_PR_BODY"]
            + f"\n\nThis PR/issue was created by cherry-pick action from commit {self.SHA}.\n\n"
        ))
        self.assertIn(["git", "cherry-pick", self.SHA], calls)

    def test_legacy_body_accepts_repeated_whitespace(self):
        result, calls = self.run_action(
            INPUT_PR_BODY="Cherry  picking\n#123\t onto   branch release-3.1"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_marker(self.issue_body(calls))

    def test_explicit_source_overrides_legacy_body(self):
        result, calls = self.run_action(INPUT_SOURCE_PR_NUMBER="456")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_marker(self.issue_body(calls), source=456)

    def test_missing_source_creates_issue_without_marker(self):
        result, calls = self.run_action(INPUT_PR_BODY="No source PR supplied")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("jarvis-cherry-pick-conflict:v1", self.issue_body(calls))

    def test_missing_explicit_commit_falls_back_to_event_sha(self):
        result, calls = self.run_action(INPUT_COMMIT_SHA="")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_marker(self.issue_body(calls), sha=self.EVENT_SHA)
        self.assertIn(["git", "cherry-pick", self.EVENT_SHA], calls)

    def test_issue_creation_failure_fails_action(self):
        result, calls = self.run_action(TEST_ISSUE_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assert_marker(self.issue_body(calls))

    def test_success_publishes_and_opens_pr_without_conflict_marker(self):
        result, calls = self.run_action(TEST_CONFLICT="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        publications = [call for call in calls if call[0] == "python3"]
        self.assertEqual(len(publications), 1)
        hub_calls = [call for call in calls if call[0] == "hub"]
        self.assertEqual(len(hub_calls), 1)
        self.assertEqual(hub_calls[0][:2], ["hub", "pull-request"])
        self.assertNotIn("jarvis-cherry-pick-conflict:v1", " ".join(hub_calls[0]))


if __name__ == "__main__":
    unittest.main()
