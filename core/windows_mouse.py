"""Mouse-input movement that restores Windows' touch-suppressed pointer."""

import ctypes

from pynput import mouse
from pynput._util.win32 import INPUT, INPUT_union, MOUSEINPUT, SendInput


class WindowsMouseController(mouse.Controller):
    def _position_set(self, pos):
        user32 = ctypes.windll.user32
        origin_x = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
        origin_y = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
        width = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
        height = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
        if width <= 0 or height <= 0:
            raise OSError("Windows did not report a usable virtual desktop")
        x, y = int(pos[0]), int(pos[1])
        # SendInput maps 0..65535 over the virtual desktop. Aim at the centre
        # of a pixel so rounding does not move the pointer to its neighbour.
        dx = max(0, min(65535, ((x - origin_x) * 65536 + 32768) // width))
        dy = max(0, min(65535, ((y - origin_y) * 65536 + 32768) // height))
        event = INPUT(
            type=INPUT.MOUSE,
            value=INPUT_union(
                mi=MOUSEINPUT(
                    dx=dx,
                    dy=dy,
                    dwFlags=MOUSEINPUT.MOVE | MOUSEINPUT.ABSOLUTE | 0x4000,  # VIRTUALDESK
                )
            ),
        )
        if SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)) != 1:
            raise OSError(
                "Windows blocked pointer movement. If the target application is running "
                "as administrator, run HeadMouse with matching permissions. "
                "Secure-desktop prompts cannot be controlled."
            )
        self._emit("on_move", x, y, True)
