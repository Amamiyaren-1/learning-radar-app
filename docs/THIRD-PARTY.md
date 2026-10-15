# Third-party and original materials

Original-code MIT is a proposal only; LICENSE-MIT.DRAFT.txt is not effective until owner approval. Existing third-party rights are independent. An upstream author's MIT grant cannot license another work's trademarks, imagery, characters, or recordings. A learning-only disclaimer is not distribution permission.

| Path/material | Source and license | Modification and public treatment |
| --- | --- | --- |
| `frontend/src/reference/` including `brand.ts` | LBEILC/RhineLabUI local snapshot; MIT, `frontend/licenses/RhineLabUI-MIT.txt` | Accepted original motion/rendering/contour restored; copyright preserved. The source grant does not license underlying third-party marks/designs. Historical hashes remain provenance, not universal current-byte equality. |
| `frontend/public/audio/atmosphere.ogg` | LBEILC original programmatic composition; upstream MIT and audio-source.md | Bytes unchanged; no game-film sampling, voices, or external recording claimed. Only this music layer included. |
| `frontend/public/audio/{click,switch,open,back}.wav` | Original `frontend/scripts/make-ui-sounds.py` | Generated tones/noise, not upstream game-film short samples. Proposed original-code license applies after approval. |
| `frontend/public/fonts/MiSans-{Regular,Demibold}.woff2` | Xiaomi official MiSans distribution; MiSans-license.pdf and NOTICE.txt | Unmodified application embedding, with notices; no adaptation/sublicensing/standalone font product. This application uses MiSans. The license is not MIT. |
| `frontend/public/assets/archive-cassette.glb` | LBEILC author-owned modeling explicitly covered by the later upstream MIT grant | Accepted original GLB restored, SHA-256 `dda42b9b3b471d11c69820a64084d254a64b27761287ab0e35dd8387ec0a0bed`; separate underlying design-rights exclusions retained. No substitute geometry. |
| `frontend/src/reference/brand.ts`, generated `frontend/public/artwork/login-loop.svg` | Accepted original MIT contour/animation and its derived artwork | Original contour/symbol behavior restored; underlying mark/design scope remains separately identified in PUBLIC-VISUALS.md. No automatic redrawing. |
| `frontend/public/assets/radar-mark.svg` | Original Learning Radar radar circles/sweep | Retained; no game logo. |
| `three` and `@types/three` | Locked npm dependencies, MIT | Notices three-MIT.txt / types-three-MIT.txt; unchanged package sources. |
| `@kitlangton/rolling-number` | Locked npm dependency, MIT | rolling-number.txt retained. |
| Vite and bundled development dependencies | Locked npm packages, mostly MIT/BSD; package-specific exceptions | vite-LICENSE.md retained/updated with patched Vite distribution. Transitive notices in dependency inventory; no node_modules in review package. |
| TypeScript | Microsoft, Apache-2.0 | typescript-Apache-2.0.txt retained. |
| Frontend-design guidance attribution | Apache-2.0 notice frontend-design-Apache-2.0.txt | Preserve notice; private design skills and histories are not shipped. AI assistance is acknowledged without invented authorship percentages. |

The original `archive-cassette.glb` is explicitly named by upstream MIT documentation at commit `12cc5e4013acb9408f753ff71de6b8492299b7c8`. That grant covers the author's own work and separately excludes underlying game-related names, marks, visual designs and recordings. The accepted original resources define the current visual baseline. No private development history is imported.

Do not describe the entire original model as unlicensed: the later explicit author grant supersedes an older snapshot's narrower wording. It also cannot grant third-party original-work rights. PUBLIC-VISUALS.md lists exact affected paths, evidence and minimum suggested decisions. Those notes are not authorization to redesign, remove geometry, replace a contour or alter the overall visual baseline. Distribution-scope decisions remain distinct from local restoration and from the original-code license proposal.

Excluded: unused archive-assembly/Blender files, original film/PV, sampled typing audio, other music stems, commercial Novecento binaries, old paid-font builds, purchase metadata, private profiles/credentials/reports/diagnostics. The private edition is unchanged. The remaining documented visual difference is commercial-font fallback; original model/materials/lights/camera/layout/motion and Chinese business behavior are preserved.

`EXPORT-MANIFEST.json` describes an historical export baseline, including assets later removed from reachable history. It is not the final byte manifest. FINAL-SOURCE-MANIFEST.json documents allowed differences and final source hashes; the package manifest adds the actual committed source SHA and build hashes. A material/privacy scan is evidence, not proof that every possible secret or right has been discovered. License scope and public visual differences remain review items before publication.
