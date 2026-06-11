"""Enable ``python -m bhe`` as an alias for the ``bhe`` console script."""

from __future__ import annotations

from bhe.cli import app

if __name__ == "__main__":
    app()
