# main.py. Claude guided by JFR, 2026 04 21.
# Copyright 2026 John F. Raffensperger. All rights reserved. Unauthorised copying or redistribution is prohibited.
# Purpose: Export the web application object for ASGI entry points.

from web.app import app

__all__ = ["app"]
