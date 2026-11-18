"""Qt task ownership and AWS context guards for the desktop pages."""
from __future__ import annotations

from collections.abc import Callable
import time
import uuid

from PySide6.QtCore import QObject, QThread, Signal, Slot

from utils.logging import get_logger

logger = get_logger("tasks")


class AwsContext(QObject):
    """Identify a selection, including changes away and back to the same profile."""

    def __init__(self, owner, commands, invalidate: Callable):
        super().__init__(owner)
        self.commands = commands
        self.revision = 0
        self.invalidate = invalidate
        commands.profile.currentTextChanged.connect(self._changed)
        commands.region.currentTextChanged.connect(self._changed)
        if hasattr(commands, "contextRefreshed"):
            commands.contextRefreshed.connect(self._refreshed)

    def snapshot(self) -> tuple[int, str, str]:
        return (self.revision, self.commands.profile.currentText().strip(),
                self.commands.region.currentText().strip())

    @Slot(str)
    def _changed(self, _value):
        self._refreshed()

    @Slot()
    def _refreshed(self):
        self.revision += 1
        self.invalidate()


class Worker(QObject):
    finished = Signal(object)
    failed = Signal(object)

    def __init__(self, operation: Callable, label: str | None = None):
        super().__init__()
        self.operation = operation
        self.label = label or getattr(operation, "__name__", "operation")
        self.task_id = uuid.uuid4().hex[:12]

    @Slot()
    def run(self):
        started = time.monotonic()
        try:
            logger.info("Worker started | task=%s | operation=%s", self.task_id, self.label)
            result = self.operation()
            logger.info("Worker completed | task=%s | seconds=%.3f", self.task_id, time.monotonic() - started)
            self.finished.emit(result)
        except Exception as exc:
            logger.exception("Worker failed | task=%s | seconds=%.3f", self.task_id, time.monotonic() - started)
            self.failed.emit(exc)


class PageTask(QObject):
    """Keep a worker alive through thread completion and deliver on the UI thread."""

    def __init__(self, owner, operation, finished, failed, cleanup, context):
        super().__init__(owner)
        self.owner = owner
        self.finished = finished
        self.failed = failed
        self.cleanup = cleanup
        self.context = context
        self.original_context = context() if context else None
        self.thread = QThread(self)
        self.worker = Worker(operation, owner.__class__.__name__)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self._succeeded)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.failed.connect(self.worker.deleteLater)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self._complete)

    def _current(self) -> bool:
        return self.context is None or self.original_context == self.context()

    @Slot(object)
    def _succeeded(self, result):
        if self._current():
            self.finished(result)
        else:
            logger.info("Discarded worker result after AWS context changed")

    @Slot(object)
    def _failed(self, exc):
        if self._current():
            self.failed(exc)
        else:
            logger.info("Discarded worker error after AWS context changed")

    @Slot()
    def _complete(self):
        self.owner.thread = None
        self.owner.worker = None
        self.owner._task = None
        self.thread.deleteLater()
        self.cleanup()
        self.deleteLater()


def task_busy(owner) -> bool:
    # Keep the guard until QThread.finished, including while result handlers
    # process nested Qt events (for example a confirmation dialog).
    return getattr(owner, "_task", None) is not None or isinstance(getattr(owner, "thread", None), QThread)


def start_task(owner, operation, finished, failed, cleanup, *, context=None) -> bool:
    if task_busy(owner):
        return False
    if context is None and hasattr(owner, "aws_context"):
        context = owner.aws_context.snapshot
    task = PageTask(owner, operation, finished, failed, cleanup, context)
    owner._task = task
    owner.thread = task.thread
    owner.worker = task.worker
    task.thread.start()
    return True
