# main.py. Claude guided by JFR, 2026 04 21.
# Purpose: Export the web application object for ASGI entry points.

from web.app import app

__all__ = ["app"]
