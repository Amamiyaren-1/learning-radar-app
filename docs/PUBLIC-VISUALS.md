# Public visual resources and review

Publication is paused pending owner visual review. Existing README screenshots
are historical review images, not approval of the current candidate. Do not
replace them until the owner has viewed the actual repaired application.

## Replacement decisions

| Resource | Evidence and decision | Required visual continuity |
| --- | --- | --- |
| Original archive GLB | Upstream commit `12cc5e4013acb9408f753ff71de6b8492299b7c8` explicitly grants MIT for author-owned GLB work and separately excludes the underlying game visual designs. Use original generic geometry as a conservative distribution choice; do not claim all original modeling is unlicensed. | Keep the accepted envelope, thin frosted cover, transmissive rim, layered optics, thickness, rounded highlights, fine edges and hardware. |
| Original brand contour | The source's recognizable game-related mark has separate third-party rights; MIT code permission does not settle those rights. Keep the public mathematical loop and original Learning Radar radar mark. | Keep the existing animation machinery and line rendering. The loop/radar symbol is an intentional visible brand difference. |
| Generated login artwork | Generated sections inherit their source contour. Generate from the public loop rather than retaining sections of the old branded mark. | Preserve motion cadence and the annotation/line layout. |
| Commercial Novecento fonts | No redistribution grant supplied for this public package. Continue excluding font binaries, purchase materials and old builds containing them. | Existing fallback stack remains. Latin widths, weight, wrapping and label texture differ from the licensed private reference; this is a separate typography difference, not evidence of a material failure. |
| MiSans | Unchanged application embedding with its own license and notice. | Preserve Chinese interface/report/prompt text. |
| Renderer, reveal, studio lights, audio | Existing MIT notices and original generated public interaction sounds. No need to remove material or lighting behavior. | Keep camera, layout, extraction, projected-height frost/clear shader, studio environment and quality presets. |

The new geometry uses three parallel rounded optical cartridges, routed
substrate, recessed channels, registration grooves, amber inlays and four
fasteners. It does not reuse source vertices, source lettering, textures or
brand geometry. `make-public-assets.py` reads only Python standard-library
inputs and produces a deterministic GLB. Material names are an interface to the
existing renderer, not a claim that the old shape was copied.

## Actual materials

`physical-material.ts` clones an existing `MeshPhysicalMaterial`, preserving
glTF extension inputs. For a standard glTF PBR material it creates a new
`MeshPhysicalMaterial` and copies the common PBR base. A TypeScript assertion
is never used as construction. Unsupported non-PBR materials fail explicitly.

The GLB declares `KHR_materials_transmission`, `KHR_materials_volume`,
`KHR_materials_ior` and `KHR_materials_clearcoat`. The cover is a closed thin
volume with rolled edges. Interior optical layers use the existing opaque
capture/compositing strategy because WebGL transmission is not recursive.
The selected cover still uses `CardAppearance`'s projected-height reveal.
At full clarity its transmission reaches 0.985, thickness 0.018 and attenuation
distance 8; frost roughness remains controlled by the existing shader.

Run `python scripts/make-public-assets.py`, `npm test` and `npm run build` from
`frontend` (set `LEARNRADAR_PYTHON` if `python` is not the desired interpreter).
The tests parse the actual GLB with Three's GLTFLoader, check runtime class,
normal validity and required layers, and exercise the shared reveal uniforms
at clarity 0, 0.5 and 1. The opt-in fixture `?ui_probe` panel includes actual
selected and array material snapshots in its measured stats. It is never
enabled on non-fixture pages.

## Visual comparison protocol

Use an isolated source-only copy of the accepted private frontend for local
reference, never its formal database/config/service. Keep that copy and its
commercial fonts outside the public repository/package. Use the same fixture
seed, browser, viewport, balanced preset, refined lighting, selected identity
and settled page state for both sides. Compare home, selected archive and
report reading; retain full-page source captures and unmodified crops with
capture metadata. Record font/brand/internal-shape differences separately.

Browser inspection and automated checks do not substitute for the owner's
visual approval. No real model, project collection or SMTP is needed.
