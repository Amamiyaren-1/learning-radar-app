# Accepted visual baseline

The accepted original frontend is the only visual baseline. Preserve it during
release preparation and future synchronization; do not introduce substitute
geometry, contours or visual redesigns.

## Restored resources and behavior

The archive GLB, brand contour and derived login artwork are restored from the
accepted source. The archive model also matches the preserved independent
candidate. Its SHA-256 is
`dda42b9b3b471d11c69820a64084d254a64b27761287ab0e35dd8387ec0a0bed`.
`frontend/licenses/visual-baseline.json` records the resource hashes.

The original material loading, frosted/clear reveal, internal optics, studio
lights, camera, layout and motion are retained. The generic generator,
physical-material promotion helper and substitute-specific material tests are
removed. Tests retain the original model hash, real GLTFLoader cover-class
check and original reveal behavior; they do not require substitute layers.
No TypeScript assertion is treated as evidence of a runtime material class.
The original GLB's transmission/IOR extensions create its physical glass.

Executable TypeScript and effective CSS are checked against the accepted
frontend. English comments, public dependency/build fixes, license records and
the commercial-font exclusion are preserved. The private checkout and formal
services/data are not modified.

## Precise distribution distinctions

| Path | Evidence/scope | Minimum treatment or outstanding decision |
| --- | --- | --- |
| `frontend/public/fonts/NovecentoWide-{Normal,DemiBold,Bold}.woff2` (not included) | The supplied MyFonts webfont notice restricts use by parties without a license; no public redistribution grant was supplied. | Continue excluding binaries, purchase materials and old font-containing builds/history/packages. Keep the existing fallback stack; do not redesign typography or layout. |
| `frontend/public/assets/archive-cassette.glb` | Upstream README at `12cc5e4013acb9408f753ff71de6b8492299b7c8` explicitly includes author-owned GLB work in MIT and separately excludes underlying game visual-design rights. | Restore the accepted resource for local preparation and retain attribution. The author's grant is established; it does not settle separate underlying rights. Record that distinction for the distribution decision, without replacing geometry. |
| `frontend/src/reference/brand.ts`, `frontend/public/artwork/login-loop.svg` | MIT code/author-created work; upstream separately excludes game names, marks and their visual presentation. | Restore the accepted contour/artwork locally. Identify the separate mark/design scope before publication; do not infer a grant for third-party marks or silently redraw the symbol. |
| `frontend/public/fonts/MiSans-{Regular,Demibold}.woff2` | Existing unmodified application embedding under MiSans's own license. | Retain the font license and Xiaomi notice. Do not label it MIT or distribute a standalone font product. |
| `frontend/public/audio/{click,switch,open,back}.wav` | Existing generated tones/noise, documented in audio-source.md; upstream game-film samples are excluded by its grant. | Retain the already prepared generated sounds. No new audio redesign is part of this restoration. |

The Latin font fallback can change glyph shape, apparent case, weight, width,
wrapping and canvas label text. It is the documented remaining visual
difference from the private licensed-font installation. No commercial font is
copied to make screenshots appear identical.

## Acceptance and screenshots

Build the restored frontend and use isolated fixture data only. Capture the
actual pages at a recorded viewport and quality preset. Check viewport/canvas
dimensions before and after capture; do not use a full-page capture if it
temporarily changes vh-based scene height. README screenshots are refreshed
from these actual restored pages, without image retouching or model redesign.
Fixture success never proves real collection, model calls, SMTP, project
installation or deployment. Original Learning Radar code uses the approved
MIT license; third-party terms and the distinctions above remain unchanged.
Source publication does not authorize deployment or another visual redesign.
