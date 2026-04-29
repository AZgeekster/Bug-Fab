"""Optional integration adapters for Bug-Fab.

Each submodule wraps a third-party service that consumers MAY enable by
populating the corresponding ``Settings`` block. Integrations are designed
to fail open: a misconfigured or down third-party MUST NOT block the local
submission flow.
"""
