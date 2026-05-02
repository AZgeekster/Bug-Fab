# Bug-Fab — Django minimal example

A 30-line Django project demonstrating Bug-Fab as a reusable Django
app. After `pip install "bug-fab[django]"`, the consumer's three-step
integration is:

1. Add `"bug_fab.adapters.django"` to `INSTALLED_APPS`.
2. Set `MEDIA_ROOT` and bump `DATA_UPLOAD_MAX_MEMORY_SIZE` to 12 MiB.
3. Mount the URLs under whatever prefix the host's auth covers.

## Run it

```bash
pip install "bug-fab[django]"
cd examples/django-minimal
python manage.py migrate
python manage.py runserver 8000
```

Then open `http://localhost:8000/`. Click the floating bug icon in the
bottom-right corner, draw on the screenshot, and submit. Reports land
under `media/bug_fab_screenshots/`. The viewer is at
`http://localhost:8000/admin/bug-reports/`.

## Files

- `manage.py` — Django entry-point (the standard scaffold).
- `myapp/settings.py` — minimum-viable Django settings + Bug-Fab config.
- `myapp/urls.py` — mounts the intake and viewer routes.
