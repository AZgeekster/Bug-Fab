using BugFab.AspNetCore.Storage;

namespace BugFab.AspNetCore.Endpoints;

/// <summary>
/// View model for the optional Razor list view (<c>BugFabList.cshtml</c>).
/// </summary>
/// <remarks>
/// Used only when consumers wire the viewer through MVC instead of the
/// inline-HTML endpoints in <see cref="ViewerHtmlEndpoints"/>. The default
/// deployment path doesn't touch this type.
/// </remarks>
public sealed record BugFabListViewModel(
    IReadOnlyList<StoredReportSummary> Items,
    int Total,
    IReadOnlyDictionary<string, int> Stats);
