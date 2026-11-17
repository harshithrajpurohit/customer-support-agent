"""
main.py - Entrypoint stub for the Multi-Agent Customer Support System.

Allows launching the FastAPI app via:
    uv run python main.py
    uv run uvicorn support.api:app --port 8000
"""

import sys
from pathlib import Path
import uvicorn

# Ensure src/ is on sys.path when running main.py directly
src_dir = Path(__file__).resolve().parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

if __name__ == "__main__":
    uvicorn.run(
        "support.api:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
