"""
beeperclaw - AI coding agent accessible from anywhere via Beeper/Matrix.

This package provides a Matrix bot that integrates with OpenCode to allow
you to assign coding tasks from your phone.
"""

__version__ = "0.1.0"
__author__ = "Your Name"
__license__ = "MIT"

from beeperclaw.bot import BeeperClawBot
from beeperclaw.config import Config

__all__ = ["BeeperClawBot", "Config", "__version__"]
