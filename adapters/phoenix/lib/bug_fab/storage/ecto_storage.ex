if Code.ensure_loaded?(Ecto) do
defmodule BugFab.Storage.EctoStorage.BugReport do
  @moduledoc false
  use Ecto.Schema
  import Ecto.Changeset

  @primary_key {:id, :id, autogenerate: true}
  schema "bug_fab_reports" do
    field :report_id, :string
    field :protocol_version, :string, default: "0.1"
    field :title, :string
    field :client_ts, :string
    field :report_type, :string, default: "bug"
    field :description, :string, default: ""
    field :expected_behavior, :string, default: ""
    field :severity, :string, default: "medium"
    field :status, :string, default: "open"
    field :tags, {:array, :string}, default: []
    field :reporter, :map, default: %{}
    field :context, :map, default: %{}
    field :module_name, :string, default: ""
    field :server_user_agent, :string, default: ""
    field :client_reported_user_agent, :string, default: ""
    field :environment, :string, default: ""
    field :lifecycle, {:array, :map}, default: []
    field :github_issue_number, :integer
    field :github_issue_url, :string
    field :has_screenshot, :boolean, default: true
    field :created_at, :utc_datetime
    field :updated_at, :utc_datetime
    field :archived_at, :utc_datetime
  end

  @castable ~w(report_id protocol_version title client_ts report_type description
              expected_behavior severity status tags reporter context module_name
              server_user_agent client_reported_user_agent environment lifecycle
              github_issue_number github_issue_url has_screenshot created_at
              updated_at archived_at)a

  def changeset(row, attrs) do
    row
    |> cast(attrs, @castable)
    |> validate_required([:report_id, :title, :client_ts])
  end

  @doc "Convert a stored row to the wire-protocol detail map."
  def to_wire(%__MODULE__{} = r) do
    %{
      "id" => r.report_id,
      "protocol_version" => r.protocol_version,
      "title" => r.title,
      "client_ts" => r.client_ts,
      "report_type" => r.report_type,
      "description" => r.description,
      "expected_behavior" => r.expected_behavior,
      "severity" => r.severity,
      "status" => r.status,
      "tags" => r.tags || [],
      "reporter" => r.reporter || %{},
      "context" => r.context || %{},
      "module" => r.module_name,
      "server_user_agent" => r.server_user_agent,
      "client_reported_user_agent" => r.client_reported_user_agent,
      "environment" => r.environment,
      "lifecycle" => r.lifecycle || [],
      "github_issue_number" => r.github_issue_number,
      "github_issue_url" => r.github_issue_url,
      "has_screenshot" => r.has_screenshot,
      "created_at" => r.created_at && DateTime.to_iso8601(r.created_at),
      "updated_at" => r.updated_at && DateTime.to_iso8601(r.updated_at)
    }
  end

  @doc "Convert a stored row to the wire-protocol summary map."
  def to_summary(%__MODULE__{} = r) do
    %{
      "id" => r.report_id,
      "title" => r.title,
      "report_type" => r.report_type,
      "severity" => r.severity,
      "status" => r.status,
      "module" => r.module_name,
      "created_at" => r.created_at && DateTime.to_iso8601(r.created_at),
      "has_screenshot" => r.has_screenshot,
      "github_issue_url" => r.github_issue_url
    }
  end
end

