# Molecular inspection and result provenance

The workbench uses one 3Dmol scene adapter (`molecular-viewer.js`) for receptor inspection, ligand inspection, result overlays and molecular file previews. It does not launch another server. Structure choices come from the selected run's registered artifacts, ordered by preparation stage; they are not a directory scan. Open arbitrary PDB, mmCIF, PDBQT or SDF files through **Project files → 3D preview**.

## Inspecting structures

The observed sequence contains only protein residues with coordinates in the first coordinate model, including chain, residue number and insertion code. It is not a reconstructed full sequence. Click a residue to select it, Shift-click for a range in the same chain, and Ctrl/Cmd-click to add/remove residues. Clicking the three-dimensional structure also selects its residue. Alternate atom locations remain available through the `alt` selector.

Selection & display provides cartoon, sticks, lines and ball-and-stick, plus an independent van der Waals surface with adjustable opacity. Selections, labels, distance measurements and the docking box are separate from that surface. Measure distance by selecting exactly two atoms. Export the view as PNG. Saved selections, named selections, styles and camera state are browser-local and scoped to the project, run, structure checksum and pose.

## Selection language

This is a deterministic PyMOL-style **subset**, not a PyMOL interpreter. Unsupported syntax produces an error; there is no script execution.

```text
chain A and resi 45-60
chain "" and resi -1A
chain AB and resi 10+10A
resn ASP+GLU
name CA and resi 10+20
protein and not hydrogen
select ligand, resn LIG
select site, byres (protein within 5 of ligand)
show sticks, site
color orange, site
zoom site
```

Selectors include `all`, `none`, `protein`, `ligand`, `hydrogen`, `chain`, `resi`, `resn`, `name`, `elem`, `model` and `alt`, with `and`, `or`, `not`, parentheses, `within` and `byres`. Internal `model` indices are zero-based: receptor 0, overlaid ligand 1. `resi 10` includes insertion variants, while `resi 10A` selects the explicit insertion code. `ligand` selects the overlaid pose, or non-water/non-common-ion hetero residues if no pose is present; define a named `ligand` selection when a more specific definition is needed.

## Applying a site

Preview computes a candidate box from the selected receptor heavy atoms and a padding distance; it changes the viewer/review draft only. **Apply to workflow** requires a reason and uses the existing docking-box review action. The decision records the structure artifact, checksum, coordinate model and residues in the audit trail. The server checks run ownership and rejects a different known docking receptor. Applying invalidates downstream docking/assessment state, retaining historical artifacts for a later rerun. Sending a selection to chat fills the composer without automatically submitting a model request.

## Result data

`web/results.py` provides the common task/pose projection consumed by the page and AI context. Success aliases are normalized, missing numeric values stay missing, and explicit failed-QC poses cannot become the best score. Task, ligand and pose counts are distinct. Scores are grouped by attempt, engine/version, receptor input and recorded protocol; a mixed-protocol run does not receive one global best score. Stale assessment reports cannot repopulate cleared tasks.

Each result retains individual pose record indices, QC details, receptor/pose artifacts and box/protocol. Receptor-to-PDBQT pairing requires the preparation record's PDBQT checksum and prepared-PDB checksum to match registered artifacts. The viewer opens the selected SDF record in those receptor coordinates. If this provenance is absent, the result remains inspectable but overlay is disabled rather than choosing an arbitrary receptor. Historical inputs are not rewritten.

The permanent limitations panel is replaced by collapsed methods/source details and contextual metric definitions.
