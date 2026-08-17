import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from publish_commit import git_blob_sha, publish_commit


class FakeApi:
    def __init__(self, expected_tree, expected_commit):
        self.expected_tree = expected_tree
        self.expected_commit = expected_commit
        self.blobs = {}
        self.tree = None
        self.commit = None
        self.ref = None

    def create_blob(self, content):
        sha = git_blob_sha(content)
        self.blobs[sha] = content
        return sha

    def create_tree(self, base_tree, entries):
        self.tree = {"base_tree": base_tree, "entries": entries}
        return self.expected_tree

    def create_commit(self, message, tree, parent, author, committer):
        self.commit = {
            "message": message,
            "tree": tree,
            "parent": parent,
            "author": author,
            "committer": committer,
        }
        return self.expected_commit

    def create_ref(self, branch, sha):
        self.ref = {"branch": branch, "sha": sha}


class PublishCommitTest(unittest.TestCase):
    def setUp(self):
        self.previous_cwd = os.getcwd()
        self.temp_dir = tempfile.TemporaryDirectory()
        os.chdir(self.temp_dir.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Test Author")
        self.git("config", "user.email", "author@example.com")

        Path("delete me.txt").write_text("old\n")
        Path("script.sh").write_text("#!/bin/sh\nexit 0\n")
        Path("script.sh").chmod(0o644)
        Path("unchanged.txt").write_text("unchanged\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "base")

    def tearDown(self):
        os.chdir(self.previous_cwd)
        self.temp_dir.cleanup()

    def git(self, *args):
        return subprocess.check_output(("git", *args)).decode().strip()

    def test_publishes_same_tree_for_binary_modes_symlink_and_deletion(self):
        Path("delete me.txt").unlink()
        Path("script.sh").write_text("#!/bin/sh\necho changed\n")
        Path("script.sh").chmod(0o755)
        Path("binary.dat").write_bytes(b"\x00\xff\x10binary\n")
        os.symlink("unchanged.txt", "link with space")
        Path("line\nbreak.txt").write_text("odd path\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "change files")

        expected_tree = self.git("rev-parse", "HEAD^{tree}")
        expected_commit = self.git("rev-parse", "HEAD")
        api = FakeApi(expected_tree, expected_commit)
        remote_sha = publish_commit(api, "HEAD", "auto-release-test")

        entries = {entry["path"]: entry for entry in api.tree["entries"]}
        self.assertEqual(
            api.tree["base_tree"], self.git("rev-parse", "HEAD~1^{tree}")
        )
        self.assertEqual(
            set(entries),
            {
                "binary.dat",
                "delete me.txt",
                "line\nbreak.txt",
                "link with space",
                "script.sh",
            },
        )
        self.assertIsNone(entries["delete me.txt"]["sha"])
        self.assertEqual(entries["script.sh"]["mode"], "100755")
        self.assertEqual(entries["link with space"]["mode"], "120000")
        self.assertIn("line\nbreak.txt", entries)
        binary_sha = hashlib.sha1(
            b"blob 10\0" + b"\x00\xff\x10binary\n"
        ).hexdigest()
        self.assertEqual(api.blobs[binary_sha], b"\x00\xff\x10binary\n")
        self.assertEqual(api.commit["tree"], expected_tree)
        self.assertEqual(api.commit["message"], "change files\n")
        self.assertEqual(api.commit["author"]["name"], "Test Author")
        self.assertEqual(
            api.ref, {"branch": "auto-release-test", "sha": expected_commit}
        )
        self.assertEqual(remote_sha, expected_commit)

    def test_rejects_invalid_branch_before_api_calls(self):
        Path("new.txt").write_text("new\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "new file")
        api = FakeApi(
            self.git("rev-parse", "HEAD^{tree}"), self.git("rev-parse", "HEAD")
        )

        with self.assertRaises(subprocess.CalledProcessError):
            publish_commit(api, "HEAD", "invalid..branch")

        self.assertIsNone(api.tree)

    def test_does_not_create_ref_for_mismatched_commit(self):
        Path("new.txt").write_text("new\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "new file")
        api = FakeApi(self.git("rev-parse", "HEAD^{tree}"), "f" * 40)

        with self.assertRaisesRegex(RuntimeError, "commit different"):
            publish_commit(api, "HEAD", "auto-release-test")

        self.assertIsNone(api.ref)


if __name__ == "__main__":
    unittest.main()
