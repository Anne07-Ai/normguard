"""Process entry point for the private Hugging Face Docker Space."""

from normguard.service import build_app_from_environment

app = build_app_from_environment()
