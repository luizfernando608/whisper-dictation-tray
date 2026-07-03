"""Single source of truth for the application version.

Kept in sync with `installer.iss` (AppVersion) and the git release tag
(`vX.Y.Z`). `build.ps1` reads `__version__` from here and passes it to the
Inno Setup compiler, and `updater.py` compares it against the latest GitHub
release.
"""

from __future__ import annotations

__version__ = "1.2.0"
