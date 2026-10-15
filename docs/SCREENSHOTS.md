# Actual-page media index

These images are captured from the actual application, using isolated
synthetic fixture data. They contain no private profile, credentials,
notification address or real report. Fixture output is not a real search/model
result, email receipt or candidate installation.

## README GIFs

| GIF | Sequence | Encoding |
| --- | --- | --- |
| [Intro](images/fixture-intro.gif) | Original full-screen intro, glass reveal, return to archive array | 1120×654, 146 frames, 22.96 seconds, 2× playback, about 9.55 MiB |
| [Reading](images/fixture-reading.gif) | Owner-supplied fixture video: archive selection, extraction, reveal, reading and return | 960×540, 139 frames, 13.90 seconds, 1× playback, 10 fps, about 8.32 MiB |

The intro keeps its existing actual-page capture and encoding: 1280×720 source,
170 captured frames over about 46 seconds, a separate 28px disclosure strip,
1120×654 output, shared 256-color palette and 2× playback. Its variable-rate
screenshot sampling has source gaps up to 2.44 seconds; the final idle wait is
shortened. Its 146 encoded frames match the quantized actual captures.

The reading GIF replaces the earlier screenshot-sampled take with a video
supplied by the owner. It preserves the complete approximately 13.94-second
clip at original speed (13.90 seconds after GIF frame-time quantization),
including the return to the list. The 1920×1080 / 60 fps source is downscaled to
960×540 / 10 fps with a shared 96-color palette without dithering. Audio and
source-file metadata are removed. No crop, new caption strip, synthesized
scene frames, retouching, or application UI changes are applied.

The source clip shows synthetic fixture archives and a saved fixture report;
it does not show live generation. The full interval and all 139 final GIF
frames were reviewed. No credentials, personal profile, real feedback,
notification, terminal or private report is visible. Original video and local
conversion/review files remain outside Git and release archives. Intro and
static images are unchanged. See [media-manifest.json](images/media-manifest.json)
for file hashes and encoding details. Static alternatives below remain
available for readability and reduced motion.

The [historical excerpt](examples/historical-project.en.md) is a separate,
rewritten public-project analysis from 2026-09-23, with its original storage
state and execution limitations. It is not the synthetic report shown in GIFs.

## Static alternatives

| Image | Content | Capture |
| --- | --- | --- |
| [Sign-in](images/fixture-login.jpg) | Empty sign-in and restored original contour artwork | Desktop in-app browser, 1280×720 |
| [Home](images/fixture-home.jpg) | Actual restored original archive model and fixture identity | Desktop in-app browser, balanced quality |
| [Task](images/fixture-task.jpg) | Saved completed offline fixture task | Desktop in-app browser |
| [Report](images/fixture-report.jpg) | Saved fixture report with the restored original archive | Desktop in-app browser, balanced quality |
| [Projects](images/fixture-projects.jpg) | Synthetic project history and native filter form | Desktop in-app browser |

The accepted original model, contour, materials, lights, camera, layout and
motion are restored. Commercial Novecento binaries remain absent, so existing
MiSans/system fallback can change Latin text appearance and wrapping. See
PUBLIC-VISUALS.md for the exact baseline and resource-scope distinctions.

Captures use a fixed viewport clip; viewport and canvas dimensions are checked
before and after capture. No image retouching or model redesign is applied.
The local handoff records capture metadata and runtime resource hashes; the
artifact manifest binds the final source SHA and actual built assets. README
changes do not imply a new runtime build when its source/assets are identical.
Browser evidence is separate from native-form/HTTP fixture checks and does not
establish cross-browser, public HTTPS or deployment acceptance.
