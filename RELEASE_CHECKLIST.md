# OpenHeadMouse 0.2.0-rc.1 release gate

This is a working publication checklist, not a claim of completed hardware or legal
validation. Keep the GitHub repository private and release drafts unpublished until
the applicable gates below are signed off.

## Automated preparation

- [x] Project MIT licence selected: copyright 2026 Simon Judge.
- [x] Public-facing branding and GitHub origin use OpenHeadMouse.
- [x] Current default config has no personal recordings path.
- [x] Notebook outputs/execution counts cleared; checkpoints/nested clone ignored.
- [x] Pattern-based current-tree and reachable-history privacy checks implemented.
- [x] Versioned model URLs, verified TLS and SHA-256 validation.
- [x] PR/push tests, per-test timeouts and release dependencies configured.
- [x] Tagged builds create drafts; hyphenated versions are marked prereleases.
- [x] Build-environment versions and discovered third-party notices exported.
- [x] Release checksum generation and no-input-control packaged smoke tests configured.
- [x] Python 3.12.15 / Qt 6.11.2: the final publication-only snapshot passed
  606 tests with exit code 0 (2026-10-22), including synchronised-window
  destruction, real OpenCV/MediaPipe imports, notice-export safety, deterministic
  mock sample periods and the approved additional notebook viewer changes.
  The earlier 592-test suite also passed in the worktree and Windows GitHub CI.
- [ ] Clean-checkout GitHub Actions run passes on each included build platform.
- [x] All four Windows EXEs/installers build and packaged smoke tests pass locally.
- [x] Windows build inventory archived (59 installed distributions, 116 discovered
  licence files); all nine executable/installer/notices checksums independently verified.
- [x] GitHub workflow syntax/semantics validated locally with actionlint 1.7.12.
- [x] Six JavaScript playback tests passed.

## Privacy/history decision

On 2026-10-22, the local pattern-based reachable-history check reported 47 flags:
24 personal-path flags, 14 saved-notebook-output flags and 9 non-default recordings
folder flags, across historical config, installer and notebook versions.
The checked token/private-key patterns did not match.
Historical notebook outputs were stream/widget-view/plain-text representations;
this is not a guarantee that they contain no sensitive information.

The maintainer chose to retain history, accepting historical personal paths and
outputs **after manual review**. No history was rewritten.

Gitleaks 8.30.1 was downloaded with checksum verification and run locally against
all reachable Git refs and a snapshot of publishable current files on 2026-10-22:
no secrets were reported. No source code or findings were uploaded to a scanning
service. This does not establish that historical study outputs are safe to publish.

- [ ] Maintainer manually reviews the historical notebook outputs and confirms
  that publication is acceptable.
- [ ] Review Git author/committer identities and email addresses that will become public.
- [ ] Confirm sample data, issue attachments and release assets contain no participant data.
- [ ] Enable GitHub private vulnerability reporting and available secret protection.
  The private-reporting API returned 404 while this repository was private; retry
  in repository settings when eligible. No paid security feature was enabled.

## Binary redistribution: blocking

- [ ] Verify complete Face Landmarker bundle redistribution terms/model cards.
- [ ] Review generated notices and native-library inventory for every shipped platform.
- [ ] Include applicable Qt/PySide, pynput, Python and other required licence texts.
- [ ] Establish a compliant corresponding-source arrangement for shipped LGPL libraries.
- [ ] Verify/document rebuilding or recombining LGPL components in single-file bundles.
- [ ] Confirm no incompatible GPL-only Qt modules/codecs/plugins are shipped.
- [ ] Resolve Qt Virtual Keyboard's GPL-3.0/commercial terms: PyInstaller's analysis
  currently includes `Qt6VirtualKeyboard.dll` in all four Windows editions.
  Remove the unused module/plugin or establish compatible redistribution terms
  before publishing binaries; the overall LGPL review remains required.

Single-file EXEs were retained by explicit maintainer choice, with binary publication
deferred pending this review. See [third-party notices](THIRD_PARTY_NOTICES.md).
Do not interpret generated licence exports or passing CI as legal approval.

## Windows clean-machine acceptance

Record tester, date, OS/build, edition/version, SHA-256 and result for each test.
Use a non-developer machine/account without Python, the repository or development
tools. Do not use real participant data.

| Check | Result / evidence |
| --- | --- |
| Full, HeadMouse-only, Dwell Click, Session Viewer install/start/uninstall | Pending |
| Portable EXEs start outside the repository, including a path containing spaces | Pending |
| Ordinary non-admin account; settings persist across restart; upgrade/uninstall policy | Pending |
| Offline startup after installation; bundled models available without downloading | Pending |
| Webcam permitted/denied/in use/missing/disconnected/reconnected | Pending |
| xIMU3 USB and configured/discovered Wi-Fi; dropout and reconnection | Pending |
| Initial calibration, recenter and modest gains before cursor control | Pending |
| Esc pause/resume and corner failsafe work while other apps are focused | Pending |
| Tracking loss stops cursor movement and dwell clicking safely | Pending |
| Dwell click, cancellation, context menu/zoom, and no unwanted clicks on exit | Pending |
| Multiple displays, negative origins and mixed DPI (100%, 150%, 200%) | Pending |
| Input over elevated apps gives a useful permissions error | Pending |
| Record/reopen JSON/CSV; Fitts result save and synchronised playback | Pending |
| Log paths/errors usable; no console window; clean shutdown | Pending |
| Keyboard-only navigation, readable labels, high-contrast/scaled UI | Pending |

## Experimental platforms

- [ ] macOS 14+ arm64: packaged startup, Camera/Accessibility/Input Monitoring,
  Gatekeeper behaviour, real OpenGL playback, AirPods Terminal Motion permission,
  Swift/Xcode requirements, helper lifecycle and disconnect/reconnect.
- [ ] Linux x64: Ubuntu 24.04/X11 startup, native shared-library requirements,
  input permissions, tray availability and `.deb` install/uninstall.
- [ ] Do not advertise Intel/universal macOS or Wayland support without testing.

## Release presentation and approval

- [x] Capture an application-only screenshot with no tracker/participant/desktop data.
- [x] Add the screenshot to the README.
- [ ] Read the Windows quick start literally on a clean machine.
- [ ] Review unsigned-download guidance; decide whether to obtain code-signing/notarization.
- [ ] Review draft release notes and known limitations.
- [x] Maintainer approved committing/pushing the complete worktree to private
  `main` for CI, without tagging, publishing or changing visibility.
- [x] First private push completed. With maintainer approval, deleted 25 pre-RC
  Actions artifacts (about 4.62 GiB) after CI hit the storage quota. GitHub may
  take 6-12 hours to recalculate usage; do not bypass failing uploads/test gates.
- [ ] Commit/push reviewed changes and confirm remote CI results.
- [ ] Invite a small RC tester group; collect results before claiming stable support.
- [ ] Approve source visibility and binary redistribution separately.
- [ ] Only then publish the approved draft; do not mark an RC as the stable/latest release.
