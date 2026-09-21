# Release checklist — claude-guard

## Status

| | |
|:--|:--|
| Source on `master` | **0.2.0, pushed** |
| CI | **green** — 9 cells (ubuntu/macos/windows x py3.10/3.11/3.12), 130 tests |
| Built artifacts | `dist/*` pass `twine check`, install clean into a fresh venv |
| Verified | against a real `~/.claude` with 208 session files |
| PyPI | **not published** — the name is free (JSON API 404) |
| Blocker | **authorship: co-founder not named in `pyproject.toml`** |

## DO NOT cut a GitHub Release yet

`.github/workflows/publish.yml` fires on `release: published` and uploads to
PyPI automatically. Creating a release right now would publish 0.2.0 with only
one author named, and **a PyPI version number can never be reused or edited**.

Fix authorship first:

```toml
authors = [
    {name = "Pranav R. Jadhav"},
    {name = "<CO-FOUNDER NAME>"},
]
```

## You probably do not need an API token

This repo is already set up for **Trusted Publishing** — `publish.yml` requests
`id-token: write` and uses `pypa/gh-action-pypi-publish` with no password input.
That means GitHub authenticates to PyPI over OIDC and there is no long-lived
secret to create, store, or leak. It is the current recommended approach.

You only have to register the publisher on PyPI's side, once:

1. Sign in at <https://pypi.org> (create the account if needed; enable 2FA — PyPI requires it).
2. Go to <https://pypi.org/manage/account/publishing/>.
3. Under **Add a new pending publisher**, fill in:
   - PyPI Project Name: `claude-guard`
   - Owner: `LuciferDono`
   - Repository name: `claude-guard`
   - Workflow name: `publish.yml`
   - Environment name: *(leave blank)*
4. Save.

"Pending publisher" is the right form because the project does not exist on PyPI
yet; it is created automatically on first upload.

Then release:

```bash
git tag v0.2.0 && git push --tags
gh release create v0.2.0 --title "v0.2.0 — correctness release" --notes-file <(sed -n '/## 0.2.0/,/^---$/p' README.md)
```

The workflow builds and uploads. Watch it: `gh run watch`.

## If you would rather use a token

Only needed for a manual `twine upload` from your machine.

1. <https://pypi.org/manage/account/token/> -> **Add API token**.
2. Scope: **Entire account** for the first upload (a project-scoped token cannot
   be made until the project exists). Afterwards, delete it and issue a
   project-scoped token for `claude-guard`.
3. Copy it immediately — shown once. It starts `pypi-`.
4. Store it in `~/.pypirc` (chmod 600), never in the repo:

```ini
[pypi]
  username = __token__
  password = pypi-AgEIcHlwaS5vcmc...
```

Then:

```bash
rm -rf dist build *.egg-info
python -m build
python -m twine check dist/*
python -m twine upload dist/*
```

## Rehearse on TestPyPI first (recommended)

Separate account and token at <https://test.pypi.org>.

```bash
python -m twine upload --repository testpypi dist/*
pip install -i https://test.pypi.org/simple/ claude-guard
claude-guard doctor          # must print "Cost tracking is working."
```

## After publishing

```bash
pip install claude-guard
claude-guard doctor
claude-guard init
```

## Note on 0.1.0

0.1.0 was never uploaded to PyPI, which is fortunate: it found zero session
files on every machine, recorded no cost, and never blocked anything. Do not
publish that tag. See "0.2.0 — correctness release" in README.md.
