"""Background tasks and Qt signal transport."""
from PySide6.QtCore import QObject, Signal, QRunnable

class WorkerSignals(QObject):
    finished = Signal(object, object)
    progress = Signal(object, object, object)

class Worker(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.signals = WorkerSignals()

    def run(self):
        try:
            result = self.fn()
        except Exception as exc:
            self.signals.finished.emit(None, str(exc))
        else:
            self.signals.finished.emit(result, None)
