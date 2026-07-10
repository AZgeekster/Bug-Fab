using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace BugFab.AspNetCore.Data.Entities;

/// <summary>
/// Single-row allocator for sequential <c>bug-NNN</c> ids.
/// </summary>
/// <remarks>
/// Replaces the old <c>MAX(IdSequence) + 1</c> scheme, which reissued the id of
/// a deleted top row — deleting <c>bug-003</c> from three reports made the next
/// insert compute <c>3</c> again, colliding with a live row (or, once that row
/// is gone, reusing a retired id the protocol says is never reused). The
/// counter is monotonic, so a delete cannot rewind it; on relational providers
/// it is bumped by an atomic <c>UPDATE ... SET last_value = last_value + 1</c>
/// so concurrent intake cannot lose an increment either.
/// </remarks>
[Table("bug_fab_id_counter")]
public sealed class BugFabIdCounter
{
    [Key]
    [Column("id")]
    public int Id { get; set; }

    [Column("last_value")]
    public long LastValue { get; set; }
}
