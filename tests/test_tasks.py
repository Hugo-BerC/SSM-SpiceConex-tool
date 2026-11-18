from __future__ import annotations

import os
import threading
import time
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QComboBox, QWidget

from pyside6_poc.tasks import AwsContext, start_task, task_busy


class TaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = QWidget()
        self.release = threading.Event()
        self.received, self.errors, self.cleaned = [], [], []

    def tearDown(self):
        self.release.set()
        self.wait_until(lambda: not task_busy(self.owner))
        self.owner.deleteLater()
        self.app.processEvents()

    def wait_until(self, condition):
        deadline = time.monotonic() + 3
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertTrue(condition(), "Qt worker did not finish")

    def start(self, operation, **kwargs):
        return start_task(self.owner, operation, self.received.append,
                          self.errors.append, lambda: self.cleaned.append(True), **kwargs)

    def context(self):
        profile, region = QComboBox(), QComboBox()
        profile.addItems(["original", "other"])
        region.addItems(["eu-west-1", "eu-south-2"])
        self.owner.aws_context = AwsContext(
            self.owner, SimpleNamespace(profile=profile, region=region), lambda: None,
        )
        return profile, region

    def test_second_task_is_rejected_until_first_thread_finishes(self):
        self.assertTrue(self.start(lambda: self.release.wait(2)))
        self.assertFalse(self.start(lambda: "second"))
        self.release.set()
        self.wait_until(lambda: not task_busy(self.owner))
        self.assertEqual([True], self.received)
        self.assertEqual([True], self.cleaned)
        self.assertTrue(self.start(lambda: "next"))
        self.wait_until(lambda: not task_busy(self.owner))
        self.assertEqual([True, "next"], self.received)

    def test_profile_change_and_return_still_discards_old_result(self):
        profile, _region = self.context()
        self.start(lambda: (self.release.wait(2), "old")[1])
        profile.setCurrentText("other")
        profile.setCurrentText("original")
        self.release.set()
        self.wait_until(lambda: not task_busy(self.owner))
        self.assertEqual([], self.received)
        self.assertEqual([True], self.cleaned)

    def test_region_change_discards_old_error(self):
        _profile, region = self.context()
        def operation():
            self.release.wait(2)
            raise RuntimeError("old context")
        self.start(operation)
        region.setCurrentText("eu-south-2")
        self.release.set()
        self.wait_until(lambda: not task_busy(self.owner))
        self.assertEqual([], self.errors)
        self.assertEqual([True], self.cleaned)

    def test_callbacks_run_on_gui_thread_and_cannot_reenter_before_cleanup(self):
        attempts = []
        def finished(result):
            self.assertEqual(self.app.thread(), QThread.currentThread())
            attempts.append(self.start(lambda: "duplicate"))
            self.received.append(result)
        start_task(self.owner, lambda: "done", finished, self.errors.append,
                   lambda: self.cleaned.append(True))
        self.wait_until(lambda: not task_busy(self.owner))
        self.assertEqual([False], attempts)
        self.assertEqual(["done"], self.received)
