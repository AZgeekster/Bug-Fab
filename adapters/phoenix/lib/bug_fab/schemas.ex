defmodule BugFab.Schemas do
  @moduledoc """
  Wire-protocol validation using `Ecto.Changeset` over plain embedded
  schemas — no database dependency required.

  Strict severity / status / report_type enums per PROTOCOL.md v0.1.
  Unknown enum values fail validation; the intake router maps changeset
  errors onto `422 schema_error` (or `400 unsupported_protocol_version`
  for the special protocol-version case).
  """

  import Ecto.Changeset

  @severities ~w(low medium high critical)
  @statuses ~w(open investigating fixed closed)
  @report_types ~w(bug feature_request)
  @protocol_version "0.1"

  def severities, do: @severities
  def statuses, do: @statuses
  def report_types, do: @report_types
  def protocol_version, do: @protocol_version

  # ----------------------------------------------------------------------
  # Reporter embedded shape
  # ----------------------------------------------------------------------
  defmodule Reporter do
    @moduledoc false
    use Ecto.Schema
    import Ecto.Changeset

    @primary_key false
    embedded_schema do
      field :name, :string, default: ""
      field :email, :string, default: ""
      field :user_id, :string, default: ""
    end

    def changeset(reporter, attrs) do
      reporter
      |> cast(attrs, [:name, :email, :user_id])
      |> validate_length(:name, max: 256)
      |> validate_length(:email, max: 256)
      |> validate_length(:user_id, max: 256)
    end
  end

  # ----------------------------------------------------------------------
  # Context embedded shape — `extra="allow"` semantics handled via
  # a raw map field that captures every unknown key verbatim.
  # ----------------------------------------------------------------------
  defmodule Context do
    @moduledoc false
    use Ecto.Schema
    import Ecto.Changeset

    @primary_key false
    embedded_schema do
      field :url, :string, default: ""
      field :module, :string, default: ""
      field :user_agent, :string, default: ""
      field :viewport_width, :integer, default: 0
      field :viewport_height, :integer, default: 0
      field :console_errors, {:array, :map}, default: []
      field :network_log, {:array, :map}, default: []
      field :source_mapping, :map, default: %{}
      field :app_version, :string, default: ""
      field :environment, :string, default: ""
      # Catches everything the schema does not know about. Preserved
      # verbatim on storage + read-back per the wire spec.
      field :extras, :map, default: %{}
    end

    @known ~w(url module user_agent viewport_width viewport_height
              console_errors network_log source_mapping app_version
              environment)a

    @known_str Enum.map(@known, &Atom.to_string/1)

    def changeset(ctx, attrs) when is_map(attrs) do
      {known, unknown} = split_known(attrs)

      ctx
      |> cast(known, @known)
      |> put_change(:extras, unknown)
    end

    def changeset(ctx, _other), do: cast(ctx, %{}, @known)

    defp split_known(attrs) do
      Enum.reduce(attrs, {%{}, %{}}, fn {k, v}, {known, unknown} ->
        ks = to_string(k)

        if ks in @known_str do
          {Map.put(known, ks, v), unknown}
        else
          {known, Map.put(unknown, ks, v)}
        end
      end)
    end
  end

  # ----------------------------------------------------------------------
  # BugReportCreate — submission payload validation
  # ----------------------------------------------------------------------
  defmodule Create do
    @moduledoc false
    use Ecto.Schema
    import Ecto.Changeset

    @primary_key false
    embedded_schema do
      field :protocol_version, :string
      field :title, :string
      field :client_ts, :string
      field :report_type, :string, default: "bug"
      field :description, :string, default: ""
      field :expected_behavior, :string, default: ""
      field :severity, :string, default: "medium"
      field :tags, {:array, :string}, default: []
      embeds_one :reporter, BugFab.Schemas.Reporter, on_replace: :update
      embeds_one :context, BugFab.Schemas.Context, on_replace: :update
    end
  end

  @doc """
  Build a changeset for a submission payload.

  Returns:

  * `{:ok, normalized_map}` on success — the keys are stringified for
    storage and `severity` / `report_type` are guaranteed members of the
    locked enums.
  * `{:error, :unsupported_protocol_version}` if `protocol_version` is
    missing or != "0.1". The intake router translates this to `400`
    with the `unsupported_protocol_version` code.
  * `{:error, {:schema, changeset}}` for everything else. The intake
    router translates this to `422 schema_error` and serializes the
    changeset errors into the standard envelope.
  """
  @spec validate_create(map()) ::
          {:ok, map()}
          | {:error, :unsupported_protocol_version}
          | {:error, {:schema, Ecto.Changeset.t()}}
  def validate_create(attrs) when is_map(attrs) do
    pv = Map.get(attrs, "protocol_version") || Map.get(attrs, :protocol_version)

    cond do
      is_nil(pv) ->
        {:error, :unsupported_protocol_version}

      pv != @protocol_version ->
        {:error, :unsupported_protocol_version}

      true ->
        cs =
          %Create{}
          |> cast(stringify(attrs), [
            :protocol_version,
            :title,
            :client_ts,
            :report_type,
            :description,
            :expected_behavior,
            :severity,
            :tags
          ])
          |> cast_embed(:reporter, with: &Reporter.changeset/2)
          |> cast_embed(:context, with: &Context.changeset/2)
          |> validate_required([:title, :client_ts])
          |> validate_length(:title, min: 1, max: 200)
          |> validate_length(:client_ts, min: 1)
          |> validate_inclusion(:severity, @severities,
            message: "must be one of: #{Enum.join(@severities, ", ")}"
          )
          |> validate_inclusion(:report_type, @report_types,
            message: "must be one of: #{Enum.join(@report_types, ", ")}"
          )

        if cs.valid? do
          {:ok, normalize(apply_changes(cs))}
        else
          {:error, {:schema, cs}}
        end
    end
  end

  @doc """
  Validate a `PUT /reports/:id/status` body. Strict — unknown status
  values produce a schema error mapped to `422`.
  """
  @spec validate_status_update(map()) ::
          {:ok, map()} | {:error, Ecto.Changeset.t()}
  def validate_status_update(attrs) when is_map(attrs) do
    types = %{status: :string, fix_commit: :string, fix_description: :string}

    cs =
      {%{}, types}
      |> cast(stringify(attrs), Map.keys(types))
      |> validate_required([:status])
      |> validate_inclusion(:status, @statuses,
        message: "must be one of: #{Enum.join(@statuses, ", ")}"
      )

    if cs.valid? do
      {:ok,
       %{
         "status" => get_field(cs, :status),
         "fix_commit" => get_field(cs, :fix_commit) || "",
         "fix_description" => get_field(cs, :fix_description) || ""
       }}
    else
      {:error, cs}
    end
  end

  @doc """
  Format a changeset's errors as the protocol's `detail` array.
  Each entry is `{loc: "field.path", msg: "..."}`.
  """
  @spec format_errors(Ecto.Changeset.t()) :: [%{loc: String.t(), msg: String.t()}]
  def format_errors(%Ecto.Changeset{} = cs) do
    cs
    |> traverse_errors(fn {msg, opts} ->
      Regex.replace(~r"%{(\w+)}", msg, fn _, key ->
        opts |> Keyword.get(String.to_atom(key), key) |> to_string()
      end)
    end)
    |> flatten_errors([])
  end

  defp flatten_errors(errors, path) when is_map(errors) do
    Enum.flat_map(errors, fn {field, val} ->
      sub = path ++ [to_string(field)]

      case val do
        list when is_list(list) and list != [] and is_binary(hd(list)) ->
          Enum.map(list, &%{loc: Enum.join(sub, "."), msg: &1})

        list when is_list(list) ->
          Enum.flat_map(list, fn entry ->
            if is_map(entry), do: flatten_errors(entry, sub), else: []
          end)

        map when is_map(map) ->
          flatten_errors(map, sub)

        _ ->
          []
      end
    end)
  end

  defp normalize(%Create{} = c) do
    reporter = c.reporter || %Reporter{}
    context = c.context || %Context{}

    ctx_map =
      %{
        "url" => context.url,
        "module" => context.module,
        "user_agent" => context.user_agent,
        "viewport_width" => context.viewport_width,
        "viewport_height" => context.viewport_height,
        "console_errors" => context.console_errors,
        "network_log" => context.network_log,
        "source_mapping" => context.source_mapping,
        "app_version" => context.app_version,
        "environment" => context.environment
      }
      |> Map.merge(context.extras || %{})

    %{
      "protocol_version" => c.protocol_version,
      "title" => c.title,
      "client_ts" => c.client_ts,
      "report_type" => c.report_type,
      "description" => c.description,
      "expected_behavior" => c.expected_behavior,
      "severity" => c.severity,
      "tags" => c.tags,
      "reporter" => %{
        "name" => reporter.name,
        "email" => reporter.email,
        "user_id" => reporter.user_id
      },
      "context" => ctx_map
    }
  end

  defp stringify(m) when is_map(m) do
    Map.new(m, fn {k, v} -> {to_string(k), v} end)
  end
end
