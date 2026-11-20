KadroGen 2D adapter for Sketchling

Upstream: https://github.com/AnayGarodia/sketchling
Pinned source commit: 1cba8ad4dbec08b4346034c0655de0db9382f234

The browser bundle uses Sketchling Stroke/SketchNode, geometry helpers and
roughOptionsFor with Rough.js. It exposes only bounded 2D contours and fills.
KadroGen's adapter rasterizes these contours in installed Edge/Chrome. It does
not ship the upstream GSAP renderer, WebGL/Three.js pipeline, arbitrary TypeScript
execution or the full Sketchling animation vocabulary. Outline/colour reveal
clips are rendered by KadroGen locally with Pillow and FFmpeg.

No Node/npm install is required by application users. Developers can rebuild
assets/drawing/sketchling.js using scripts/build_drawing_bundle.mjs against a
checkout of the pinned upstream source with its npm build dependencies installed.
See LICENSE and DEPENDENCY_LICENSES.txt for redistribution notices.
