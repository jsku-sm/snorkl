"""Copy to an existing Streamlit app's pages/ directory, with think_app/ at its root.

This page has independent authentication. It never trusts another app's
unverified session_state user/role. See README for deployment and account setup.
"""
from think_app.ui import run

run()
