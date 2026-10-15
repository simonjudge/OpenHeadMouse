"""Hover explanations for the measures displayed by Fitts playback."""

from html import escape
import re
from urllib.parse import unquote

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QLabel, QToolTip


MEASURE_HELP = {
    "Access method": "The input device used to drive the cursor during this session.",
    "Duration": "Time from the first trial's start to the final click, in seconds.",
    "Trials": "Number of included reaches and clicks; hits landed inside the target.",
    "Error rate": "Percentage of included trials whose click missed the target. Lower is better.",
    "Mean MT": "Mean movement time from target presentation to click, in milliseconds.",
    "Elapsed MT": "Movement time elapsed in the current trial, capped at its click time.",
    "Ae": "Effective amplitude: mean actual straight-line distance from the start position to the click, in pixels.",
    "We": "Effective width: 4.133 times the sample standard deviation of click errors projected along the start-to-target axis, in pixels.",
    "IDe": "Effective index of difficulty: log2(Ae / We + 1), in bits. Reported as zero when We is zero.",
    "Throughput": "Effective index of difficulty divided by mean movement time in seconds, in bits/s. Combines speed and endpoint accuracy; higher is better.",
    "TRE": "Target re-entries: number of times the cursor re-enters the target after the first entry. Summary shows mean re-entries per usable trial.",
    "TAC": "Task-axis crossings: number of times the cursor crosses the straight line from start to target. Summary shows the mean count per usable trial.",
    "MDC": "Movement direction changes: reversals of motion perpendicular to the start-to-target axis (side-to-side corrections). Summary shows the mean count per usable trial.",
    "ODC": "Orthogonal direction changes: reversals of motion along the start-to-target axis (forward/backward corrections). Summary shows the mean count per usable trial.",
    "MV": "Movement variability: sample standard deviation of the path's perpendicular distances about their mean, in pixels. Lower means a steadier path.",
    "ME": "Movement error: mean absolute perpendicular distance of the cursor path from the start-to-target axis, in pixels. Lower is better.",
    "MO": "Movement offset: mean signed perpendicular distance of the path from the start-to-target axis, in pixels. The sign indicates which side of the axis.",
    "A": "Nominal target amplitude configured for this trial, in pixels.",
    "W": "Nominal target width (diameter for circular targets), in pixels.",
    "Time": "Playback time since the start of this session or synchronised task.",
    "Trial": "Current reach-and-click trial, numbered from one.",
    "Block": "Type of mouse task: circular, edge-target, or typing.",
    "Condition": "The amplitude/width combination or typing word being tested.",
    "Repetition": "Repeat number within this condition. Exclude first N repetitions removes practice repeats from the summary.",
    "Target": "Centre of the current target in the original test screen's pixel coordinates.",
    "Cursor": "Cursor position in the original test screen's pixel coordinates.",
    "Speed": "Instantaneous cursor speed between recorded cursor points, in pixels per second.",
    "Distance to target": "Straight-line distance from the cursor to the target centre, in pixels.",
    "Outcome": "HIT means the final click landed within the target; MISS means it did not.",
    "Yaw": "Head rotation left/right around the vertical axis, in degrees.",
    "Pitch": "Head rotation up/down around the lateral axis, in degrees.",
    "Roll": "Head tilt around the forward axis, in degrees.",
    "Head samples": "Number of recorded head poses used for the held initial-rest position.",
    "Cursor drift": "Standard deviation of horizontal (sigma x) and vertical (sigma y) cursor position during the initial-rest hold, in pixels.",
    "Max deviation": "Largest cursor distance from its mean position during the initial-rest hold, in pixels.",
    "Hold": "Recorded duration of the held position, in seconds, and number of cursor samples.",
    "Yaw range": "Absolute difference between mean yaw held at the left and right range-of-movement positions, in degrees.",
    "Pitch range": "Absolute difference between mean pitch held at the up and down range-of-movement positions, in degrees.",
    "Cursor H range": "Horizontal span between the extreme cursor positions held left and right, in pixels.",
    "Cursor V range": "Vertical span between the extreme cursor positions held up and down, in pixels.",
    "Left": "Mean held head yaw and its offset from initial rest, or the leftmost held cursor x position.",
    "Right": "Mean held head yaw and its offset from initial rest, or the rightmost held cursor x position.",
    "Up": "Mean held head pitch and its offset from initial rest, or the topmost held cursor y position.",
    "Down": "Mean held head pitch and its offset from initial rest, or the bottommost held cursor y position.",
    "Δrest": "Difference between this direction's mean held angle and the mean initial-rest angle, in degrees.",
}

_MEASURES = re.compile(
    r"(?<!\w)(" + "|".join(re.escape(key) for key in sorted(MEASURE_HELP, key=len, reverse=True)) + r")(?!\w)"
)


class MeasureLabel(QLabel):
    """A rich-text label whose individual measure names expose hover help."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.setTextInteractionFlags(Qt.LinksAccessibleByMouse)
        self.setOpenExternalLinks(False)
        self.setMouseTracking(True)
        self.linkHovered.connect(self._show_help)
        self.setText(text)

    def setText(self, text):
        parts = re.split(r"(<b>[^<]*</b>|<[^>]*>)", text)
        for i, part in enumerate(parts):
            if part.startswith("<b>") and part.endswith("</b>"):
                measure = part[3:-4].rstrip(":")
                if measure in MEASURE_HELP:
                    parts[i] = self._link(measure, part)
            elif not part.startswith("<"):
                parts[i] = _MEASURES.sub(lambda match: self._link(match[0], match[0]), part)
        super().setText("".join(parts))

    @staticmethod
    def _link(measure, text):
        return (
            f'<a href="{escape(measure, quote=True)}" '
            f'style="color: inherit; text-decoration: none;">{text}</a>'
        )

    def _show_help(self, measure):
        measure = unquote(measure)
        if measure in MEASURE_HELP:
            QToolTip.showText(QCursor.pos(), MEASURE_HELP[measure], self)
        else:
            QToolTip.hideText()
