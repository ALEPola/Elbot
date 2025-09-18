"""Elbot package.

Importing this package patches nextcord's VoiceClient at import time.
"""

from .patch_nextcord import apply_patch

apply_patch()
