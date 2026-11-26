# NerazimNet v0.4.1

Dashboard rebuilt as real widgets — per-cell styling, colored action
buttons, resizable columns — plus form polish throughout the dialogs.

## Features

- **Widget-row dashboard.** The tunnel list is no longer a Treeview —
  every row is real `CTk` widgets, so each cell carries its own styling:
  white bold hostnames, tinted status text, dim route text, and colored
  action chips (green start, red stop, disabled gray, teal menu).
- **Resizable, sortable columns.** Draggable grips on the Status and
  Route headers; click-to-sort headers; a vertical rule separates the
  name column from the data zone. Route is elastic so long
  `port → host:port (+N extra)` strings get room; name is fixed-width
  and hugs the hostnames.
- **Density tiers.** Toolbar buttons, the filter box, column widths and
  the legend all densify together as the window narrows — compact labels
  first, then icon-only — instead of columns starving each other.
- **Row interactions.** Hover highlight, click-select, double-click
  toggle, right-click context menu (Start/Stop, Logs, Edit, Delete,
  Copy Address), collapsible server group bands with tunnel counts,
  and tooltips for clipped text and port details.
- **NydusNet-style toolbar.** Real PNG icons with `compound="left"`,
  `#DCE4EE` gray-white button text, and the dark solid glyphs NydusNet
  draws on its colored buttons.
- **Multiline form fields.** Extra Service Ports and Allowed IPs are
  now real multiline boxes (one entry per line, comma-joined on save),
  and the invite paste/display and recovery-key fields wrap instead of
  scrolling horizontally. Enter inserts newlines; Tab still traverses.

## Fixes

- **Placeholder hints restored.** The CTkEntry → ttk conversion had
  silently dropped every `placeholder_text` (ttk has none). A new
  `PlaceholderEntry`/`PlaceholderText` pair paints dim hints again —
  `e.g., 'app.example.com'`, `'localhost:8080'`, `'10r/s'` — on all
  dialog and settings fields.
- **Empty dropdowns.** `ttk.Combobox` doesn't auto-display the first
  value like `CTkOptionMenu` did, so new tunnels opened with blank
  Server/Client fields and failed validation. Both now default-select.
- **Column bounce & button pulse.** Solved at the root: every cell sits
  in a `pack_propagate(False)` wrapper so content can never expand a
  column, columns are purely spec-driven (no measurement races), and
  refresh diffs each widget's values so identical writes never trigger
  a CTk redraw. The whole-table rebuild is fingerprinted too — an
  unchanged sync degrades to an in-place refresh with zero widget churn.
- **Blank dashboard on launch.** Plain `tk` widgets were getting CTk
  `(light, dark)` color tuples; they now get resolved palette colors.
- **Row height.** ~50px rows tightened to ~36px (scaled by widget
  scaling), band 30px, buttons 26px — much closer to NydusNet density.
