# Actual-page media index

These images are captured from the actual application, using isolated
synthetic fixture data. They contain no private profile, credentials,
notification address or real report. Fixture output is not a real search/model
result, email receipt or candidate installation.

## README GIFs

| GIF | Sequence | Encoding |
| --- | --- | --- |
| [Intro](images/fixture-intro.gif) | Original full-screen intro, glass reveal, return to archive array | 1120×654, 146 frames, 22.96 seconds, 2× playback, about 9.55 MiB |
| [Reading](images/fixture-reading.gif) | Keyboard selection, open/extract action, frosted-to-clear reveal and saved fixture reading | 1280×748, 38 frames, 14.34 seconds, 1× playback, about 1.81 MiB |

Both were captured from the same desktop in-app browser at 1280×720. The source
rendering, model, materials, lights, camera and motion were not changed for the
recording. The GIFs add a separate 28px disclosure strip outside the page;
the intro is then resized. They use a shared 256-color palette per sequence
without dithering, retain actual captures, merge identical frames and shorten
the final idle wait. They contain no audio, interpolated frames or retouching.

Capture is variable-rate screenshot sampling, not a smooth screen video:
source-frame gaps reach 2.44 seconds during intro and 1.36 seconds during the
reading interaction. Brief transitions can therefore appear stepped. The intro
is accelerated, while reading keeps recorded timing. No claims about live
generation or rendering frame rate follow from these GIFs.

The complete selected takes (170 intro frames / about 46 seconds and 68 reading
frames / about 18 seconds) were visually reviewed, including material omitted
from the shortened ending. Every encoded GIF frame was decoded and compared
with its quantized source capture. No credentials, personal profile, real
feedback, notification, terminal or private report is visible. Source captures
remain outside Git and outside the release archive. See
[media-manifest.json](images/media-manifest.json) for file hashes and encoding
details. Static images below remain available for readability and reduced motion.

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
