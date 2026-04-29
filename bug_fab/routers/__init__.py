"""FastAPI routers shipped with the Bug-Fab reference adapter.

Two routers are exposed so consumers can mount each under a URL prefix
covered by their own auth middleware (per the v0.1 mount-point delegation
decision):

* :data:`submit_router` — ``POST /bug-reports`` intake endpoint.
* :data:`viewer_router` — list/detail HTML pages plus the JSON management
  endpoints (status, delete, bulk operations, screenshot serve).
"""

from bug_fab.routers.submit import submit_router
from bug_fab.routers.viewer import viewer_router

__all__ = ["submit_router", "viewer_router"]
