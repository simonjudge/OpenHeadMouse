# Third-party notices and binary distribution

OpenHeadMouse's own code is licensed under the [MIT licence](LICENSE), copyright
2026 Simon Judge. That licence does not replace the licences of bundled libraries,
models, fonts, codecs or other assets.

## Dependency inventory

The build exports the exact installed versions and discovered licence/notice files
into `release-notices/`. This is an inventory of the build environment, not a claim
that every installed package is bundled or that every required notice was found.
Review PyInstaller's analysis and native-library dependencies for each platform.
Use a dedicated generated output directory: repeat exports replace their generated
licence files and refuse to overwrite an unrecognised nonempty directory.

| Component | Upstream licence / review notes |
| --- | --- |
| Python | PSF licence and notices for included components |
| NumPy, SciPy | BSD-style licences plus bundled numerical-library notices |
| PySide6, Shiboken6, Qt | LGPL/GPL/commercial alternatives; review the exact Qt modules and native plugins included |
| Qt Virtual Keyboard | GPL-3.0/commercial, not LGPL; currently automatically collected in all four Windows candidates and a blocking redistribution item |
| pyqtgraph | MIT |
| OpenCV / opencv-python | Apache-2.0 for current releases; preserve third-party notices and review included codecs |
| MediaPipe | Apache-2.0; preserve its LICENSE and NOTICE |
| pynput | LGPL-3.0 |
| ximu3 | MIT |
| PyOpenGL | BSD-style licence |
| IPython, ipywidgets (notebook only) | BSD-style licences |
| PyInstaller (build tool) | GPL with a bootloader exception; see its distribution terms |

Sources: [Qt for Python licensing](https://doc.qt.io/qtforpython-6/commercial/index.html),
[Qt third-party licences](https://doc.qt.io/qt-6/licenses-used-in-qt.html),
[pynput](https://github.com/moses-palmer/pynput),
[PyInstaller licence](https://pyinstaller.org/en/stable/license.html).
The installed version's own licence files are authoritative.
The Windows build analysis currently includes `Qt6VirtualKeyboard.dll`; its
[6.11.2 source licence declaration](https://github.com/qt/qtvirtualkeyboard/blob/v6.11.2/src/virtualkeyboard/qvirtualkeyboardinputcontext.cpp)
offers commercial or GPL-3.0-only terms. Exclude the unused module/plugin or resolve
those terms before distributing binaries. Also note that the installed PySide6
wheels only supplied a commercial licence file in their discovered package notices;
the generated inventory does not supply the missing LGPL/GPL texts or source.

## MediaPipe assets

- Face Landmarker task: version `1`, float16,
  [official download](https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task).
  SHA-256: `64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff`.
- Canonical face mesh: MediaPipe commit
  `a908d668c730da128dfa8d9f6bd25d519d006692`,
  [upstream asset](https://github.com/google-ai-edge/mediapipe/blob/a908d668c730da128dfa8d9f6bd25d519d006692/mediapipe/modules/face_geometry/data/canonical_face_model.obj).
  SHA-256: `8bac80443397e113f41a8b565ea72c59390bc031d9defab289dba7bc0c54e618`.
  The repository carries an Apache-2.0 licence.

Verify and retain the licence/model-card terms applicable to the complete
Face Landmarker bundle, not just MediaPipe's software licence. See the
[official model documentation](https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker).
Asset redistribution approval is a release gate until this is recorded.

## Binary release gate: not yet approved

The current single-file executables are retained at the maintainer's request.
**Do not publish binary assets until the LGPL and asset review is complete.**
In particular:

- Include the applicable LGPL/GPL licence texts and all required copyright notices;
  package metadata alone is not sufficient. Some wheels may omit required texts.
- Determine and satisfy the corresponding-source requirements for the exact
  LGPL components shipped. Merely linking to a moving upstream branch is not a
  substitute for a compliant source arrangement.
- Preserve recipients' ability to modify/recombine the LGPL components and debug
  those modifications. Document a verified rebuild/relink path for the single-file
  bundles and provide the required application/source materials.
- Do not impose terms that prohibit reverse engineering for debugging modifications
  to LGPL components. Do not claim that an MIT licence removes these obligations.
- Review GPL-only Qt modules, native plugins, codecs and platform-specific extras;
  exclude anything whose terms are incompatible with the intended distribution.

This document and the generated inventory help the review; they are not a legal
opinion or a declaration of compliance. Release workflows create drafts only.
