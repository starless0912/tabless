"""`python -m tabless` -- the same entry point as the `tabless` command."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
