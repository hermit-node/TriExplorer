"""Explorer feature methods. Implemented on the shared Explorer instance.

These mixins do not construct UI independently; Explorer composes the controllers.
"""
from __future__ import annotations
from appcore import (
    CATEGORY_SUFFIXES, Event, Path, read_image_preview, readable_size
)

class PreviewMixin:
    def preview_animation_finished(self):
        if self.preview_open:
            target = self.preview_animation.endValue()
            self.preview_panel.setMaximumWidth(16777215)
            self.size_preview_panel(target)


    def set_preview_open(self, opened):
        if self.preview_open == opened:
            return
        self.preview_open = opened
        self.preview_animation.stop()
        if opened:
            original = self.splitter.sizes()
            target = max(180, min(self.preview_width, max(180, sum(original) // 2)))
            self.preview_panel.setMaximumWidth(0)
            self.preview_panel.show()
            self.preview_animation.setStartValue(0)
            self.preview_animation.setEndValue(target)
            self.preview_animation.start()
            self.schedule_preview()
        else:
            width = self.preview_panel.width()
            if width >= 100:
                self.preview_width = width
            self.stop_preview()
            self.preview_panel.hide()
            self.preview_panel.setMaximumWidth(16777215)
            self.preview_label.reset("Select an image")
            self.preview_details.clear()


    def size_preview_panel(self, target):
        sizes = self.splitter.sizes()
        available = sum(sizes)
        if available > target:
            keep = sizes[0]
            rest = max(1, sizes[1] + sizes[2])
            middle = max(0, available - target - keep)
            self.splitter.setSizes([keep, int(middle * sizes[1] / rest),
                                    middle - int(middle * sizes[1] / rest), target])


    def stop_preview(self):
        self.preview_timer.stop()
        self.preview_pending = False
        if self.preview_job is not None:
            self.preview_job.set()
            self.preview_job = None
        self.preview_key = None


    def schedule_preview(self):
        if not self.preview_open:
            return
        entries = self.selected(self.active_view) if self.active_view is not None else []
        entry = entries[0] if len(entries) == 1 else None
        key = (entry.path, entry.size, entry.modified) if entry and not entry.folder else None
        if key == self.preview_key:
            return
        self.stop_preview()
        self.preview_key = key
        self.preview_details.clear()
        if key is None:
            self.preview_label.reset("Select one image")
            return
        if Path(entry.name).suffix.casefold() not in CATEGORY_SUFFIXES["images"]:
            self.preview_label.reset("No image preview for this file")
            return
        self.preview_label.reset("Loading preview...")
        self.preview_timer.start()


    def load_preview(self):
        if not self.preview_open or self.preview_key is None:
            return
        if self.preview_inflight:
            self.preview_pending = True
            return
        key = self.preview_key
        stop = Event()
        self.preview_job = stop
        self.preview_inflight = True
        self.preview_pending = False

        def done(result, error):
            self.preview_inflight = False
            if not self.preview_open or self.preview_job is not stop or stop.is_set():
                if self.preview_open and self.preview_pending:
                    self.load_preview()
                return
            self.preview_job = None
            if error or result is None:
                self.preview_label.reset(error or "Preview unavailable")
                return
            image, original = result
            self.preview_label.set_image(image)
            self.preview_details.setText(
                f"{Path(key[0]).name}\n{original.width():,} × {original.height():,} px  |  {readable_size(key[1])}")

        self.submit(lambda: read_image_preview(key[0], stop.is_set), done)
