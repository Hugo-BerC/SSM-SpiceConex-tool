from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from app import updater


def completed(args: list[str], stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")


class VersionTests(unittest.TestCase):
    def test_latest_tag_uses_semantic_versions(self):
        tags = [
            {"name": "v0.9.0", "commit": {"sha": "old"}},
            {"name": "not-a-release", "commit": {"sha": "ignored"}},
            {"name": "v1.0.0", "commit": {"sha": "new"}},
        ]
        with patch.object(updater, "_github_tags", return_value=tags):
            target = updater.latest_published_target("owner/repo")
        self.assertEqual("1.0.0", str(target.version))
        self.assertEqual("v1.0.0", target.tag)

    def test_no_tags_is_an_explicit_non_update(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(updater.shutil, "which", return_value="git"), patch.object(updater, "is_git_worktree", return_value=True), patch.object(updater, "_git", return_value=completed(["git"], "origin-url\n")), patch.object(updater, "latest_published_target", return_value=None):
            result = updater.check_for_update(Path(directory))
        self.assertFalse(result["available"])
        self.assertIn("No published Git tag", result["reason"])

    def test_network_error_is_human_readable(self):
        with patch.object(updater, "urlopen", side_effect=URLError("offline")):
            with self.assertRaisesRegex(RuntimeError, "Could not reach GitHub"):
                updater._github_tags("owner/repo")


class UpdateSafetyTests(unittest.TestCase):
    def test_coordinator_restarts_after_successful_update(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(sys, "argv", ["updater.py", "--apply", directory, "main", "v1.0.0", "123"]), \
             patch.object(updater, "apply_update") as apply, \
             patch.object(updater, "_restart") as restart:
            self.assertEqual(0, updater._main())
        apply.assert_called_once_with(Path(directory).resolve(), "main", "v1.0.0", 123)
        restart.assert_called_once_with(Path(directory).resolve())

    def test_dirty_worktree_blocks_before_fetch(self):
        with tempfile.TemporaryDirectory() as directory:
            calls: list[list[str]] = []
            def git(args, cwd):
                calls.append(args)
                if args == ["status", "--porcelain"]:
                    return completed(args, " M app/updater.py\n")
                return completed(args)
            with patch.object(updater, "_git", side_effect=git):
                with self.assertRaisesRegex(RuntimeError, "Local changes"):
                    updater.apply_update(Path(directory), "main", "v1.0.0")
            self.assertNotIn(["fetch", "--tags", "origin", "main"], calls)

    def test_dependency_failure_does_not_merge_source(self):
        with tempfile.TemporaryDirectory() as directory:
            app_dir = Path(directory)
            calls: list[list[str]] = []
            def git(args, cwd):
                calls.append(args)
                if args == ["status", "--porcelain"]:
                    return completed(args)
                if args == ["branch", "--show-current"]:
                    return completed(args, "main\n")
                if args == ["fetch", "--tags", "origin", "main"]:
                    return completed(args)
                if args == ["merge-base", "--is-ancestor", "refs/tags/v1.0.0", "origin/main"]:
                    return completed(args)
                if args == ["show", "refs/tags/v1.0.0:requirements.txt"]:
                    return completed(args, "packaging>=25\n")
                return completed(args)
            failed_pip = completed(["python", "-m", "pip"], returncode=1)
            with patch.object(updater, "_git", side_effect=git), patch.object(updater.subprocess, "run", return_value=failed_pip):
                with self.assertRaisesRegex(RuntimeError, "dependency preparation failed"):
                    updater.apply_update(app_dir, "main", "v1.0.0")
            self.assertNotIn(["merge", "--ff-only", "origin/main"], calls)
            self.assertFalse((app_dir / ".spiceconex-update.lock").exists())

