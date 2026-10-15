# Local review artifact and future deployment

`scripts/build_release_artifact.py` packages a clean approved public source HEAD and its actual frontend manifest. It verifies FINAL-SOURCE-MANIFEST hashes and includes a package manifest with source SHA, file sizes/hashes, and build resources. It omits .git, private data/configuration, node_modules, and commercial fonts. This is a local review archive, not deployment authorization.

```powershell
python scripts/build_release_artifact.py --candidate . --out data/review-artifacts/learning-radar-review.zip
```

Use a new output path inside this source root's ignored data directory. The archive includes a ready frontend/dist and source. Extract it into a new empty directory, run public_smoke.py, and stop only its own processes. Source installation still requires npm ci/build; do not confuse it with the prebuilt path.

Future deployment needs separate target/maintenance authorization, account/configuration review, a consistent SQLite/files backup and restore exercise, one executor, old task/notification review, same-version assets, Linux/systemd/Caddy acceptance, public TLS/Secure cookies, and rollback. Preserve old weekly scope, data/budgets/validation, and uncertain mail. Do not stop timers, reinitialize passwords, reset consumption, or resend notifications as release preparation. Windows loopback results do not cover these conditions.
