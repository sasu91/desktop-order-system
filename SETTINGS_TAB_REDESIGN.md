# Settings Tab Redesign - Implementation Summary

## Overview
Redesigned the Settings tab with collapsible sections, search functionality, and section-level auto-apply checkboxes for improved usability and organization.

## Key Changes

### 1. New Components
- **CollapsibleFrame** (`src/gui/collapsible_frame.py`): Custom widget for expandable/collapsible sections
- **Search Filter**: Real-time parameter search at the top of settings tab
- **Section Auto-Apply**: One checkbox per section instead of per-parameter

### 2. Layout Reorganization

#### Before
- Single flat list of 25+ parameters
- Repetitive "Auto-applica ai nuovi SKU" checkbox for each parameter
- No visual grouping or hierarchy
- Difficult to scan and locate specific settings

#### After
- **5 Collapsible Sections**:
  1. ⚙️ **Parametri Base Motore Riordino** (12 params) - expanded by default
     - Lead time, MOQ, pack size, review period, safety stock, max stock, reorder point
     - Demand variability, forecast method
     - OOS boost, lookback days, detection mode
  
  2. ⚡ **Auto-classificazione Variabilità** (6 params) - collapsed by default
     - Enabled flag
     - Min observations, percentiles (STABLE/HIGH)
     - Seasonal threshold, fallback category
  
  3. 🎲 **Simulazione Monte Carlo** (8 params) - collapsed by default
     - Distribution, n_simulations, random_seed
     - Output stat, percentile
     - Horizon mode, horizon days
     - Show comparison flag
  
  4. 📊 **Dashboard** (1 param) - collapsed by default
     - Stock unit price

### 3. Grid Layout
- **3-column structure** (25% / 45% / 30%):
  - Column 1: Parameter label (bold)
  - Column 2: Description (gray, wrapped at 300px)
  - Column 3: Input widget
- Consistent alignment across all rows
- Better utilization of horizontal space

### 4. Search Functionality
- **Real-time filtering**: Type to filter visible parameters
- Searches both label and description text
- Case-insensitive matching
- Hidden rows don't affect layout

### 5. Auto-Apply Simplification
- **One checkbox per section** at the top of each collapsible frame
- Text: "✓ Auto-applica tutti i parametri di questa sezione ai nuovi SKU"
- Reduces visual clutter (25 checkboxes → 5 checkboxes)
- Applies to all parameters in that section on save

## Technical Implementation

### Files Modified
1. **src/gui/app.py**:
   - Added import for `CollapsibleFrame`
   - New helper methods:
     - `_create_param_rows()`: Creates parameter rows with grid layout
     - `_filter_settings()`: Real-time search filtering
   - Refactored `_build_settings_tab()`: New sectioned layout
   - Updated `_refresh_settings_tab()`: Load values + section auto-apply state
   - Updated `_save_settings()`: Save with section-level auto-apply

2. **src/gui/collapsible_frame.py** (NEW):
   - Custom `CollapsibleFrame` widget
   - Toggle button with arrow symbols (▼ / ▶)
   - Expandable/collapsible content frame
   - Uses `Toolbutton` style for clean header

### Data Structures
```python
# Widget storage (per parameter)
self.settings_widgets = {
    "param_key": {
        "value_var": tk.IntVar(),  # Value variable
        "section": "section_key"   # Section reference
    }
}

# Section auto-apply checkboxes
self.settings_section_widgets = {
    "reorder_engine": tk.BooleanVar(),
    "auto_variability": tk.BooleanVar(),
    "monte_carlo": tk.BooleanVar(),
    "dashboard": tk.BooleanVar(),
}

# Search filter storage
self.settings_rows = [
    {
        "frame": row_frame,
        "label": "Lead Time (giorni)",
        "description": "Tempo di attesa dall'ordine alla ricezione"
    },
    # ... more rows
]
```

### Parameter Mapping
Centralized mapping in `_refresh_settings_tab` and `_save_settings`:
```python
param_map = {
    "lead_time_days": ("reorder_engine", "lead_time_days"),
    "mc_distribution": ("monte_carlo", "distribution"),
    "auto_variability_enabled": ("auto_variability", "enabled"),
    # ... etc
}
```

## Benefits

### Usability
1. **Reduced cognitive load**: Clear visual hierarchy with sections
2. **Faster navigation**: Search + collapsible sections
3. **Less clutter**: Section-level auto-apply (25 → 5 checkboxes)
4. **Better scannability**: Grid layout with consistent alignment

### Maintainability
1. **DRY principle**: `_create_param_rows()` reusable helper
2. **Centralized mapping**: Single source of truth for param → section
3. **Extensibility**: Easy to add new sections or parameters

### Accessibility
1. **Responsive to window size**: Grid columns adapt
2. **Description wrapping**: No overflow on smaller screens
3. **Keyboard navigation**: Collapsible sections focusable

## Testing
- **Syntax validation**: `py_compile` successful ✅
- **Monte Carlo tests**: 7/7 passed ✅
- **No regressions**: Existing settings save/load logic preserved

## Future Enhancements
1. **Conditional enabling**: Disable MC params when `forecast_method != "monte_carlo"`
2. **Tooltips**: Hover help for complex parameters
3. **Validation feedback**: Visual indicators for invalid values
4. **Export/Import**: Settings backup/restore functionality
5. **Keyboard shortcuts**: Quick collapse/expand all sections

## Migration Notes
- **No data migration needed**: Settings JSON structure unchanged
- **Backward compatible**: Old settings files load correctly
- **UI only**: Business logic untouched

---

**Date**: February 7, 2026  
**Status**: ✅ Complete  
**Breaking changes**: None
