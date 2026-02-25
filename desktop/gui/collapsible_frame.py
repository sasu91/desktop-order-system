"""Collapsible frame widget for tkinter GUI."""
import tkinter as tk
from tkinter import ttk


class CollapsibleFrame(ttk.Frame):
    """A frame that can be collapsed/expanded with a toggle button."""
    
    def __init__(self, parent, title="Section", expanded=True, **kwargs):
        """
        Initialize collapsible frame.
        
        Args:
            parent: Parent widget
            title: Section title
            expanded: Initial state (True = expanded, False = collapsed)
            **kwargs: Additional frame options
        """
        super().__init__(parent, **kwargs)
        
        self.title = title
        self.expanded = tk.BooleanVar(value=expanded)
        
        # Header frame with toggle button
        self.header = ttk.Frame(self)
        self.header.pack(fill="x", padx=2, pady=2)
        
        # Toggle button (arrow + title)
        self.toggle_btn = ttk.Button(
            self.header,
            text=self._get_arrow() + " " + self.title,
            command=self._toggle,
            style="Toolbutton"
        )
        self.toggle_btn.pack(side="left", fill="x", expand=True)
        
        # Content frame (collapsible)
        self.content = ttk.Frame(self, padding=10)
        if expanded:
            self.content.pack(fill="both", expand=True)
    
    def _get_arrow(self):
        """Get arrow symbol based on expanded state."""
        return "▼" if self.expanded.get() else "▶"
    
    def _toggle(self):
        """Toggle expand/collapse state."""
        self.expanded.set(not self.expanded.get())
        self.toggle_btn.config(text=self._get_arrow() + " " + self.title)
        
        if self.expanded.get():
            self.content.pack(fill="both", expand=True)
        else:
            self.content.pack_forget()
    
    def get_content_frame(self):
        """Return the content frame where widgets should be placed."""
        return self.content
