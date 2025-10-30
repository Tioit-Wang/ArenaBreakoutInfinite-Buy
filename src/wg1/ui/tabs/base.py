from __future__ import annotations

from typing import TYPE_CHECKING

from tkinter import ttk

if TYPE_CHECKING:
    from wg1.ui.app import App


class BaseTab(ttk.Frame):
    """Notebook 标签页基类，向下委托 App 公共状态。"""

    def __init__(self, app: "App", notebook: ttk.Notebook) -> None:
        super().__init__(notebook)
        self.app = app
        self.notebook = notebook

    def __getattr__(self, name: str):
        try:
            return super().__getattribute__(name)
        except AttributeError:
            return getattr(self.app, name)
