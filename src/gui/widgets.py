"""
Custom widgets for desktop-order-system GUI.

Reusable autocomplete and enhanced UI components.
"""
import tkinter as tk
from tkinter import ttk
from typing import Callable, List, Optional


class AutocompleteEntry:
    """
    Entry field with dynamic autocomplete popup.
    
    Features:
    - Real-time filtering while typing
    - Popup listbox with suggestions
    - Keyboard navigation (Up/Down arrows, Enter, Escape)
    - Mouse click selection
    - Maintains focus in entry field
    """
    
    def __init__(
        self,
        parent: tk.Widget,
        textvariable: tk.StringVar,
        items_callback: Callable[[str], List[str]],
        width: int = 25,
        on_select: Optional[Callable[[str], None]] = None,
        **kwargs
    ):
        """
        Initialize autocomplete entry.
        
        Args:
            parent: Parent widget
            textvariable: StringVar linked to entry value
            items_callback: Function that takes search text and returns filtered items list
            width: Entry width in characters
            on_select: Optional callback when item is selected
            **kwargs: Additional ttk.Entry options
        """
        self.parent = parent
        self.var = textvariable
        self.items_callback = items_callback
        self.on_select = on_select
        
        # Create entry widget
        self.entry = ttk.Entry(parent, textvariable=self.var, width=width, **kwargs)
        
        # Popup components
        self.popup = None
        self.listbox = None
        
        # Setup
        self._setup_bindings()
    
    def _setup_bindings(self):
        """Setup keyboard and focus bindings."""
        # Trace variable for real-time filtering
        self.var.trace('w', lambda *args: self._filter_items())
        
        # Keyboard navigation
        self.entry.bind('<Down>', self._on_down)
        self.entry.bind('<Up>', self._on_up)
        self.entry.bind('<Return>', self._on_select_item)
        self.entry.bind('<Escape>', self._on_escape)
        self.entry.bind('<FocusOut>', self._on_focus_out)
    
    def _filter_items(self):
        """Filter items and show/hide popup based on search text."""
        search_text = self.var.get().strip()
        
        # Get filtered items from callback
        filtered = self.items_callback(search_text)
        
        # Show popup if there are results and user is typing
        if filtered and search_text:
            self._show_popup(filtered)
        else:
            self._hide_popup()
    
    def _show_popup(self, items: List[str]):
        """Show popup with filtered items."""
        if not self.popup:
            # Create popup window
            self.popup = tk.Toplevel(self.entry)
            self.popup.wm_overrideredirect(True)  # Remove window borders
            
            # Frame with border
            frame = ttk.Frame(self.popup, relief='solid', borderwidth=1)
            frame.pack(fill='both', expand=True)
            
            # Scrollbar
            scrollbar = ttk.Scrollbar(frame)
            scrollbar.pack(side='right', fill='y')
            
            # Listbox
            self.listbox = tk.Listbox(
                frame,
                height=8,
                yscrollcommand=scrollbar.set,
                font=("Helvetica", 9),
                selectmode=tk.SINGLE
            )
            self.listbox.pack(fill='both', expand=True)
            scrollbar.config(command=self.listbox.yview)
            
            # Bind click for selection
            self.listbox.bind('<Button-1>', self._on_listbox_click)
            self.listbox.bind('<Return>', self._on_select_item)
        
        # Update items
        self.listbox.delete(0, tk.END)
        for item in items:
            self.listbox.insert(tk.END, item)
        
        # Select first item
        if items:
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(0)
            self.listbox.activate(0)
        
        # Position popup below entry
        try:
            x = self.entry.winfo_rootx()
            y = self.entry.winfo_rooty() + self.entry.winfo_height()
            width = self.entry.winfo_width()
            self.popup.geometry(f"{width}x200+{x}+{y}")
            self.popup.deiconify()
        except tk.TclError:
            # Widget not yet visible, ignore
            pass
    
    def _hide_popup(self):
        """Hide popup."""
        if self.popup:
            self.popup.withdraw()
    
    def _on_down(self, event):
        """Navigate down in listbox."""
        if self.listbox and self.popup and self.popup.winfo_viewable():
            current = self.listbox.curselection()
            if current:
                next_index = min(current[0] + 1, self.listbox.size() - 1)
            else:
                next_index = 0
            
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(next_index)
            self.listbox.activate(next_index)
            self.listbox.see(next_index)
            return 'break'
    
    def _on_up(self, event):
        """Navigate up in listbox."""
        if self.listbox and self.popup and self.popup.winfo_viewable():
            current = self.listbox.curselection()
            if current:
                prev_index = max(current[0] - 1, 0)
                self.listbox.selection_clear(0, tk.END)
                self.listbox.selection_set(prev_index)
                self.listbox.activate(prev_index)
                self.listbox.see(prev_index)
            return 'break'
    
    def _on_select_item(self, event):
        """Select item from listbox."""
        if self.listbox and self.popup and self.popup.winfo_viewable():
            selection = self.listbox.curselection()
            if selection:
                selected_text = self.listbox.get(selection[0])
                self.var.set(selected_text)
                self._hide_popup()
                
                # Call on_select callback if provided
                if self.on_select:
                    self.on_select(selected_text)
                
                # Move focus to next widget
                self.entry.event_generate('<Tab>')
                return 'break'
    
    def _on_escape(self, event):
        """Close popup with ESC."""
        self._hide_popup()
        return 'break'
    
    def _on_focus_out(self, event):
        """Hide popup when focus leaves (with delay for click)."""
        self.entry.after(200, self._hide_popup)
    
    def _on_listbox_click(self, event):
        """Handle click on listbox item."""
        index = self.listbox.nearest(event.y)
        if index >= 0:
            selected_text = self.listbox.get(index)
            self.var.set(selected_text)
            self._hide_popup()
            
            # Call on_select callback if provided
            if self.on_select:
                self.on_select(selected_text)
            
            self.entry.focus_set()
        return 'break'
    
    def grid(self, **kwargs):
        """Grid the entry widget."""
        self.entry.grid(**kwargs)
    
    def pack(self, **kwargs):
        """Pack the entry widget."""
        self.entry.pack(**kwargs)
    
    def focus_set(self):
        """Set focus to entry."""
        self.entry.focus_set()
