"""
Streamlit wrapper to force proper script context initialization
"""
import streamlit as st
from streamlit.runtime.scriptrunner import add_script_run_ctx
import sys
import os

# Force proper working directory
os.chdir('/app/streamlit')
sys.path.insert(0, '/app/streamlit')

# Import and run the main app
if __name__ == "__main__":
    import app
