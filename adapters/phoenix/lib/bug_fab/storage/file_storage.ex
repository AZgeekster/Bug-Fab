defmodule BugFab.Storage.FileStorage do
  @moduledoc """
  JSON-on-disk storage backend mirroring the Python reference layout:

      <storage_dir>/
      ├── index.json              denormalized listing for fast filter/page
      ├── bug-001.json            full report payload
      ├── bug-001.png             screenshot bytes
      └── archive/
          ├── bug-002.json        archived report
          └── bug-002.png

  Atomicity: every write goes through a tmp+rename to avoid torn writes
  on crash (matches the Python reference and audit finding B3).

  Concurrency: a single Agent-process-per-storage-handle serializes
  writes. This means multi-node deployments MUST NOT share the same
  storage directory across nodes — use `EctoStorage` for that. See
  `MIGRATION_NOTES.md` § "Multi-node FileStorage caveat".
  """

  @behaviour BugFab.Storage

  alias BugFab.Storage.FileStorage.Server

  @impl true
  def handle(opts) do
    storage_dir =
      Keyword.get(opts, :storage_dir) ||
        raise ArgumentError, "FileStorage requires :storage_dir"

    id_prefix = Keyword.get(opts, :id_prefix, BugFab.config(:id_prefix, ""))
    {:ok, pid} = Server.start_link(storage_dir: storage_dir, id_prefix: id_prefix)
    %{server: pid, storage_dir: storage_dir, id_prefix: id_prefix}
  end

  @impl true
  def save_report(%{server: pid}, metadata, screenshot),
    do: Server.save_report(pid, metadata, screenshot)

  @impl true
  def get_report(%{server: pid}, report_id), do: Server.get_report(pid, report_id)

  @impl true
  def list_reports(%{server: pid}, filters, page, page_size),
    do: Server.list_reports(pid, filters, page, page_size)

  @impl true
  def list_stats(%{server: pid}), do: Server.list_stats(pid)

  @impl true
  def get_screenshot(%{server: pid}, report_id), do: Server.get_screenshot(pid, report_id)

  @impl true
  def update_status(%{server: pid}, report_id, payload, opts),
    do: Server.update_status(pid, report_id, payload, opts)

  @impl true
  def set_github_link(%{server: pid}, report_id, issue_number, issue_url),
    do: Server.set_github_link(pid, report_id, issue_number, issue_url)

  @impl true
  def delete_report(%{server: pid}, report_id), do: Server.delete_report(pid, report_id)

  @impl true
  def bulk_close_fixed(%{server: pid}, opts), do: Server.bulk_close_fixed(pid, opts)

  @impl true
  def bulk_archive_closed(%{server: pid}), do: Server.bulk_archive_closed(pid)
end

