# bootcheck

**Your QNX buildfile, checked before it bricks your board.**

You write a buildfile. `mkifs` packs it into an OS image. You flash it.
Board sits there, dead, no serial output, and you're guessing what went
wrong for the next three hours.

bootcheck reads the buildfile first — on your Linux host, no QNX box
needed, no license, no target hardware — and tells you what's wrong
before you waste a flash cycle.

## run it

No install. Python 3.8+ is all you need, stdlib only, zero deps.

```sh
python3 bootcheck.py --root <your QNX_TARGET> your-board.bld
```

That prints the boot setup (CPU, format, startup, kernel), the full
image layout (what's going in, where it lands, how big it is), and every
issue it found. Exit code 0 means you're good. Exit code 1 means fix
stuff first.

```sh
python3 bootcheck.py --json your-board.bld     # machine-readable, for CI
python3 bootcheck.py --quiet your-board.bld    # issues only, no layout
python3 bootcheck.py --strict your-board.bld   # warnings fail the build too
python3 bootcheck.py - < your-board.bld        # read from stdin
```

## what it catches

Real mkifs grammar, straight from the SDP 8.0 docs — not vibes. Stuff
like:

| code | what it means |
| ---- | ------------- |
| E001 | no `.bootstrap` at all — nothing to boot from |
| E002 | `.bootstrap` with no `virtual=` — mkifs don't know the CPU or format |
| E003 | no `procnto` in `.bootstrap` — that's the kernel, my guy |
| E004 | unknown attribute, with a "did you mean…?" guess |
| E005 | two `[...]` groups on one line (mkifs only takes one) |
| E006 | inline file never closes its `{` |
| E007 | two files landing on the same path in the image |
| E008 | `[type=link]` with no `target=source` |
| E009 | `.bootstrap` / `.script` defined twice |
| E010 | `.script` missing `+script` — nothing runs at boot |
| E011 | `uid=` / `gid=` that ain't a number |
| E012 | `perms=` that ain't octal |
| E013 | empty buildfile |
| E014 | stuff on the same line as the opening `{` |
| E016 | malformed attribute or line it can't parse |
| W001 | no `.script` — image boots and then just sits there |
| W002 | `.bootstrap` doesn't start with a `startup-*` |
| W003 | `#` comment with no space before it |
| W004 | `+script` on a file that ain't `.script` |
| W005 | host file it can't find (with `--root`) |
| W006 | `data=` — old-school, fell off the SDP 8.0 list |
| W009 | image format it don't recognize |
| W011 | no `PATH=` set before `procnto` |
| W013 | `type=` it don't know (only `type=link` is real) |
| W015 | CPU it never heard of |

It also resolves host files the way `mkifs -r` does — CWD first, then
the standard search dirs under each `--root` — and reports real byte
sizes for everything it finds.

## examples

`examples/` has three buildfiles and a fake host tree so you can see it
work without a QNX install:

```sh
python3 bootcheck.py --root examples/host examples/good-aarch64.bld
# -> clean. ship it.

python3 bootcheck.py --root examples/host examples/broken.bld
# -> 14 errors, 6 warnings. every line in there is a mistake on purpose.

python3 bootcheck.py examples/minimal.bld
# -> smallest image that still boots. also clean.
```

## the QNX connection

This ain't a generic linter with "QNX" slapped on the README. It
understands QNX-specific stuff and nothing else:

- **mkifs buildfile grammar** from the QNX SDP 8.0 utilities reference —
  attributes (`virtual=`, `uid=`, `type=link`, `+script`, `+raw`…),
  sticky bare-`[attr]` lines, inline files, line continuations, the
  whole thing.
- **QNX boot flow** — `.bootstrap` (startup-* first, then `procnto`),
  `.script` (needs `+script` or nothing runs), `PATH=/proc/boot`.
- **Targets** — `aarch64le`, `armle-v7`, `x86_64`, image formats
  `raw` / `srec` / `elf` / `bin`.
- **Host tool** — runs on Linux (also fine on macOS/Windows with
  Python 3), no QNX target needed. It reads buildfiles; it doesn't run
  `mkifs`, so no QNX license is involved.

QNX version: **SDP 8.0**. Toolchain: **Python 3.8+ stdlib, no
dependencies**. Target for the images it checks: any QNX 8.0 board
(`aarch64le` in the main example).

## tests

37 of them, stdlib `unittest`, no deps:

```sh
python3 -m unittest discover -s tests -v
```

They cover the good files, every error code, the host resolver, the
JSON output, and stdin.

## why this exists

QNX images fail quiet. A typo in an attribute, a missing `procnto`, a
`.script` without `+script` — `mkifs` might still build you an image,
and the board just won't come up. You find out at the bench, not at
your desk. bootcheck moves that moment to your desk, where it's cheap.

## license

MIT. Do what you want with it, just keep the license file in there.
