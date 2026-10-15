# Maintaining the private and public lines

The owner's accepted original frontend is the sole visual baseline. The prior
generic model/contour policy and tests are withdrawn. Preserve the original
archive mesh, contour, derived login artwork, materials, lighting, camera,
layout and motion. Do not overwrite them with a generic asset or introduce
visual polishing during release preparation. See PUBLIC-VISUALS.md for hashes
and the remaining font/distribution distinctions.

Keep private development and the independent public main history. Never merge a private branch, mirror refs, clone the old private remote as public source, or restart public history for routine updates.

For each sync: fix an exact source SHA, choose explicit general-code/test paths, review diffs against public HEAD, preserve public docs/licenses and accepted visuals, run isolated checks and privacy/history/package audit, then commit in English on public main. Record source SHA → allowed paths/public adjustments → checks → public SHA in a local nonpublic handoff. No private audit logs or conversation context enter public docs.

Protect README.md/README.en.md, AGENTS.md, CONTRIBUTING.md, docs/, LICENSE*, frontend/licenses/, the accepted original artwork/mesh, manifests, and public dependency patches. Private Chinese docs and paid-font builds must not overwrite them. Translate incoming comments/docstrings before applying; preserve Chinese runtime/test data. If public and private code both changed, review and port the specific diff instead of copying the whole file. Commercial fonts, private config/data and private Git history remain excluded.

Record exact paths and source evidence for any concrete distribution restriction. Suggest only the minimum required treatment; do not infer permission to redraw a model, replace a contour or change the overall visual design. A rights review is separate from a visual redesign.

`scripts/public_export.py` is a historical fresh-export tool, not a synchronization command for an existing public repo. Its old hashes are provenance only. Future exports require a new allowlist review; routine synchronization continues the existing public history. Source/build/package manifests must be regenerated after relevant changes. Push only an explicitly approved main SHA, never --all/--mirror/force or backup refs.
