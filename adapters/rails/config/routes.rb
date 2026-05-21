# frozen_string_literal: true

# Engine routes. Mounted by the host app under any prefix:
#
#     # host config/routes.rb
#     mount BugFab::Engine, at: "/bug-fab"
#
# The viewer's HTML index serves at the mount root (an empty path) per the
# protocol's mount-prefix invariant; the JSON list lives at "/reports".
BugFab::Engine.routes.draw do
  # Intake — single endpoint. Multipart in, JSON out. The auto-derived
  # route name from the path is `bug_reports`; declaring `as:` here
  # would collide with that auto-name on Rails 7.2+, so we omit it.
  post "/bug-reports", to: "reports#create"

  # Viewer HTML index at the engine mount root. We use `get ""` (empty
  # path) instead of `root to:` so the route name doesn't collide with
  # the host application's own `root` route on Rails 7.2+ when the
  # engine routes are re-evaluated during test boot.
  get "", to: "reports#index", as: :viewer_index
  get "/:id", to: "reports#show",
              constraints: { id: /bug-[A-Za-z]?\d{1,12}/ },
              as: :report_html

  # Viewer JSON.
  get    "/reports",                  to: "reports#list_json"
  get    "/reports/:id",              to: "reports#show_json",
                                      constraints: { id: /bug-[A-Za-z]?\d{1,12}/ },
                                      as: :report_json
  get    "/reports/:id/screenshot",   to: "screenshots#show",
                                      constraints: { id: /bug-[A-Za-z]?\d{1,12}/ },
                                      as: :report_screenshot
  put    "/reports/:id/status",       to: "reports#update_status",
                                      constraints: { id: /bug-[A-Za-z]?\d{1,12}/ },
                                      as: :report_status
  delete "/reports/:id",              to: "reports#destroy",
                                      constraints: { id: /bug-[A-Za-z]?\d{1,12}/ },
                                      as: :report

  # Bulk operations.
  post "/bulk-close-fixed",   to: "bulk_actions#close_fixed",   as: :bulk_close_fixed
  post "/bulk-archive-closed", to: "bulk_actions#archive_closed", as: :bulk_archive_closed
end
