# The lvkit cache

lvkit reads a `.vi` binary by unpacking it to XML, and keeps that work so the
next command on the same VI starts instantly. The cache never goes in your
project folder, and your VIs are never modified.

## Where it lives

`~/.lvkit/cache` — on Windows, `%USERPROFILE%\.lvkit\cache`.

Set the `LVKIT_CACHE_DIR` environment variable to use a different folder:

```bash
# Linux / macOS
export LVKIT_CACHE_DIR=/data/lvkit-cache
```

```bat
:: Windows (cmd)
setx LVKIT_CACHE_DIR D:\lvkit-cache
```

Pick a short path on Windows. A very deep project plus a long cache path can
reach the 260-character path limit, and lvkit warns you when a cache path is
close to it.

Everything in the cache is derived from your VIs, so you can delete the whole
folder at any time; lvkit rebuilds what it needs. Unused entries are cleaned up
automatically (set `LVKIT_CACHE_TTL_DAYS` and `LVKIT_EXTRACT_TTL_DAYS` to change
how long they are kept: 7 and 60 days by default).

## Running lvkit under security software

Managed Windows machines often run behavior-monitoring antivirus that
terminates a program when it creates and renames many files in a short time. A
cold run on a large project does exactly that, because it unpacks every VI it
touches into the cache.

To avoid this:

1. Set `LVKIT_CACHE_DIR` to one dedicated folder (see above).
2. Ask IT to exclude that one folder from behavior monitoring. Nothing else
   needs an exclusion, and neither Python nor `lvkit.exe` has to be excluded.

lvkit keeps its temporary files in a single `.staging` folder inside the cache,
one subfolder per running command, and publishes each unpacked VI with one
folder rename. A command that is killed leaves its subfolder behind; the next
lvkit run removes it.

If your policy treats the `lvkit.exe` launcher differently from `python.exe`,
run lvkit through Python instead. Every command works the same way:

```bash
python -m lvkit describe "My VI.vi"
python -m lvkit structure MyProject.lvproj
```

## See also

- [install](install.md) — get lvkit in front of your agent.
- [query](query.md) — the project index, which lives in the same cache.
