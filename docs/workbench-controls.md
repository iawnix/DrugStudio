# Workbench controls and motion

Use the common control classes and tokens in `static/controls.css`. Primary and secondary actions have a 36px minimum height; compact toolbar actions have a 30px minimum height; icon buttons are 34px square. Icons are decorative Lucide SVGs at 16px (19px in the tool rail), with a consistent 1.75 stroke. Keep text labels on scientific actions, and give icon-only buttons translated accessible names.

Lucide 0.468.0 is packaged locally with its license and checksum in `static/vendor`. After inserting icon placeholders, call `DrugStudioControls.icons()`. This renders new placeholders only, preserving existing SVG nodes during status and streaming updates.

Wrap asynchronous button actions in `DrugStudioControls.busy(button, action)`. It supplies an accessible busy state, spinner and repeat-click guard without changing button geometry or overriding native disabled states owned by the form/workflow. Always await the operation; do not use a fixed timeout to simulate success. Preserve visible error messages and restore the control on failure.

Control feedback takes 120ms; drawer, dialog and terminal transitions take 180ms. Desktop drawers fade and move 14px; mobile drawers fade in place. Outgoing drawers become inert immediately. Escape closes a drawer and returns focus to its tool button when focus came from the drawer. Chat and editor nodes stay mounted throughout. Palette changes are atomic to avoid mismatched foreground/background flashes. Resizing has no delayed interpolation. System reduced-motion preferences disable motion, including looping indicators, while leaving status text and busy attributes visible.

Run the real-browser controls, preferences and workspace interaction suites after changing these behaviours. The harness deliberately blocks external assets, so missing local icons cannot be masked by a working CDN.
