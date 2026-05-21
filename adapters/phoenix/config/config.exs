import Config

config :bug_fab,
  # Storage backend — `FileStorage` for hobby / single-node, `EctoStorage`
  # for production / multi-node deployments. See README for details.
  storage:
    {BugFab.Storage.FileStorage,
     [storage_dir: Path.join(System.tmp_dir!(), "bug-fab")]},
  max_upload_mb: 4,
  rate_limit_enabled: false,
  rate_limit_max: 30,
  rate_limit_window_seconds: 60,
  id_prefix: "",
  viewer_permissions: %{
    can_edit_status: true,
    can_delete: true,
    can_bulk: true
  },
  actor_resolver: nil

import_config "#{config_env()}.exs"
