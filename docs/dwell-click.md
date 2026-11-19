# Dwell click

Tick **Dwell Click** in the Subsystems panel to click automatically when the pointer remains
inside the configured movement tolerance (default 10 pixels) for the dwell time (default 1
second). A translucent red ring around the cursor fills clockwise as the dwell completes. After
a click, the pointer must leave the activation area before another click can occur.
The main window shows **Dwell Click Settings** only while **Dwell Click** is ticked.
Unticking it hides the settings without discarding their values; tray settings remain available.

Ticking the box only arms the feature. Dwell clicking starts only once the input provider is
actually controlling the cursor (mouse control active and head data moving the pointer), not
merely when **Start** is pressed or tracking is resumed. It stops whenever mouse control is
switched off, paused by the toggle hotkey or corner failsafe, or suspended during initial
calibration.

If the pointer leaves an activation area and returns within the configurable resume time
(default 0.5 seconds), progress continues from where it paused. Dwell time, movement tolerance,
and resume time can be changed in the main window and are persisted in `config.json`.

The timer ring's appearance is also configurable: **Diameter** (size of the circle, default 40
pixels), **Thickness** (width of the timer segment, default 4 pixels), and **Colour** (click
the swatch to open a colour picker). These are saved as `ring_diameter_px`,
`ring_thickness_px`, and `ring_color` under `ui.dwell_click` in `config.json`, and the same
options appear in the tray's dwell-click settings dialog.

The module can also run independently of OpenHeadMouse:

```
python -m dwell_click
python -m dwell_click --dwell-time 1.5 --movement-tolerance 12 --resume-time 0.75
python -m dwell_click --diameter 60 --thickness 8 --color "#00a0ff"
python -m dwell_click --zoom --zoom-width 300 --zoom-height 200 --zoom-scale 4
```

Both standalone mode and OpenHeadMouse provide a cross-platform system-tray icon. Its menu starts
or stops dwell clicking, opens the dwell-click settings dialog, and exits standalone mode.
Left-clicking the tray icon opens the settings dialog directly; right-clicking opens its menu.
The settings dialog also has a **Start/Stop Dwell Clicking** button. It stays open when
the button is used and follows changes made through the tray or OpenHeadMouse lifecycle.
On desktops without a system tray, standalone mode opens the settings window instead;
**Apply** keeps those controls open, and **Close** exits the application. Errors use a dialog
when tray notifications are unavailable.