defmodule BugFab.Storage.EctoStorage do
  @moduledoc """
  Ecto-backed storage backend (Postgres preferred, SQLite via
  `ecto_sqlite3` also supported).

  Loaded only when Ecto is available — the dep is `optional: true` in
  `mix.exs`. Consumers who only use the file backend skip the Ecto deps
  entirely and this module remains dormant.

  ## Configuration

      config :bug_fab,
        storage: {BugFab.Storage.EctoStorage, [repo: MyApp.Repo, screenshot_dir: "/var/bug-fab-images"]}

  Screenshot bytes are NOT stored as DB blobs — they live on disk at
  `:screenshot_dir`, indexed by report id. This matches the wire
  protocol's "screenshot may be sizable" rationale and avoids hot-row
  toast bloat in Postgres / oversized cell pressure in SQLite.

  See `priv/repo/migrations/*_create_bug_reports.exs` for the
  schema. Consumers must run `mix ecto.migrate` after configuring.
  """

  @behaviour BugFab.Storage

  alias BugFab.Storage.EctoStorage.BugReport

  @impl true
  def handle(opts) do
    repo =
      Keyword.get(opts, :repo) ||
        raise ArgumentError, "EctoStorage requires :repo"

    screenshot_dir =
      Keyword.get(opts, :screenshot_dir) ||
        raise ArgumentError, "EctoStorage requires :screenshot_dir"

    id_prefix = Keyword.get(opts, :id_prefix, BugFab.config(:id_prefix, ""))

    File.mkdir_p!(screenshot_dir)
    %{repo: repo, screenshot_dir: screenshot_dir, id_prefix: id_prefix}
  end

  @impl true
  def save_report(%{repo: repo} = h, metadata, screenshot) do
    # Counter-based id assignment under a transaction so concurrent intake
    # never produces colliding bug-NNN values.
    repo.transaction(fn ->
      n = repo.aggregate(BugReport, :count, :id) + 1
      report_id = "bug-#{h.id_prefix}#{:io_lib.format("~3..0B", [n]) |> IO.iodata_to_binary()}"
      now = DateTime.utc_now() |> DateTime.truncate(:second)

      context = metadata["context"] || %{}
      reporter = metadata["reporter"] || %{}

      attrs = %{
        report_id: report_id,
        protocol_version: metadata["protocol_version"] || "0.1",
        title: metadata["title"] || "",
        client_ts: metadata["client_ts"] || "",
        report_type: metadata["report_type"] || "bug",
        description: metadata["description"] || "",
        expected_behavior: metadata["expected_behavior"] || "",
        severity: metadata["severity"] || "medium",
        status: "open",
        tags: metadata["tags"] || [],
        reporter: reporter,
        context: context,
        module_name: metadata["module"] || context["module"] || "",
        server_user_agent: metadata["server_user_agent"] || "",
        client_reported_user_agent: context["user_agent"] || "",
        environment: metadata["environment"] || context["environment"] || "",
        lifecycle: [
          %{
            "action" => "created",
            "by" => metadata["submitted_by"] || "anonymous",
            "at" => DateTime.to_iso8601(now),
            "fix_commit" => "",
            "fix_description" => ""
          }
        ],
        has_screenshot: true,
        created_at: now,
        updated_at: now,
        archived_at: nil
      }

      {:ok, _} =
        %BugReport{}
        |> BugReport.changeset(attrs)
        |> repo.insert()

      File.write!(Path.join(h.screenshot_dir, "#{report_id}.png"), screenshot)
      report_id
    end)
    |> case do
      {:ok, id} -> {:ok, id}
      err -> err
    end
  end

  @impl true
  def get_report(%{repo: repo}, report_id) do
    case repo.get_by(BugReport, report_id: report_id) do
      nil -> {:error, :not_found}
      row -> {:ok, BugReport.to_wire(row)}
    end
  end

  @impl true
  def list_reports(%{repo: repo}, filters, page, page_size) do
    import Ecto.Query

    base =
      from b in BugReport,
        where: is_nil(b.archived_at) or ^Map.get(filters, "include_archived", false),
        order_by: [desc: b.created_at]

    q =
      Enum.reduce(filters, base, fn
        {"status", v}, acc when is_binary(v) and v != "" -> from b in acc, where: b.status == ^v

        {"severity", v}, acc when is_binary(v) and v != "" ->
          from b in acc, where: b.severity == ^v

        {"environment", v}, acc when is_binary(v) and v != "" ->
          from b in acc, where: b.environment == ^v

        {"module", v}, acc when is_binary(v) and v != "" ->
          from b in acc, where: b.module_name == ^v

        {"report_type", v}, acc when is_binary(v) and v != "" ->
          from b in acc, where: b.report_type == ^v

        _, acc ->
          acc
      end)

    total = repo.aggregate(q, :count, :id)

    rows =
      q
      |> limit(^page_size)
      |> offset(^((page - 1) * page_size))
      |> repo.all()

    {:ok, %{items: Enum.map(rows, &BugReport.to_summary/1), total: total}}
  end

  @impl true
  def list_stats(%{repo: repo}) do
    import Ecto.Query

    rows =
      from(b in BugReport,
        where: is_nil(b.archived_at),
        group_by: b.status,
        select: {b.status, count(b.id)}
      )
      |> repo.all()

    base = %{"open" => 0, "investigating" => 0, "fixed" => 0, "closed" => 0}
    Enum.reduce(rows, base, fn {k, v}, acc -> Map.put(acc, k, v) end)
  end

  @impl true
  def get_screenshot(%{screenshot_dir: dir}, report_id) do
    path = Path.join(dir, "#{report_id}.png")

    if File.exists?(path) do
      {:ok, File.read!(path)}
    else
      {:error, :not_found}
    end
  end

  @impl true
  def update_status(%{repo: repo}, report_id, payload, opts) do
    case repo.get_by(BugReport, report_id: report_id) do
      nil ->
        {:error, :not_found}

      row ->
        now = DateTime.utc_now() |> DateTime.truncate(:second)
        by = Keyword.get(opts, :by, "")

        event = %{
          "action" => "status_changed",
          "by" => by,
          "at" => DateTime.to_iso8601(now),
          "status" => payload["status"],
          "fix_commit" => payload["fix_commit"] || "",
          "fix_description" => payload["fix_description"] || ""
        }

        attrs = %{
          status: payload["status"],
          updated_at: now,
          lifecycle: (row.lifecycle || []) ++ [event]
        }

        {:ok, updated} =
          row
          |> BugReport.changeset(attrs)
          |> repo.update()

        {:ok, BugReport.to_wire(updated)}
    end
  end

  @impl true
  def set_github_link(%{repo: repo}, report_id, issue_number, issue_url) do
    case repo.get_by(BugReport, report_id: report_id) do
      nil ->
        {:error, :not_found}

      row ->
        {:ok, updated} =
          row
          |> BugReport.changeset(%{github_issue_number: issue_number, github_issue_url: issue_url})
          |> repo.update()

        {:ok, BugReport.to_wire(updated)}
    end
  end

  @impl true
  def delete_report(%{repo: repo, screenshot_dir: dir}, report_id) do
    case repo.get_by(BugReport, report_id: report_id) do
      nil ->
        {:error, :not_found}

      row ->
        {:ok, _} = repo.delete(row)
        path = Path.join(dir, "#{report_id}.png")
        if File.exists?(path), do: File.rm!(path)
        :ok
    end
  end

  @impl true
  def bulk_close_fixed(%{repo: repo}, opts) do
    import Ecto.Query
    by = Keyword.get(opts, :by, "")
    now = DateTime.utc_now() |> DateTime.truncate(:second)

    rows = repo.all(from b in BugReport, where: b.status == "fixed")

    closed =
      Enum.reduce(rows, 0, fn row, acc ->
        event = %{
          "action" => "status_changed",
          "by" => by,
          "at" => DateTime.to_iso8601(now),
          "status" => "closed",
          "fix_commit" => "",
          "fix_description" => ""
        }

        {:ok, _} =
          row
          |> BugReport.changeset(%{
            status: "closed",
            updated_at: now,
            lifecycle: (row.lifecycle || []) ++ [event]
          })
          |> repo.update()

        acc + 1
      end)

    {:ok, closed}
  end

  @impl true
  def bulk_archive_closed(%{repo: repo}) do
    import Ecto.Query
    now = DateTime.utc_now() |> DateTime.truncate(:second)
    rows = repo.all(from b in BugReport, where: b.status == "closed" and is_nil(b.archived_at))

    archived =
      Enum.reduce(rows, 0, fn row, acc ->
        {:ok, _} =
          row
          |> BugReport.changeset(%{archived_at: now})
          |> repo.update()

        acc + 1
      end)

    {:ok, archived}
  end
end
end
