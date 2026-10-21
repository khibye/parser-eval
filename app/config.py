"""Process-level configuration, read once from the environment."""

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))

# Secret used to encrypt stored keys and to sign session cookies.
# Comes from the SealedSecret in the Helm chart. Must be set in production.
APP_SECRET_KEY = os.environ.get("APP_SECRET_KEY", "")

# Password used until someone changes it in Settings > Access.
APP_INITIAL_PASSWORD = os.environ.get("APP_INITIAL_PASSWORD", "")

# Address docintel should use for its VLM calls (shown in the UI).
VLM_PROXY_PUBLIC_URL = os.environ.get("VLM_PROXY_PUBLIC_URL", "http://parser-eval:8081/v1")

UI_PORT = int(os.environ.get("UI_PORT", "8080"))
PROXY_PORT = int(os.environ.get("PROXY_PORT", "8081"))

# Scratch space for downloaded files during a run (not on the PVC).
WORK_DIR = Path(os.environ.get("WORK_DIR", "/tmp/parser-eval-work"))

# Marks session cookies Secure when the UI is served over HTTPS (OpenShift Route with TLS).
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() == "true"

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"
