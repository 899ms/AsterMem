"""
AsterMem Entry Point

Background: Users run `python3.11 server.py` from the repo root as described in the README.
Design intent: The actual implementation lives in backend/main.py (frontend/backend separation);
this root entry point simply delegates, ensuring the "one command to start" experience remains
stable regardless of internal restructuring.

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))

from main import main  # noqa: E402  (backend/main.py)

if __name__ == "__main__":
    main()
