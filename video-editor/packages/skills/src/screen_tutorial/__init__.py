"""Screen tutorial skill package."""

from screen_tutorial.click_detector import ClickEvent, detect_clicks
from screen_tutorial.cursor_tracker import CursorFrame, CursorTracker
from screen_tutorial.keystroke_overlay import Keystroke, render_keystrokes
from screen_tutorial.screen_tutorial import ScreenTutorialSkill

__all__ = [
    "ClickEvent",
    "CursorFrame",
    "CursorTracker",
    "Keystroke",
    "ScreenTutorialSkill",
    "detect_clicks",
    "render_keystrokes",
]
