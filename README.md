# env/

Per-environment config for the Signalor backend.

## Files

- `local.env` — local dev values (gitignored)
- `staging.env` — staging values (gitignored)
- `production.env` — production values (gitignored)
- `example.env` — committed template; copy this to one of the above when bootstrapping a new machine

## How it works

`python scripts/use-env.py <name>` copies `env/<name>.env` to `.env` at the project root. Django loads `.env` automatically (via the existing settings loader).

```sh
# Switch to local
python scripts/use-env.py local
python manage.py runserver

# Switch to staging values for a one-off test
python scripts/use-env.py staging
python manage.py runserver

# Back to local
python scripts/use-env.py local
```

The active env is whatever was last copied. The generated `.env` is also gitignored.

## Adding a new variable

1. Add the key to `example.env` with a placeholder + comment.
2. Add the key to each of `local.env` / `staging.env` / `production.env` with the right value per environment.
3. Run `python scripts/use-env.py <current>` to refresh `.env`.
