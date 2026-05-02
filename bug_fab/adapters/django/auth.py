"""Auth helpers for the Bug-Fab Django viewer routes.

v0.1 has no auth abstraction — :class:`AdminRequiredMixin` is provided
as a convenience for class-based viewer routes that want a sane default
("logged-in staff user"). Consumers running their own admin middleware
at the mount prefix can ignore the mixin entirely; protection then
happens at the URL prefix and the viewer routes never see an
unauthenticated request.

The mixin delegates to Django's stock
:class:`~django.contrib.auth.mixins.LoginRequiredMixin` and
:class:`~django.contrib.auth.mixins.UserPassesTestMixin` so behavior
matches whatever the host project already configures (login URL,
redirect behavior, raise-vs-redirect, etc.).
"""

from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin


class AdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Reject anonymous and non-staff users from a viewer CBV.

    Override :meth:`test_func` in a subclass to widen or tighten the
    "is admin" definition for a specific deployment. Default is
    ``request.user.is_staff``, matching Django's built-in admin app.
    """

    def test_func(self) -> bool:
        """Allow authenticated staff users only — override per deployment."""
        user = getattr(self.request, "user", None)
        return bool(user and user.is_authenticated and user.is_staff)
