@extends('bug-fab::_layout')

@section('title', 'Bug reports')

@section('content')
    <div class="stats">
        @foreach (['open' => 'Open', 'investigating' => 'Investigating', 'fixed' => 'Fixed', 'closed' => 'Closed'] as $key => $label)
            <div class="stat-card">
                <div class="label">{{ $label }}</div>
                <div class="value">{{ $stats[$key] ?? 0 }}</div>
            </div>
        @endforeach
        <div class="stat-card">
            <div class="label">Total</div>
            <div class="value">{{ $total }}</div>
        </div>
    </div>

    <form class="filters" method="get">
        <select name="status">
            <option value="">Status (any)</option>
            @foreach (['open', 'investigating', 'fixed', 'closed'] as $s)
                <option value="{{ $s }}" @selected(($filters['status'] ?? '') === $s)>{{ ucfirst($s) }}</option>
            @endforeach
        </select>
        <select name="severity">
            <option value="">Severity (any)</option>
            @foreach (['low', 'medium', 'high', 'critical'] as $s)
                <option value="{{ $s }}" @selected(($filters['severity'] ?? '') === $s)>{{ ucfirst($s) }}</option>
            @endforeach
        </select>
        <input type="text" name="module" placeholder="Module" value="{{ $filters['module'] ?? '' }}">
        <input type="text" name="environment" placeholder="Environment" value="{{ $filters['environment'] ?? '' }}">
        <button type="submit">Filter</button>
    </form>

    <table>
        <thead>
            <tr>
                <th>ID</th>
                <th>Title</th>
                <th>Type</th>
                <th>Severity</th>
                <th>Status</th>
                <th>Module</th>
                <th>Created</th>
            </tr>
        </thead>
        <tbody>
            @forelse ($items as $item)
                <tr>
                    <td><a href="{{ url(rtrim(config('bugfab.routes.viewer.prefix', 'admin/bug-reports'), '/') . '/' . $item['id']) }}">{{ $item['id'] }}</a></td>
                    <td>{{ $item['title'] }}</td>
                    <td>{{ $item['report_type'] }}</td>
                    <td><span class="badge sev-{{ $item['severity'] }}">{{ $item['severity'] }}</span></td>
                    <td><span class="badge status-{{ $item['status'] }}">{{ $item['status'] }}</span></td>
                    <td>{{ $item['module'] }}</td>
                    <td>{{ $item['created_at'] }}</td>
                </tr>
            @empty
                <tr><td colspan="7" style="text-align:center;padding:24px;color:#6b7280;">No bug reports yet.</td></tr>
            @endforelse
        </tbody>
    </table>

    @if ($total_pages > 1)
        <p style="margin-top:16px;">
            Page {{ $page }} of {{ $total_pages }} —
            @if ($page > 1)<a href="?page={{ $page - 1 }}">&larr; Prev</a>@endif
            @if ($page < $total_pages)<a href="?page={{ $page + 1 }}">Next &rarr;</a>@endif
        </p>
    @endif
@endsection
