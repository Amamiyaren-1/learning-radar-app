# Third-party and original materials

Original-code MIT is a proposal only; LICENSE-MIT.DRAFT.txt is not effective until owner approval. Existing third-party rights are independent. An upstream author's MIT grant cannot license another work's trademarks, imagery, characters, or recordings. A learning-only disclaimer is not distribution permission.

| Path/material | Source and license | Modification and public treatment |
| --- | --- | --- |
| `frontend/src/reference/` except `brand.ts` | LBEILC/RhineLabUI local snapshot; MIT, `frontend/licenses/RhineLabUI-MIT.txt` | Adapted motion/rendering/data code; copyright preserved. Snapshot hashes in reference-source.json are historical provenance, not current-byte equality. |
| `frontend/public/audio/atmosphere.ogg` | LBEILC original programmatic composition; upstream MIT and audio-source.md | Bytes unchanged; no game-film sampling, voices, or external recording claimed. Only this music layer included. |
| `frontend/public/audio/{click,switch,open,back}.wav` | Original `frontend/scripts/make-ui-sounds.py` | Generated tones/noise, not upstream game-film short samples. Proposed original-code license applies after approval. |
| `frontend/public/fonts/MiSans-{Regular,Demibold}.woff2` | Xiaomi official MiSans distribution; MiSans-license.pdf and NOTICE.txt | Unmodified application embedding, with notices; no adaptation/sublicensing/standalone font product. This application uses MiSans. The license is not MIT. |
| `frontend/public/assets/archive-cassette.glb` | Original make-public-assets.py generator | Generic rectangular archive card; renderer-compatible material identifiers. No upstream mesh, textures, game logos, or model lettering. |
| `frontend/src/reference/brand.ts`, generated `frontend/public/artwork/login-loop.svg` | Original mathematical cubic loop and existing MIT animation machinery | Generic loop without the old plus/minus branded mark; generated cross-sections use that loop. |
| `frontend/public/assets/radar-mark.svg` | Original Learning Radar radar circles/sweep | Retained; no game logo. |
| `three` and `@types/three` | Locked npm dependencies, MIT | Notices three-MIT.txt / types-three-MIT.txt; unchanged package sources. |
| `@kitlangton/rolling-number` | Locked npm dependency, MIT | rolling-number.txt retained. |
| Vite and bundled development dependencies | Locked npm packages, mostly MIT/BSD; package-specific exceptions | vite-LICENSE.md retained/updated with patched Vite distribution. Transitive notices in dependency inventory; no node_modules in review package. |
| TypeScript | Microsoft, Apache-2.0 | typescript-Apache-2.0.txt retained. |
| Frontend-design guidance attribution | Apache-2.0 notice frontend-design-Apache-2.0.txt | Preserve notice; private design skills and histories are not shipped. AI assistance is acknowledged without invented authorship percentages. |

The historical original `archive-cassette.glb` was explicitly named by upstream MIT documentation at commit `12cc5e4013acb9408f753ff71de6b8492299b7c8`. That does not resolve underlying game-related visual rights. This release copy therefore removes the original GLB, old brand contour, and derivative generated login artwork from all four reachable export commits, retaining author identities, dates, order, and English messages. Generic replacements are added transparently in a new preparation commit. Original candidate/backup are private; no private development history is imported.

Excluded: archive-assembly, Blender files, original film/PV, sampled typing audio, other music stems, commercial Novecento binaries, old paid-font builds, purchase metadata, private profiles/credentials/reports/diagnostics. Existing private-edition visuals are preserved outside this release copy. Public differences are limited to generic card/loop and commercial-font fallback; business behavior and Chinese text stay intact.

`EXPORT-MANIFEST.json` describes an historical export baseline, including assets later removed from reachable history. It is not the final byte manifest. FINAL-SOURCE-MANIFEST.json documents allowed differences and final source hashes; the package manifest adds the actual committed source SHA and build hashes. A material/privacy scan is evidence, not proof that every possible secret or right has been discovered. License scope and public visual differences remain review items before publication.
