@extends('bug-fab::_layout')

@section('title', 'Bug ' . $report['id'])

@section('content')
    <h2>{{ $report['title'] }}</h2>
    <p style="color:#6b7280;">
        <code>{{ $report['id'] }}</code> &middot;
        <span class="badge sev-{{ $report['severity'] }}">{{ $report['severity'] }}</span>
        <span class="badge status-{{ $report['status'] }}">{{ $report['status'] }}</span>
        <span style="margin-left:8px;">{{ $report['report_type'] }}</span>
    </p>

    @if (! empty($report['has_screenshot']))
        <img class="screenshot"
             src="{{ url(rtrim(config('bugfab.routes.viewer.prefix', 'admin/bug-reports'), '/') . '/reports/' . $report['id'] . '/screenshot') }}"
             alt="Screenshot for {{ $report['id'] }}">
    @endif

    <dl class="detail-grid">
        <dt>Description</dt><dd>{{ $report['description'] ?: '—' }}</dd>
        <dt>Expected behavior</dt><dd>{{ $report['expected_behavior'] ?: '—' }}</dd>
        <dt>Module</dt><dd>{{ $report['module'] ?: '—' }}</dd>
        <dt>Environment</dt><dd>{{ $report['environment'] ?: '—' }}</dd>
        <dt>Reporter</dt><dd>
            @php
                $rep = $report['reporter'] ?? [];
                $printable = $rep['email'] ?? '';
                if ($printable === '') $printable = $rep['user_id'] ?? '';
                if ($printable === '') $printable = $rep['name'] ?? '';
            @endphp
            {{ $printable ?: 'anonymous' }}
        </dd>
        <dt>Page URL</dt><dd>
            @if ($safe_context_url !== '')
                <a href="{{ $safe_context_url }}" target="_blank" rel="noopener">{{ $safe_context_url }}</a>
            @else
                <em>(not provided or unsafe scheme)</em>
            @endif
        </dd>
        <dt>Tags</dt><dd>{{ implode(', ', $report['tags'] ?? []) ?: '—' }}</dd>
        <dt>Client timestamp</dt><dd>{{ $report['client_ts'] }}</dd>
        <dt>Created</dt><dd>{{ $report['created_at'] }}</dd>
        <dt>Updated</dt><dd>{{ $report['updated_at'] }}</dd>
        <dt>Server User-Agent</dt><dd><code>{{ $report['server_user_agent'] }}</code></dd>
        <dt>Client-reported UA</dt><dd><code>{{ $report['client_reported_user_agent'] }}</code></dd>
        <dt>Protocol version</dt><dd>{{ $report['protocol_version'] }}</dd>
        @if (! empty($report['github_issue_url']))
            <dt>GitHub issue</dt><dd><a href="{{ $report['github_issue_url'] }}" target="_blank" rel="noopener">{{ $report['github_issue_url'] }}</a></dd>
        @endif
    </dl>

    <h3 style="margin-top:24px;">Lifecycle</h3>
    @forelse ($report['lifecycle'] as $entry)
        <div class="lifecycle-entry">
            <strong>{{ $entry['action'] }}</strong>
            by {{ $entry['by'] ?: 'anonymous' }}
            at {{ $entry['at'] }}
            @if (! empty($entry['fix_commit']))
                <div style="margin-top:4px;">Fix commit: <code>{{ $entry['fix_commit'] }}</code></div>
            @endif
            @if (! empty($entry['fix_description']))
                <div style="margin-top:4px;">{{ $entry['fix_description'] }}</div>
            @endif
        </div>
    @empty
        <p><em>No lifecycle events recorded.</em></p>
    @endforelse

    <h3 style="margin-top:24px;">Context</h3>
    <pre>{{ json_encode($report['context'], JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES) }}</pre>
@endsection
