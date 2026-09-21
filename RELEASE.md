# Release checklist — claude-guard

## Status: 0.2.0 built and validated, NOT uploaded

`dist/claude_guard-0.2.0-py3-none-any.whl` and `.tar.gz` pass `twine check`,
install clean into a fresh venv, and run correctly against a real `~/.claude`
with 208 session files. Everything up to the upload is done.

## Why it stopped here

Three things only you can decide or supply:

1. **PyPI API token.** Not mine to hold. `~/.pypirc` or `TWINE_PASSWORD`.
2. **Authorship.** `pyproject.toml` lists `authors = [{name = "LuciferDono"}]`.
   The reason for publishing now was to create a joint artifact with BOTH
   founders named, for the YC application. Add your co-founder before upload —
   a version number can never be reused once taken.
3. **Verify `github.com/LuciferDono/claude-guard` is public and pushed.** It
   returned HTTP 200, but this working tree has no git remote and the repo may
   not contain the 0.2.0 source. PyPI renders those URLs permanently.

The PyPI name is free: the JSON API returns 404 for `claude-guard`. (The project
page returns 200 to curl, but that is PyPI's bot-challenge page — it returns 200
for nonexistent names too. Trust the JSON API.)

## To publish

```bash
# 1. add the co-founder
#    authors = [{name = "..."}, {name = "..."}]

# 2. rebuild after any metadata change
rm -rf dist build *.egg-info
python -m build
python -m twine check dist/*

# 3. dry run against TestPyPI first — strongly recommended
python -m twine upload --repository testpypi dist/*
pip install -i https://test.pypi.org/simple/ claude-guard
claude-guard doctor

# 4. real upload
python -m twine upload dist/*
```

## Post-publish

```bash
pip install claude-guard
claude-guard doctor     # must print "Cost tracking is working."
claude-guard init
```

Then tag the release: `git tag v0.2.0 && git push --tags`.

## Do not publish 0.1.0 semantics

0.1.0 was a no-op: it found zero session files on every machine, never recorded
cost, and never blocked anything. If it is ever uploaded, yank it. See the
"0.2.0 — correctness release" table in README.md.