defmodule BugFab.Storage.FileStorage.Server do
  @moduledoc false
  use GenServer

  @index_filename "index.json"
  @archive_subdir "archive"
  @report_id_regex ~r/^bug-[A-Za-z]?\d{3,}$/

  def start_link(opts) do
    GenServer.start_link(__MODULE__, opts)
  end

  @impl true
  def init(opts) do
    storage_dir = Keyword.fetch!(opts, :storage_dir)
    id_prefix = Keyword.get(opts, :id_prefix, "")
    File.mkdir_p!(storage_dir)
    File.mkdir_p!(Path.join(storage_dir, @archive_subdir))

    {:ok,
     %{
       storage_dir: storage_dir,
       archive_dir: Path.join(storage_dir, @archive_subdir),
       index_path: Path.join(storage_dir, @index_filename),
       id_prefix: id_prefix
     }}
  end

  # ----- public message API -----

  def save_report(pid, metadata, screenshot),
    do: GenServer.call(pid, {:save_report, metadata, screenshot})

  def get_report(pid, id), do: GenServer.call(pid, {:get_report, id})
  def list_reports(pid, f, p, ps), do: GenServer.call(pid, {:list_reports, f, p, ps})
  def list_stats(pid), do: GenServer.call(pid, :list_stats)
  def get_screenshot(pid, id), do: GenServer.call(pid, {:get_screenshot, id})
  def update_status(pid, id, p, o), do: GenServer.call(pid, {:update_status, id, p, o})

  def set_github_link(pid, id, n, u),
    do: GenServer.call(pid, {:set_github_link, id, n, u})

  def delete_report(pid, id), do: GenServer.call(pid, {:delete_report, id})
  def bulk_close_fixed(pid, opts), do: GenServer.call(pid, {:bulk_close_fixed, opts})
  def bulk_archive_closed(pid), do: GenServer.call(pid, :bulk_archive_closed)

  # ----- handlers -----

  @impl true
  def handle_call({:save_report, metadata, screenshot}, _from, state) do
    index = read_index(state)
    report_id = next_id(state, index)
    now = now_iso()
    report = build_report(report_id, metadata, now)

    write_screenshot(state, report_id, screenshot)
    write_report(state, report_id, report)

    entry = build_index_entry(report)
    next_index =
      Map.update!(index, "reports", &(&1 ++ [entry]))
      |> Map.update!("next_number", &(&1 + 1))

    write_index(state, next_index)
    {:reply, {:ok, report_id}, state}
  end

  def handle_call({:get_report, id}, _from, state) do
    case do_read(state, id) do
      nil -> {:reply, {:error, :not_found}, state}
      data -> {:reply, {:ok, data}, state}
    end
  end

  def handle_call({:list_reports, filters, page, page_size}, _from, state) do
    index = read_index(state)

    matched =
      index["reports"]
      |> Enum.filter(&matches_filters?(&1, filters))
      |> Enum.sort_by(& &1["created_at"], :desc)

    total = length(matched)
    start = max(0, (page - 1) * page_size)
    items = matched |> Enum.drop(start) |> Enum.take(page_size)

    {:reply, {:ok, %{items: items, total: total}}, state}
  end

  def handle_call(:list_stats, _from, state) do
    index = read_index(state)

    stats =
      Enum.reduce(~w(open investigating fixed closed), %{}, fn s, acc ->
        count = Enum.count(index["reports"], &(&1["status"] == s))
        Map.put(acc, s, count)
      end)

    {:reply, stats, state}
  end

  def handle_call({:get_screenshot, id}, _from, state) do
    if Regex.match?(@report_id_regex, id) do
      live = Path.join(state.storage_dir, "#{id}.png")
      archived = Path.join(state.archive_dir, "#{id}.png")

      cond do
        File.exists?(live) -> {:reply, {:ok, File.read!(live)}, state}
        File.exists?(archived) -> {:reply, {:ok, File.read!(archived)}, state}
        true -> {:reply, {:error, :not_found}, state}
      end
    else
      {:reply, {:error, :not_found}, state}
    end
  end

  def handle_call({:update_status, id, payload, opts}, _from, state) do
    case do_read(state, id) do
      nil ->
        {:reply, {:error, :not_found}, state}

      data ->
        now = now_iso()
        by = Keyword.get(opts, :by, "")

        event = %{
          "action" => "status_changed",
          "by" => by,
          "at" => now,
          "status" => payload["status"],
          "fix_commit" => payload["fix_commit"] || "",
          "fix_description" => payload["fix_description"] || ""
        }

        updated =
          data
          |> Map.put("status", payload["status"])
          |> Map.put("updated_at", now)
          |> Map.update("lifecycle", [event], &(&1 ++ [event]))

        write_report(state, id, updated)
        update_index_entry(state, id, %{"status" => payload["status"]})
        {:reply, {:ok, updated}, state}
    end
  end

  def handle_call({:set_github_link, id, issue_number, issue_url}, _from, state) do
    case do_read(state, id) do
      nil ->
        {:reply, {:error, :not_found}, state}

      data ->
        updated =
          data
          |> Map.put("github_issue_number", issue_number)
          |> Map.put("github_issue_url", issue_url)

        write_report(state, id, updated)
        update_index_entry(state, id, %{"github_issue_url" => issue_url})
        {:reply, {:ok, updated}, state}
    end
  end

  def handle_call({:delete_report, id}, _from, state) do
    if Regex.match?(@report_id_regex, id) do
      candidates = candidate_paths(state, id)
      existed = Enum.any?(candidates, &File.exists?/1)

      Enum.each(candidates, fn p -> if File.exists?(p), do: File.rm!(p) end)

      if existed do
        index = read_index(state)
        next = Map.update!(index, "reports", fn rs -> Enum.reject(rs, &(&1["id"] == id)) end)
        write_index(state, next)
        {:reply, :ok, state}
      else
        {:reply, {:error, :not_found}, state}
      end
    else
      {:reply, {:error, :not_found}, state}
    end
  end

  def handle_call({:bulk_close_fixed, opts}, _from, state) do
    index = read_index(state)
    by = Keyword.get(opts, :by, "")
    now = now_iso()

    {closed_count, next_reports} =
      Enum.map_reduce(index["reports"], 0, fn entry, count ->
        if entry["status"] == "fixed" do
          id = entry["id"]

          case do_read(state, id) do
            nil ->
              {entry, count}

            data ->
              event = %{
                "action" => "status_changed",
                "by" => by,
                "at" => now,
                "status" => "closed",
                "fix_commit" => "",
                "fix_description" => ""
              }

              updated =
                data
                |> Map.put("status", "closed")
                |> Map.put("updated_at", now)
                |> Map.update("lifecycle", [event], &(&1 ++ [event]))

              write_report(state, id, updated)
              {Map.put(entry, "status", "closed"), count + 1}
          end
        else
          {entry, count}
        end
      end)
      |> then(fn {list, count} -> {count, list} end)

    write_index(state, Map.put(index, "reports", next_reports))
    {:reply, {:ok, closed_count}, state}
  end

  def handle_call(:bulk_archive_closed, _from, state) do
    index = read_index(state)
    closed = Enum.filter(index["reports"], &(&1["status"] == "closed"))

    archived =
      Enum.reduce(closed, 0, fn entry, acc ->
        id = entry["id"]
        json_src = Path.join(state.storage_dir, "#{id}.json")
        png_src = Path.join(state.storage_dir, "#{id}.png")
        json_dst = Path.join(state.archive_dir, "#{id}.json")
        png_dst = Path.join(state.archive_dir, "#{id}.png")

        moved =
          (File.exists?(json_src) and move_file(json_src, json_dst)) ||
            File.exists?(png_src)

        if File.exists?(png_src), do: move_file(png_src, png_dst)

        if moved, do: acc + 1, else: acc
      end)

    next = Map.put(index, "reports", index["reports"] -- closed)
    write_index(state, next)
    {:reply, {:ok, archived}, state}
  end

  # ----- helpers -----

  defp now_iso, do: DateTime.utc_now() |> DateTime.to_iso8601()

  defp atomic_write_text(path, payload) do
    tmp = path <> ".tmp"
    File.write!(tmp, payload)
    File.rename!(tmp, path)
  end

  defp atomic_write_bytes(path, payload) do
    tmp = path <> ".tmp"
    File.write!(tmp, payload)
    File.rename!(tmp, path)
  end

  defp move_file(src, dst) do
    File.mkdir_p!(Path.dirname(dst))

    case File.rename(src, dst) do
      :ok -> true
      _ -> File.cp!(src, dst) && File.rm!(src) && true
    end
  end

  defp read_index(state) do
    case File.read(state.index_path) do
      {:ok, content} ->
        case Jason.decode(content) do
          {:ok, %{} = data} ->
            data
            |> Map.put_new("reports", [])
            |> Map.put_new("next_number", 1)

          _ ->
            %{"reports" => [], "next_number" => 1}
        end

      {:error, _} ->
        %{"reports" => [], "next_number" => 1}
    end
  end

  defp write_index(state, index) do
    atomic_write_text(state.index_path, Jason.encode!(index, pretty: true))
  end

  defp do_read(state, id) do
    if Regex.match?(@report_id_regex, id) do
      live = Path.join(state.storage_dir, "#{id}.json")
      archived = Path.join(state.archive_dir, "#{id}.json")

      cond do
        File.exists?(live) -> File.read!(live) |> Jason.decode!()
        File.exists?(archived) -> File.read!(archived) |> Jason.decode!()
        true -> nil
      end
    else
      nil
    end
  end

  defp write_report(state, id, data) do
    primary = Path.join(state.storage_dir, "#{id}.json")
    archived = Path.join(state.archive_dir, "#{id}.json")
    path = if File.exists?(archived) and not File.exists?(primary), do: archived, else: primary
    atomic_write_text(path, Jason.encode!(data, pretty: true))
  end

  defp write_screenshot(state, id, bytes) do
    path = Path.join(state.storage_dir, "#{id}.png")
    atomic_write_bytes(path, bytes)
  end

  defp update_index_entry(state, id, fields) do
    index = read_index(state)

    next_reports =
      Enum.map(index["reports"], fn e ->
        if e["id"] == id, do: Map.merge(e, fields), else: e
      end)

    write_index(state, Map.put(index, "reports", next_reports))
  end

  defp candidate_paths(state, id) do
    [
      Path.join(state.storage_dir, "#{id}.json"),
      Path.join(state.storage_dir, "#{id}.png"),
      Path.join(state.archive_dir, "#{id}.json"),
      Path.join(state.archive_dir, "#{id}.png")
    ]
  end

  defp next_id(state, index) do
    n = index["next_number"] || 1
    "bug-#{state.id_prefix}#{:io_lib.format("~3..0B", [n]) |> IO.iodata_to_binary()}"
  end

  defp build_report(id, metadata, now) do
    context = metadata["context"] || %{}
    reporter = metadata["reporter"] || %{}

    %{
      "id" => id,
      "protocol_version" => metadata["protocol_version"] || "0.1",
      "title" => metadata["title"] || "",
      "client_ts" => metadata["client_ts"] || "",
      "report_type" => metadata["report_type"] || "bug",
      "description" => metadata["description"] || "",
      "expected_behavior" => metadata["expected_behavior"] || "",
      "severity" => metadata["severity"] || "medium",
      "status" => "open",
      "tags" => metadata["tags"] || [],
      "reporter" => %{
        "name" => reporter["name"] || "",
        "email" => reporter["email"] || "",
        "user_id" => reporter["user_id"] || ""
      },
      "context" => context,
      "module" => metadata["module"] || context["module"] || "",
      "created_at" => now,
      "updated_at" => now,
      "has_screenshot" => true,
      "server_user_agent" => metadata["server_user_agent"] || "",
      "client_reported_user_agent" => context["user_agent"] || "",
      "environment" => metadata["environment"] || context["environment"] || "",
      "github_issue_url" => nil,
      "github_issue_number" => nil,
      "lifecycle" => [
        %{
          "action" => "created",
          "by" => metadata["submitted_by"] || "anonymous",
          "at" => now,
          "fix_commit" => "",
          "fix_description" => ""
        }
      ]
    }
  end

  defp build_index_entry(report) do
    %{
      "id" => report["id"],
      "title" => report["title"],
      "report_type" => report["report_type"],
      "severity" => report["severity"],
      "status" => report["status"],
      "module" => report["module"],
      "created_at" => report["created_at"],
      "has_screenshot" => report["has_screenshot"],
      "github_issue_url" => report["github_issue_url"]
    }
  end

  defp matches_filters?(entry, filters) do
    Enum.all?(~w(status severity module report_type environment), fn key ->
      case Map.get(filters, key) do
        nil -> true
        "" -> true
        wanted -> entry[key] == wanted
      end
    end)
  end
end
