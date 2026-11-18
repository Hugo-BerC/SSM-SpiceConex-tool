from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
import json
import zipfile
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from app import updater


def completed(args: list[str], stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")


class VersionTests(unittest.TestCase):
    def test_latest_tag_uses_semantic_versions(self):
        tags = [
            {"name": "v0.9.0", "commit": {"sha": "1111111111111111111111111111111111111111"}},
            {"name": "not-a-release", "commit": {"sha": "ignored"}},
            {"name": "v1.0.0", "commit": {"sha": "2222222222222222222222222222222222222222"}},
        ]
        with patch.object(updater, "_github_tags", return_value=tags):
            target = updater.latest_published_target("owner/repo")
        self.assertEqual("1.0.0", str(target.version))
        self.assertEqual("v1.0.0", target.tag)

    def test_no_tags_is_an_explicit_non_update(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(updater, "active_installation_problem", return_value=None), patch.object(updater.shutil, "which", return_value="git"), patch.object(updater, "is_git_worktree", return_value=True), patch.object(updater, "_git", return_value=completed(["git"], "origin-url\n")), patch.object(updater, "_verify_repository"), patch.object(updater, "latest_published_target", return_value=None):
            result = updater.check_for_update(Path(directory))
        self.assertFalse(result["available"])
        self.assertIn("No published Git tag", result["reason"])

    def test_network_error_is_human_readable(self):
        with patch.object(updater, "urlopen", side_effect=URLError("offline")):
            with self.assertRaisesRegex(RuntimeError, "Could not reach GitHub"):
                updater._github_tags("owner/repo")


class UpdateSafetyTests(unittest.TestCase):
    def test_non_active_copy_cannot_offer_an_update(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(updater, "active_installation_problem", return_value="not the active installation"):
            result = updater.check_for_update(Path(directory))
        self.assertFalse(result["available"])
        self.assertFalse(result["can_apply"])
        self.assertIn("not the active installation", result["reason"])

    def test_coordinator_restarts_after_successful_update(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(sys, "argv", ["updater.py", "--apply", directory, "main", "v1.0.0", "123", "owner/repo", "2222222222222222222222222222222222222222"]), \
             patch.object(updater, "apply_update") as apply, \
             patch.object(updater, "_restart") as restart:
            self.assertEqual(0, updater._main())
        apply.assert_called_once_with(Path(directory).resolve(), "main", "v1.0.0", 123, repository="owner/repo", expected_sha="2222222222222222222222222222222222222222")
        restart.assert_called_once_with(Path(directory).resolve())

    def test_dirty_worktree_blocks_before_fetch(self):
        with tempfile.TemporaryDirectory() as directory:
            calls: list[list[str]] = []
            def git(args, cwd):
                calls.append(args)
                if args == ["status", "--porcelain"]:
                    return completed(args, " M app/updater.py\n")
                return completed(args)
            with patch.object(updater, "_git", side_effect=git), patch.object(updater, "active_installation_problem", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "Local changes"):
                    updater.apply_update(Path(directory), "main", "v1.0.0")
            self.assertNotIn(["fetch", "--tags", "origin", "main"], calls)


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.app_dir = self.root / "app"; self.app_dir.mkdir()
        self.manifest = self.root / "runtime" / "active.json"
        self.original_git = updater._git
        self.git(["init", "-b", "main"])
        self.git(["config", "core.hooksPath", str(self.root / "no-hooks")])
        (self.app_dir / ".gitignore").write_text(".spiceconex-update.lock\n", encoding="utf-8")
        self.source = self.app_dir / "app.txt"; self.source.write_text("old", encoding="utf-8")
        self.git(["add", "."]); self.commit("initial")
        self.old = updater._commit(self.app_dir, "HEAD")
        self.source.write_text("new", encoding="utf-8"); self.git(["add", "."]); self.commit("release")
        self.new = updater._commit(self.app_dir, "HEAD")
        self.git(["tag", "v1.0.0"])
        self.git(["update-ref", "refs/remotes/origin/main", self.new])
        self.git(["reset", "--keep", self.old])
        self.git(["remote", "add", "origin", "https://github.com/owner/repo.git"])
        self.manifest_patch = patch.object(updater, "runtime_manifest", return_value=self.manifest)
        self.manifest_patch.start(); self.addCleanup(self.manifest_patch.stop)
        self.active_patch = patch.object(updater, "active_installation_problem", return_value=None)
        self.active_patch.start(); self.addCleanup(self.active_patch.stop)
        self.calls = []

    def git(self, args):
        result = self.original_git(args, self.app_dir)
        self.assertEqual(0, result.returncode, result.stderr)
        return result

    def commit(self, message):
        self.git(["-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                  "commit", "--no-gpg-sign", "-m", message])

    def offline_git(self, args, cwd):
        self.calls.append(args)
        if args[0] == "fetch": return completed(args)
        return self.original_git(args, cwd)

    def prepare(self, _app, _sha, version_dir):
        python = version_dir / "venv" / "python"
        python.parent.mkdir(); python.write_text("staged interpreter", encoding="utf-8")
        return python

    def apply(self, **kwargs):
        return updater.apply_update(self.app_dir, "main", "v1.0.0", expected_sha=kwargs.get("sha", self.new), repository="owner/repo")

    def test_wrong_remote_blocks_before_fetch_or_installation(self):
        self.git(["remote", "set-url", "origin", "https://github.com/other/repository.git"])
        with patch.object(updater, "_git", side_effect=self.offline_git), patch.object(updater, "_prepare_environment") as prepare:
            with self.assertRaisesRegex(RuntimeError, "origin remote"):
                self.apply()
        prepare.assert_not_called()
        self.assertFalse(any(args[0] == "fetch" for args in self.calls))

    def test_changed_tag_sha_blocks_before_environment_preparation(self):
        with patch.object(updater, "_git", side_effect=self.offline_git), patch.object(updater, "_prepare_environment") as prepare:
            with self.assertRaisesRegex(RuntimeError, "SHA differs"):
                self.apply(sha=self.old)
        prepare.assert_not_called()
        self.assertEqual("old", self.source.read_text(encoding="utf-8"))

    def test_divergence_is_rejected_before_pip(self):
        self.source.write_text("local branch", encoding="utf-8"); self.git(["add", "."]); self.commit("local")
        with patch.object(updater, "_git", side_effect=self.offline_git), patch.object(updater, "_prepare_environment") as prepare:
            with self.assertRaisesRegex(RuntimeError, "not a fast-forward"):
                self.apply()
        prepare.assert_not_called()
        self.assertEqual("local branch", self.source.read_text(encoding="utf-8"))

    def test_install_failure_does_not_modify_source_or_active_environment(self):
        with patch.object(updater, "_git", side_effect=self.offline_git), \
             patch.object(updater, "_prepare_environment", side_effect=RuntimeError("dependency preparation failed")):
            with self.assertRaisesRegex(RuntimeError, "dependency preparation"):
                self.apply()
        self.assertEqual(self.old, updater._commit(self.app_dir, "HEAD"))
        self.assertFalse(self.manifest.exists())
        self.assertFalse(any(args[0] == "merge" for args in self.calls))
        self.assertFalse((self.app_dir / ".spiceconex-update.lock").exists())

    def test_merge_failure_keeps_source_and_runtime(self):
        def fail_merge(args, cwd):
            return completed(args, returncode=1) if args[0] == "merge" else self.offline_git(args, cwd)
        with patch.object(updater, "_git", side_effect=fail_merge), patch.object(updater, "_prepare_environment", side_effect=self.prepare):
            with self.assertRaisesRegex(RuntimeError, "fast-forward failed"):
                self.apply()
        self.assertEqual("old", self.source.read_text(encoding="utf-8"))
        self.assertFalse(self.manifest.exists())

    def test_merge_failure_after_head_moves_is_rolled_back(self):
        def fail_after_merge(args, cwd):
            result = self.offline_git(args, cwd)
            return completed(args, returncode=1) if args[0] == "merge" else result
        with patch.object(updater, "_git", side_effect=fail_after_merge), \
             patch.object(updater, "_prepare_environment", side_effect=self.prepare):
            with self.assertRaisesRegex(RuntimeError, "fast-forward failed"):
                self.apply()
        self.assertEqual(self.old, updater._commit(self.app_dir, "HEAD"))
        self.assertEqual("old", self.source.read_text(encoding="utf-8"))

    def test_recovery_refuses_to_overwrite_concurrent_edits(self):
        atomic = updater._atomic_json
        def fail_activation(path, payload):
            if path == self.manifest:
                self.source.write_text("concurrent post-merge edit", encoding="utf-8")
                raise OSError("simulated activation error")
            atomic(path, payload)
        with patch.object(updater, "_git", side_effect=self.offline_git), \
             patch.object(updater, "_prepare_environment", side_effect=self.prepare), \
             patch.object(updater, "_atomic_json", side_effect=fail_activation):
            with self.assertRaises(updater.UpdateRecoveryError): self.apply()
        self.assertEqual("concurrent post-merge edit", self.source.read_text(encoding="utf-8"))
        self.assertTrue(list(self.manifest.parent.glob("versions/*/venv/python")))
        self.assertTrue((self.app_dir / ".spiceconex-update.lock").exists())
        journal = next(self.manifest.parent.glob("versions/*/transaction.json"))
        self.assertEqual("recovery_required", json.loads(journal.read_text(encoding="utf-8"))["phase"])

    def test_runtime_activation_failure_restores_original_commit(self):
        atomic = updater._atomic_json
        def fail_activation(path, payload):
            if path == self.manifest: raise OSError("simulated full disk")
            atomic(path, payload)
        with patch.object(updater, "_git", side_effect=self.offline_git), \
             patch.object(updater, "_prepare_environment", side_effect=self.prepare), \
             patch.object(updater, "_atomic_json", side_effect=fail_activation):
            with self.assertRaises(OSError): self.apply()
        self.assertEqual(self.old, updater._commit(self.app_dir, "HEAD"))
        self.assertEqual("old", self.source.read_text(encoding="utf-8"))
        self.assertFalse(self.manifest.exists())

    def test_success_activates_staged_interpreter_and_records_previous_revision(self):
        with patch.object(updater, "_git", side_effect=self.offline_git), patch.object(updater, "_prepare_environment", side_effect=self.prepare):
            self.apply()
        data = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual(self.new, updater._commit(self.app_dir, "HEAD"))
        self.assertEqual(self.old, data["previous_sha"])
        self.assertTrue(Path(data["python"]).is_file())
        journal = next(self.manifest.parent.glob("versions/*/transaction.json"))
        self.assertEqual("committed", json.loads(journal.read_text(encoding="utf-8"))["phase"])

    def test_concurrent_local_edit_is_preserved_without_merge(self):
        def edit(*args):
            python = self.prepare(*args)
            self.source.write_text("concurrent edit", encoding="utf-8")
            return python
        with patch.object(updater, "_git", side_effect=self.offline_git), patch.object(updater, "_prepare_environment", side_effect=edit):
            with self.assertRaisesRegex(RuntimeError, "Local changes"): self.apply()
        self.assertEqual("concurrent edit", self.source.read_text(encoding="utf-8"))
        self.assertFalse(any(args[0] == "merge" for args in self.calls))

    def test_staged_pip_never_targets_active_interpreter(self):
        version_dir = self.root / "prepared"; version_dir.mkdir()
        def archive(args, _cwd):
            with zipfile.ZipFile(version_dir / "source.zip", "w") as bundle:
                bundle.writestr("requirements.txt", "packaging>=25\n")
                bundle.writestr("app/runtime.py", "")
                bundle.writestr("ssm_spiceconex.py", "use_active_runtime()")
            return completed(args)
        with patch.object(updater, "_git", side_effect=archive), \
             patch.object(updater.subprocess, "run", return_value=completed([])) as run:
            interpreter = updater._prepare_environment(self.app_dir, self.new, version_dir)
        self.assertNotEqual(str(Path(sys.executable)), str(interpreter))
        for call in run.call_args_list[1:]:
            self.assertEqual(str(interpreter), call.args[0][0])
        self.assertIn("pip", run.call_args_list[1].args[0])
        for call in run.call_args_list:
            self.assertEqual("true", call.kwargs["env"]["PIP_REQUIRE_VIRTUALENV"])
            self.assertEqual(updater.os.devnull, call.kwargs["env"]["PIP_CONFIG_FILE"])


class RepositoryTests(unittest.TestCase):
    def test_only_expected_github_remote_forms_are_accepted(self):
        for url in ("https://github.com/Owner/Repo.git", "git@github.com:Owner/Repo.git", "ssh://git@github.com/Owner/Repo.git"):
            self.assertEqual("owner/repo", updater._repository_from_url(url))
        for url in ("https://github.com.evil.test/owner/repo", "https://user:secret@github.com/owner/repo",
                    "file:///tmp/owner/repo", "https://github.com/owner/repo?target=other"):
            self.assertIsNone(updater._repository_from_url(url))

