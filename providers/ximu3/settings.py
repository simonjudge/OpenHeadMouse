"""xIMU3 mounting presets: axes alignment and AHRS convention (2 = NED)."""

MOUNTING_ORIENTATIONS = {
    "LHS-Horizontal": ("+X-Z+Y", 2),
    "LHS-Vertical": ("+Y-Z-X", 2),
    "RHS-Horizontal": ("+X+Z-Y", 2),
    "RHS-Vertical": ("-Y+Z-X", 2),
}
