# `wwwroot/bug_fab/`

This directory ships the Bug-Fab JavaScript bundle that the consumer's pages load to expose the floating action button (FAB), screenshot capture, and submit overlay.

## What goes here

- `bug-fab.js` — the framework-agnostic frontend bundle from the upstream Bug-Fab repo at [`static/bug-fab.js`](https://github.com/AZgeekster/Bug-Fab/blob/main/static/bug-fab.js).

## Why it isn't checked in

The frontend bundle lives in the public Bug-Fab repo. Vendoring a copy here would create two sources of truth that can drift. Instead, copy the file at build time so the version stamp matches your installed `BugFab.AspNetCore` package.

## Build-time copy (MSBuild)

Add to the consumer's `.csproj`:

```xml
<Target Name="CopyBugFabJs" BeforeTargets="Build">
  <DownloadFile
    SourceUrl="https://raw.githubusercontent.com/AZgeekster/Bug-Fab/main/static/bug-fab.js"
    DestinationFolder="$(ProjectDir)wwwroot/bug_fab"
    SkipUnchangedFiles="true" />
</Target>
```

…or check the file in alongside your other `wwwroot` assets and update it on each Bug-Fab release.

## Loading on a consumer page

```html
<script src="~/bug_fab/bug-fab.js" defer></script>
<script>
  BugFab.init({
    endpoint: '/bug-fab/bug-reports',
    appVersion: '@System.Reflection.Assembly.GetExecutingAssembly().GetName().Version',
    environment: '@builder.Environment.EnvironmentName',
  });
</script>
```

## Why a placeholder is committed

The empty `bug-fab.js.placeholder` file ensures the directory survives `git clone` even before the consumer wires the build-time copy. Delete it once the real `bug-fab.js` lands.
