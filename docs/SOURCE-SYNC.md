# Maintaining the private and public lines

Keep private development and the independent public main history. Never merge a private branch, mirror refs, clone the old private remote as public source, or restart public history for routine updates.

For each sync: fix an exact private source SHA, choose explicit general-code/test paths, review diffs against public HEAD, preserve public docs/licenses/assets, run isolated checks and privacy/history/package audit, then commit in English on public main. Record private-source SHA → allowed paths/public adjustments → checks → public SHA in a local nonpublic handoff. No private audit logs or conversation context enter public docs.

Protect README.md/README.en.md, AGENTS.md, CONTRIBUTING.md, docs/, LICENSE*, frontend/licenses/, generic public artwork/mesh, manifests, and public dependency patches. Private Chinese docs and paid-font builds must not overwrite them. Translate incoming comments/docstrings before applying; preserve Chinese runtime/test data. If public and private code both changed, review and port the specific diff instead of copying the whole file.

`scripts/public_export.py` is a historical fresh-export tool, not a synchronization command for an existing public repo. Its old hashes are provenance only. Future exports require a new allowlist review; routine synchronization continues the existing public history. Source/build/package manifests must be regenerated after relevant changes. Push only an explicitly approved main SHA, never --all/--mirror/force or backup refs.
