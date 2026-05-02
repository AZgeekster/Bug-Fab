"""First-party framework adapters for Bug-Fab.

Each subpackage exposes a small shim that wires the framework-agnostic
core (``bug_fab.intake.validate_payload``, ``bug_fab.storage.Storage``,
``bug_fab.config.Settings``) into the host framework's routing
primitives. Adapters are gated by optional ``[extras]`` so a FastAPI
consumer never pays for Flask, and vice versa.

Adapters live here (rather than at the top-level package) so the import
surface stays predictable: ``from bug_fab.adapters.flask import
make_blueprint``, ``from bug_fab.adapters.starlette import ...``, etc.
"""
