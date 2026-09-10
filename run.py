"""Portable checkout launcher; relative arguments stay relative to the caller."""
from stepagent.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
