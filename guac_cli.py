"""Guac VM Manager CLI entry point."""
import sys
from pathlib import Path

# Add current directory to path
current_dir = Path(__file__).parent
sys.path.insert(0, str(current_dir))

from cli import main

if __name__ == "__main__":
    sys.exit(main())