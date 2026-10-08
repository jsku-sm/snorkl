"""Streamlit Cloud entry point. Keep this file beside app.py."""
from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).with_name("app.py")), run_name="__main__")
