<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>@yield('title', 'Bug-Fab')</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style{!! $csp_nonce ? ' nonce="' . e($csp_nonce) . '"' : '' !!}>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 24px; color: #1a1a1a; }
        h1 { font-size: 1.5rem; margin: 0 0 16px; }
        .stats { display: flex; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
        .stat-card { padding: 8px 14px; background: #f3f4f6; border-radius: 6px; min-width: 80px; }
        .stat-card .label { font-size: 0.75rem; color: #6b7280; text-transform: uppercase; }
        .stat-card .value { font-size: 1.4rem; font-weight: 600; }
        table { border-collapse: collapse; width: 100%; margin-top: 12px; font-size: 0.9rem; }
        th, td { padding: 8px 10px; text-align: left; border-bottom: 1px solid #e5e7eb; }
        th { background: #f9fafb; font-weight: 600; }
        a { color: #2563eb; text-decoration: none; }
        a:hover { text-decoration: underline; }
        .badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 0.75rem; }
        .sev-low { background: #dcfce7; color: #166534; }
        .sev-medium { background: #fef9c3; color: #854d0e; }
        .sev-high { background: #ffedd5; color: #9a3412; }
        .sev-critical { background: #fee2e2; color: #991b1b; }
        .status-open { background: #dbeafe; color: #1e40af; }
        .status-investigating { background: #fef3c7; color: #92400e; }
        .status-fixed { background: #d1fae5; color: #065f46; }
        .status-closed { background: #e5e7eb; color: #374151; }
        form.filters { margin: 12px 0; display: flex; gap: 8px; flex-wrap: wrap; }
        form.filters input, form.filters select { padding: 4px 8px; }
        pre { background: #f3f4f6; padding: 12px; border-radius: 6px; overflow-x: auto; font-size: 0.85rem; }
        img.screenshot { max-width: 100%; border: 1px solid #e5e7eb; border-radius: 6px; }
        .detail-grid { display: grid; grid-template-columns: 200px 1fr; gap: 8px 16px; margin-top: 16px; }
        .detail-grid dt { font-weight: 600; color: #4b5563; }
        .lifecycle-entry { padding: 8px 0; border-bottom: 1px dashed #e5e7eb; }
    </style>
</head>
<body>
    <header>
        <h1><a href="{{ url(config('bugfab.routes.viewer.prefix', 'admin/bug-reports')) }}">Bug-Fab</a></h1>
    </header>
    <main>
        @yield('content')
    </main>
</body>
</html>
