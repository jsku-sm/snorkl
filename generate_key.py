"""Run once; keep the output in Streamlit Secrets, never in GitHub."""
from cryptography.fernet import Fernet

print(Fernet.generate_key().decode())
