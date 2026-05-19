"""GitHub authentication subsystem.

Activated when AUTH_TYPE=github is set in the environment. The package is
self-contained: removing it (and the call sites in app/main.py) reverts
StatView to its no-auth deployment model.
"""
