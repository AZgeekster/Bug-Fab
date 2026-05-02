#!/usr/bin/env python
"""Django management entry-point for the Bug-Fab minimal example."""

from __future__ import annotations

import os
import sys


def main() -> None:
    """Standard Django ``manage.py`` shim."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myapp.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
