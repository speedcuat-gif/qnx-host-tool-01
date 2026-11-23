# DEMO_SCRIPT.md — bootcheck, narrated version (~2 min)

> Voice: casual, like you're showing a friend over your shoulder.
> No corporate voice. Short lines, real talk.

---

**[0:00 — title card]**

"So. You ever flash a QNX image, board sits there dead, no serial
output, and you just... guess for three hours? Yeah. That's what
bootcheck is for."

**[0:12 — good buildfile]**

"This is a normal buildfile for an aarch64 board. Run bootcheck on
it — it shows you the boot setup, the whole image layout, what's
going in, how big it is. And at the bottom: clean. Ship it."

**[0:35 — broken buildfile]**

"Now this one. Somebody had a bad day writing this buildfile. Run
the same command — fourteen errors, six warnings. No procnto, which
is the kernel, my guy. A typo'd attribute. Two files landing on the
same path. A .script missing its +script flag, so nothing would've
run at boot. All of that, caught on your laptop, before you flash
anything."

**[1:00 — JSON mode]**

"And if you live in CI, there's a --json flag. Same report,
machine-readable. Pipe it wherever you want."

**[1:12 — tests]**

"Thirty-seven tests, all passing. Every error code's got a test, the
host resolver's got a test, the examples in the repo are real
buildfiles it actually checks."

**[1:25 — close]**

"That's it. One Python file, no dependencies, no QNX box needed.
Check your buildfile before it bricks your board. bootcheck."

---

## Video notes (silent captioned version, web/demo.mp4)

- 1280x720, ~30s, silent with burned-in captions.
- Scenes, in order:
  1. Title card: "bootcheck — your QNX buildfile, checked before it bricks your board."
  2. Real run on `examples/good-aarch64.bld` — layout table, "clean. ship it."
  3. Real run on `examples/broken.bld` — diagnostics scrolling, "14 errors, 6 warnings."
  4. Real run with `--json` — first lines of the JSON report.
  5. Real run of the test suite — "Ran 37 tests, OK."
  6. End card: repo layout + `python3 bootcheck.py your-board.bld`.
- All terminal output in the video is the real output of the real tool,
  captured on this machine — nothing mocked up.