**Click Context Menu** is optional and disabled by default. Enable it in the main window's
dwell settings or the tray settings dialog. A completed dwell opens six icons around
the original click point: **left click** at 135 degrees (upper-left), **zoom** at 90 degrees
(above), **right click** at 45 degrees (upper-right), **double click** at 225 degrees
(lower-left), **pause** at 315 degrees (lower-right), and **Snap to** at 270 degrees
(below). Dwell on an icon to choose it.
Each icon samples the screen beneath it when the menu opens: dark backgrounds use white
artwork and a 20%-opacity white backing; light backgrounds use dark artwork and a
20%-opacity black backing. Rounded corners remain transparent and active choices have a
contrasting blue outline. If screen sampling is unavailable, a warning is logged and the
application colour scheme is used. **Icon size** sets each square target's size
(default 54 pixels, 50% larger than the original 36; range 24-200), with artwork scaled
proportionally. **Distance** sets P, the distance to each icon centre (default 60 pixels;
minimum 48). Spacing grows when needed to prevent larger targets overlapping.
The group shifts together near screen edges to keep the icons visible; spacing is reduced
if the configured distance would make the group wider or taller than the display.
When the dwell target is on the taskbar (outside the screen's usable area), the menu is
placed at least 260 pixels away from the taskbar, clear of the window previews that pop up
there; the zoom box is moved clear in the same way (it still magnifies the taskbar area). Windows draws those previews above all normal programs, even always-on-top ones;
see the UIAccess next step below.

Left, right and double click are the click buttons ("L/R" below includes double click).
The first L/R choice is remembered across menu openings. Each new menu has its own original
target: selecting an icon clicks that target, not the icon or a target from a previous menu.
The menu closes before clicking and the cursor stays at the clicked location; it is not
warped back to the icons. Menu and zoom clicks rest on the target for about 120 ms before
pressing, so custom-drawn title-bar buttons (e.g. browser or Windows 11 app close buttons)
register the hover and accept the click.

| Menu choice | Effect | Menu |
| --- | --- | --- |
| First L or R selection | Click the original target once using that button; remember the choice | Closes |
| Same L/R at a later target | Lock that button and click this target once | Closes |
| Already-locked L/R icon | Unlock that button, clear its remembered choice; no click | Closes |
| Different L/R while a button is locked | Unlock the old button and click once with the new choice; choosing it again later locks it | Closes |
| First zoom selection, with L/R already chosen | Magnify this pending click and click with the locked or remembered L/R | Closes |
| First zoom selection, no L/R chosen yet | Highlight zoom and wait; choosing L or R then magnifies and clicks with that button | Stays open |
| Second zoom selection (a later menu) | Lock zoom independently of L/R; magnify and click as above | Closes, or stays open if no L/R is chosen |
| Already-locked zoom icon | Unlock zoom only; no click | Closes |
| Zoom selected after an automatic click was already sent | Arm zoom for the next target, or lock it on the second selection; never repeat the completed click | Closes |
| Pause | Cancel the pending click and pause dwell clicking; locks are kept | Closes |
| Snap to | Toggle cursor snapping to nearby clickable controls; no click | Closes |
| Dwell outside an unlocked menu | Cancel without clicking | Closes |

**Snap to** is off by default. Use **Start with Snap to enabled** in the main window or
tray settings dialog to change the startup default. Dwell on its crosshair icon to toggle
the current state; a blue outline indicates that it is enabled. While dwell clicking is active, the pointer snaps to the
centre of a nearby enabled control exposed by the application's accessibility API, when its
centre is within the configured radius (default 48 px; adjustable from 8 to 500 px).
Move away to leave a target; head-driven movement stays snapped until the head target
leaves that radius. Snapping does not activate the
control, and is suspended while paused or using the menu or magnifier. It preserves
click-button and zoom choices. Stopping dwell clicking or changing settings restores the
configured startup default.
On Windows, **Windows method** selects native UI Automation (UIA, the default) or the
previous MSAA point-lookup implementation. UIA gets the element at each sampled point,
checks its enabled state and supported action patterns (such as Invoke and Toggle), and
walks to its control-view ancestors when needed. MSAA uses `AccessibleObjectFromPoint` and
can also reach some controls exposed through the UIA-to-MSAA bridge. Compare both methods
for target applications; either can miss controls that do not expose usable accessibility
information. Increasing the radius only helps with exposed controls and does not discover
inaccessible ones. The selected method is saved as `snap_backend` under `ui.dwell_click`.
macOS requires Accessibility permission for OpenHeadMouse or its Python executable.
Linux requires an X11 session, AT-SPI 2 (`libatspi2.0-0`) and desktop accessibility enabled;
snapping is restricted to the active window to avoid covered background controls.
Custom-drawn controls that do not expose accessibility actions cannot be detected.
Accessibility queries run outside the UI thread; failures turn snapping off and report an error.

**Pause** stops all dwell clicking and shows a floating **Resume** (play) button in the
bottom-right corner of the screen. Dwell on it to resume hands-free, or use **Start Dwell
Clicking** in the tray menu or settings dialog. While paused the tray shows Start; the main
window's Dwell Click checkbox stays ticked. If Resume appears under the pointer, move off it
and back before dwelling. Stopping dwell clicking also clears the pause.

Locked options have blue outlines. Future target dwells use the selected button directly
when L/R is locked, opening the magnifier first when zoom is also locked. If only zoom is
locked, the menu still opens so L or R can be chosen for the zoomed click.
After these automatic clicks, the menu appears for **Stay** seconds (S, default 3), allowing
unlocking or a different choice. Switching to the other L/R intentionally sends one click
with the newly selected button at that menu's target, even if the previous automatic click
was already sent. Dwelling outside this timed menu performs the next automatic click.
The dismissal timer pauses during an icon dwell so an action can finish near the deadline.
If the menu opens underneath the pointer, leave the icon and re-enter it before selecting;
remaining stationary cannot immediately unlock a newly locked option.

**Use zoom (start locked)** seeds the independent zoom lock when starting dwell clicking.
Menu selections change runtime locks only: they do not toggle the checkbox or save locks.
For example, unlocking zoom in the menu leaves the checkbox checked; restarting restores
the configured zoom default. Stopping or changing settings clears remembered selections and
L/R locks, and reapplies that zoom default.

OpenHeadMouse persists `snap_enabled`, `snap_radius_px`, `snap_backend`, `context_menu_enabled`,
`context_menu_distance_px`, `context_menu_size_px`, and `context_menu_lock_time_s` under
`ui.dwell_click`. Standalone mode also accepts
`--context-menu --menu-distance 60 --menu-size 54 --menu-lock-time 3`.
After zoom opens, move away from the icon-selection position before dwelling on the image;
remaining still will not complete or cancel the precise click.

**Zoom clicking** is optional and disabled by default. Enable it in the main window or tray
settings to start with zoom locked, or select zoom once from the context menu. The magnifying
dwell opens a borderless screenshot around that point without
clicking the application. Move the pointer beyond the movement tolerance, then dwell on the
desired pixel inside the box to complete the selected L/R click at the original screen location
(left click when running without the context menu).
Dwelling outside the box cancels without clicking. Simply remaining still after the box opens
does not complete a click. The same dwell time and tolerance apply to both stages.

**Width** and **Height** set the displayed box size (default 300 by 200 pixels);
**Scale** sets magnification (default 4x, without image smoothing). The box and captured area
are kept on the pointer's display and the box is reduced to fit smaller screens. These sizes
use Qt screen coordinates (logical pixels on scaled displays); selection maps back to
individual captured screen pixels, including on high-DPI displays. OpenHeadMouse saves
`zoom_enabled`, `zoom_width_px`, `zoom_height_px`, and `zoom_scale` under `ui.dwell_click`
in `config.json`. Standalone settings apply for the current run, with command-line options
available as above. Stopping dwell clicking or changing settings cancels any pending zoom click.
Screen capture must be permitted by the OS; capture failures are logged and shown through
a tray notification rather than falling back to an unzoomed click. Pointer-control permission
is also required; if the cursor cannot reach the selected point, the click is cancelled and
an error is reported.

## UIAccess (Windows)

**Possible next step:** Taskbar previews, the Start menu and windows of
programs running as administrator sit in Windows z-order layers above every normal program,
so the dwell menu can be hidden by them and clicks into elevated windows are blocked.
Accessibility software such as OptiKey avoids this with *UIAccess*, which lets an app
draw above those layers and interact with elevated windows without itself running as
administrator. Requirements:

* Manifest flag `uiAccess="true"` (PyInstaller: `uac_uiaccess=True` in `EXE(...)` in the
  `.spec` file). Make it opt-in for release builds only: an unsigned UIAccess exe will not
  start ("A referral was returned from the server").
* Authenticode-sign the exe with a certificate trusted by the installing PC. Options:
  * Trusted, no extra steps for users: an open-source code-signing certificate (e.g.
    Certum Open Source, as OptiKey uses), SignPath Foundation (free for open-source
    projects) or Azure Trusted Signing.
  * Self-signed (for testing on your own PCs): each PC must import the certificate into
    *Local Machine > Trusted Root Certification Authorities* (admin rights). That PC
    then trusts anything signed with the key, so keep the private key safe and out of
    the repository.
* Install the exe in a secure location such as `C:\Program Files\...`; by default Windows
  refuses to start a UIAccess exe from user folders such as Downloads.

Running Dwell Click as administrator is a simpler interim workaround for clicking into
elevated windows, but it does not lift the menu above taskbar previews or the Start menu.

