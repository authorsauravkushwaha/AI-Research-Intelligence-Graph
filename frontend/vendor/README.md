# Vendored front-end libraries

These files are vendored so the NEXUS front end runs **offline**, with no CDN or
third-party hosted service at demo time:

* `three.module.js` — [three.js](https://threejs.org) r160 (MIT licence,
  Copyright © 2010-2023 three.js authors). Used for the 3D knowledge graph.
* `OrbitControls.js` — three.js example control (MIT), with its bare `'three'`
  import rewritten to `'./three.module.js'`.

Both were fetched with `npm pack three@0.160.1` and are unmodified apart from that
one import path. To update: `npm pack three@<version>` and re-copy
`build/three.module.js` and `examples/jsm/controls/OrbitControls.js`.
