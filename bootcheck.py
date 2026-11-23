#!/usr/bin/env python3
"""bootcheck — your QNX buildfile, checked before it bricks your board.

Reads a QNX mkifs buildfile (the thing that tells mkifs how to pack your
OS image), parses it the way mkifs would, and flags the mistakes that only
show up when your board refuses to boot. Then it prints the image layout
so you can see what you're actually shipping.

No deps. Python 3.8+ stdlib only. Runs on any Linux host.

Usage:
    bootcheck [options] <buildfile>
    bootcheck --help

Exit codes: 0 = clean (warnings are fine), 1 = errors found.
"""

import difflib
import json
import os
import re
import sys

VERSION = "0.1.0"

# ---------------------------------------------------------------------------
# What mkifs actually knows about (QNX SDP 8.0 docs, mkifs reference).
# Boolean attrs take +name / -name. Value attrs take name=value.
# ---------------------------------------------------------------------------
BOOL_ATTRS = {
    "autolink", "big_pages", "bigendian", "followlink", "include",
    "keeplinked", "optional", "page_align", "raw", "script",
}
VALUE_ATTRS = {
    "autoso", "cd", "chain", "cksum", "dperms", "drop", "filter", "gid",
    "image", "keepsection", "linker", "module", "mount", "mtime",
    "pagesizes", "perms", "phys_align", "physical", "prefix", "ram",
    "search", "sha256", "type", "uid", "virtual",
}
# compress is a bare token in the docs (no value). data= is old-school,
# gone from the SDP 8.0 attribute list — still accepted, with a nudge.
BARE_ATTRS = {"compress"}
LEGACY_ATTRS = {"data"}

KNOWN_ATTRS = BOOL_ATTRS | VALUE_ATTRS | BARE_ATTRS | LEGACY_ATTRS
KNOWN_CPUS = {"aarch64le", "armle-v7", "armle", "x86_64", "x86"}
KNOWN_FORMATS = {"raw", "srec", "elf", "bin"}
PROCNTO_NAMES = {"procnto", "procnto-smp", "procnto-instr"}
LINK_TYPES = {"link"}

# mkifs host search order, from the SDP 8.0 docs (-r rootdir).
MKIFS_SEARCH_SUBDIRS = [
    "{cpu}/sbin", "{cpu}/usr/sbin", "{cpu}/boot/sys", "{base}/boot/sys",
    "{cpu}/bin", "{cpu}/usr/bin", "{cpu}/lib", "{cpu}/lib/dll",
    "{cpu}/usr/lib",
]


class Issue:
    """One thing wrong (or iffy) with the buildfile."""

    def __init__(self, code, level, line, message, hint=""):
        self.code = code          # e.g. E003
        self.level = level        # "error" or "warning"
        self.line = line          # line number, 0 = whole file
        self.message = message
        self.hint = hint

    def to_dict(self):
        d = {"code": self.code, "level": self.level, "line": self.line,
             "message": self.message}
        if self.hint:
            d["hint"] = self.hint
        return d

    def __str__(self):
        where = f"line {self.line}: " if self.line else ""
        out = f"[{self.code}] {where}{self.message}"
        if self.hint:
            out += f" ({self.hint})"
        return out


class Entry:
    """One file going into the image."""

    def __init__(self, line, target, kind, host=None, inline_lines=None,
                 attrs=None, mapped=False):
        self.line = line
        self.target = target            # path inside the image
        self.kind = kind                # file | link | inline | bootstrap | script
        self.host = host                # host path, if any
        self.inline_lines = inline_lines or []   # raw lines, for inline files
        self.attrs = attrs or {}        # effective attributes
        self.mapped = mapped            # came from a target=host line
        self.size = None                # host bytes, if resolved
        self.resolved = None            # resolved host path, if found


class Buildfile:
    def __init__(self, path):
        self.path = path
        self.entries = []
        self.issues = []
        self.sticky = {}                # attrs from bare [attr] lines
        self.virtual_cpu = None
        self.virtual_format = None

    def error(self, code, line, message, hint=""):
        self.issues.append(Issue(code, "error", line, message, hint))

    def warn(self, code, line, message, hint=""):
        self.issues.append(Issue(code, "warning", line, message, hint))

    @property
    def errors(self):
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self):
        return [i for i in self.issues if i.level == "warning"]

    def bootstrap(self):
        return [e for e in self.entries if e.kind == "bootstrap"]

    def script(self):
        return [e for e in self.entries if e.kind == "script"]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _strip_comment(line, lineno, bf):
    """Cut a # comment. mkifs wants a space before the #, flag it if missing."""
    idx = line.find("#")
    if idx == -1:
        return line
    if idx > 0 and line[idx - 1] not in " \t":
        bf.warn("W003", lineno,
                "comment marker '#' with no space before it",
                "mkifs wants a space before '#', else it might not see the comment")
        return line[:idx]
    return line[:idx]


def _parse_attr_token(tok, lineno, bf):
    """Parse one attribute token. Returns (name, value, conditional) or None."""
    conditional = False
    if tok.startswith("?"):
        conditional = True
        tok = tok[1:]
    if not tok:
        return None
    if tok[0] in "+-":
        name, value = tok[1:], (tok[0] == "+")
        kind = "bool"
    elif "=" in tok:
        name, value = tok.split("=", 1)
        kind = "value"
    else:
        name, value, kind = tok, True, "bare"
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name or ""):
        bf.error("E016", lineno, f"junk in attribute token '{tok}'",
                 "attributes look like +name, -name, or name=value — no spaces around '='")
        return None
    if name in LEGACY_ATTRS:
        bf.warn("W006", lineno, f"'{name}' is old-school",
                "it fell off the attribute list in SDP 8.0 — mkifs may just ignore it")
    if name not in KNOWN_ATTRS:
        guess = difflib.get_close_matches(name, sorted(KNOWN_ATTRS), n=1)
        hint = f"did you mean '{guess[0]}'?" if guess else \
            "check the mkifs attribute list in the SDP 8.0 docs"
        bf.error("E004", lineno, f"unknown attribute '{name}'", hint)
        return None
    if kind == "bool" and name not in BOOL_ATTRS:
        bf.error("E016", lineno, f"'{name}' isn't a +/- attribute",
                 f"'{name}' takes a value: {name}=...")
        return None
    if kind == "value" and name not in VALUE_ATTRS and name not in LEGACY_ATTRS:
        bf.error("E016", lineno, f"'{name}' doesn't take a value",
                 "it's a bare flag — just write it plain")
        return None
    if kind == "bare" and name not in BARE_ATTRS and name not in BOOL_ATTRS:
        bf.error("E016", lineno, f"'{name}' needs a value",
                 f"write it as {name}=...")
        return None
    if name in BOOL_ATTRS and kind == "bare":
        value = True
    return (name, value, conditional)


def _parse_attr_group(text, lineno, bf):
    """Parse the [ ... ] group at the start of a line. Returns dict."""
    inner = text[1:-1].strip()
    attrs = {}
    if not inner:
        bf.error("E016", lineno, "empty attribute brackets '[]'",
                 "drop the brackets or put something in them")
        return attrs
    for tok in inner.split():
        parsed = _parse_attr_token(tok, lineno, bf)
        if parsed:
            name, value, _cond = parsed
            attrs[name] = value
    return attrs


def parse_buildfile(path, lines=None):
    """Parse a buildfile into a Buildfile. All the mkifs grammar lives here."""
    bf = Buildfile(path)
    if lines is None:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError as e:
            bf.error("E000", 0, f"can't read the buildfile: {e}")
            return bf

    # Walk raw lines. Outside inline files, a trailing backslash joins the
    # next line (mkifs line continuation). Inside inline files a backslash
    # is just an escape char, so those lines are read raw, no joining.
    i = 0
    n = len(lines)
    while i < n:
        start = i + 1
        text = lines[i]
        i += 1
        while text.endswith("\\") and not text.endswith("\\\\"):
            if i >= n:
                break
            text = text[:-1] + lines[i]
            i += 1
        lineno, line = start, text
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        line = _strip_comment(line, lineno, bf)
        if not line.strip():
            continue

        # --- attributes ------------------------------------------------
        attrs = {}
        rest = line.strip()
        if rest.startswith("["):
            m = re.match(r"(\[[^\]]*\])", rest)
            if not m:
                bf.error("E016", lineno, "attribute brackets never close",
                         "every '[' needs its ']' on the same line")
                continue
            group = m.group(1)
            attrs = _parse_attr_group(group, lineno, bf)
            rest = rest[len(group):].strip()
            if rest.startswith("["):
                bf.error("E005", lineno,
                         "two attribute groups on one line",
                         "mkifs only takes one [...] per line — put both attrs in the same brackets")
                continue
        if not rest:
            # bare [attrs] line — applies to everything after it
            bf.sticky.update(attrs)
            continue

        # --- file spec -------------------------------------------------
        effective = dict(bf.sticky)
        effective.update(attrs)
        i = _parse_file_spec(bf, rest, lineno, effective, lines, i)
    return bf


def _parse_file_spec(bf, rest, lineno, attrs, raw_lines, i):
    """Parse one file-spec line. Consumes inline bodies from raw_lines and
    returns the index of the next unconsumed line."""
    m = re.match(r"^(\"[^\"]+\"|\S+)\s*=\s*\{\s*(.*)$", rest)
    if m:
        target = m.group(1).strip('"')
        after_brace = m.group(2)
        if after_brace:
            bf.error("E014", lineno,
                     "stuff on the same line as the opening '{'",
                     "inline file contents can't sit on the '{' line — start them on the next line")
        body = []
        closed = False
        while i < len(raw_lines):
            jline = raw_lines[i]
            i += 1
            if jline.strip() == "}":
                closed = True
                break
            body.append(jline)
        if not closed:
            bf.error("E006", lineno,
                     f"inline file '{target}' never closes",
                     "every '{' needs its '}' — mkifs would eat the rest of the file")
            return i
        kind = "bootstrap" if target == ".bootstrap" else \
            "script" if target == ".script" else "inline"
        bf.entries.append(Entry(lineno, target, kind,
                                inline_lines=body, attrs=attrs))
        return i

    m = re.match(r"^(\"[^\"]+\"|\S+)\s*=\s*(\"[^\"]+\"|\S+)\s*$", rest)
    if m:
        target, host = m.group(1).strip('"'), m.group(2).strip('"')
        kind = "link" if attrs.get("type") in LINK_TYPES else "file"
        bf.entries.append(Entry(lineno, target, kind, host=host,
                                attrs=attrs, mapped=True))
        return i

    m = re.match(r"^(\"[^\"]+\"|\S+)\s*$", rest)
    if m:
        target = m.group(1).strip('"')
        kind = "link" if attrs.get("type") in LINK_TYPES else "file"
        bf.entries.append(Entry(lineno, target, kind, host=target,
                                attrs=attrs))
        return i

    bf.error("E016", lineno, f"can't make sense of this line: '{rest}'",
             "a line is [attrs] then a path, target=host, or target={ ... }")
    return i

# ---------------------------------------------------------------------------
# Semantic checks — the stuff that bricks boards
# ---------------------------------------------------------------------------

def _cmd_name(line):
    """First real command on a bootstrap/script line, skipping VAR=val
    assignments up front. '/proc/boot/procnto -vv' -> 'procnto'."""
    for tok in line.strip().split():
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tok):
            continue
        return os.path.basename(tok)
    return ""


def check_buildfile(bf):
    entries = bf.entries
    if not entries:
        bf.error("E013", 0, "nothing in here — empty buildfile",
                 "a buildfile with no files builds no image")
        return
    boots = bf.bootstrap()
    scripts = bf.script()

    if not boots:
        bf.error("E001", 0, "no .bootstrap section",
                 "mkifs got nothing to boot from — .bootstrap is where startup and procnto live")
    elif len(boots) > 1:
        for b in boots[1:]:
            bf.error("E009", b.line, ".bootstrap shows up twice",
                     "one image, one bootstrap")
    if len(scripts) > 1:
        for s in scripts[1:]:
            bf.error("E009", s.line, ".script shows up twice",
                     "one image, one boot script")
    if not scripts:
        bf.warn("W001", 0, "no .script section",
                "image boots and then just... sits there. .script is what starts your drivers and shell")

    for b in boots:
        virt = b.attrs.get("virtual")
        if virt is None:
            bf.error("E002", b.line, ".bootstrap has no virtual= attribute",
                     "that's how mkifs knows the CPU and the image format — like virtual=aarch64le,raw")
        else:
            parts = str(virt).split(",")
            cpu = parts[0] if parts else ""
            fmt = parts[1] if len(parts) > 1 else None
            bf.virtual_cpu = cpu or None
            bf.virtual_format = fmt
            if cpu and cpu not in KNOWN_CPUS:
                bf.warn("W015", b.line, f"CPU '{cpu}' — never heard of it",
                        "known ones: " + ", ".join(sorted(KNOWN_CPUS)))
            if fmt and fmt not in KNOWN_FORMATS:
                bf.warn("W009", b.line, f"image format '{fmt}' looks off",
                        "usual formats: raw, srec, elf, bin")
        lines = [ln for ln in b.inline_lines
                 if ln.strip() and not ln.strip().startswith("#")]
        if not lines:
            bf.error("E017", b.line, ".bootstrap is empty",
                     "it needs at least a startup-* and procnto")
            continue
        first = _cmd_name(lines[0])
        if first and not first.startswith("startup-"):
            bf.warn("W002", b.line,
                    f".bootstrap starts with '{first}', not a startup-*",
                    "startup-* sets the board up first — it should be line one")
        cmds = [_cmd_name(ln) for ln in lines]
        proc_idx = [i for i, c in enumerate(cmds) if c in PROCNTO_NAMES]
        if not proc_idx:
            bf.error("E003", b.line, "no procnto in .bootstrap",
                     "that's the microkernel, my guy — no procnto, no boot")
        else:
            before = " ".join(lines[:proc_idx[0] + 1])
            if "PATH=" not in before:
                bf.warn("W011", b.line, "no PATH= set before procnto",
                        "procnto needs PATH=/proc/boot to find its startup helpers")

    for s in scripts:
        if not s.attrs.get("script"):
            bf.error("E010", s.line, ".script is missing the +script attribute",
                     "without +script, mkifs treats it as a plain data file and nothing runs at boot")

    seen = {}
    for e in entries:
        if e.kind in ("bootstrap", "script"):
            continue
        if e.target in seen:
            bf.error("E007", e.line,
                     f"'{e.target}' is already in the image (first seen line {seen[e.target]})",
                     "two files can't live at the same path — one of them just vanishes")
        else:
            seen[e.target] = e.line
        if e.attrs.get("type") in LINK_TYPES and not e.mapped:
            bf.error("E008", e.line, f"link '{e.target}' has no source",
                     "links look like [type=link] /dev/console=/dev/ser1 — target=source")
        for key in ("uid", "gid"):
            v = e.attrs.get(key)
            if v is not None and not re.fullmatch(r"\d+", str(v)):
                bf.error("E011", e.line, f"{key}='{v}' ain't a number",
                         "uid/gid are plain numbers, like uid=0")
        for key in ("perms", "dperms"):
            v = e.attrs.get(key)
            if v is not None and not re.fullmatch(r"[0-7]{3,4}", str(v)):
                bf.error("E012", e.line, f"{key}='{v}' ain't valid octal",
                         "perms look like 0755 or 0644")
        if e.attrs.get("script"):
            bf.warn("W004", e.line, f"+script on '{e.target}' — but that ain't .script",
                    "the +script flag only means something on .script itself")
        t = e.attrs.get("type")
        if t is not None and t not in LINK_TYPES:
            bf.warn("W013", e.line, f"type='{t}' — mkifs only really knows type=link",
                    "anything else is probably a typo")


# ---------------------------------------------------------------------------
# Host file resolution (like mkifs -r, minus the license check)
# ---------------------------------------------------------------------------

def resolve_host(host, roots, cpu):
    """Find a host file the way mkifs would. Returns a path or None."""
    if os.path.isabs(host):
        return host if os.path.isfile(host) else None
    if os.path.isfile(host):
        return host
    base = (cpu or "aarch64le").split("-")[0]
    subs = [s.format(cpu=cpu or "aarch64le", base=base)
            for s in MKIFS_SEARCH_SUBDIRS]
    for root in roots:
        if os.path.isfile(os.path.join(root, host)):
            return os.path.join(root, host)
        if "/" not in host and "\\" not in host:
            for sub in subs:
                cand = os.path.join(root, sub, host)
                if os.path.isfile(cand):
                    return cand
    return None


def check_host_files(bf, roots):
    """Flag image files that don't exist on the host."""
    for e in bf.entries:
        if e.kind in ("bootstrap", "script", "inline", "link"):
            continue
        if e.host is None:
            continue
        found = resolve_host(e.host, roots, bf.virtual_cpu)
        if found:
            e.resolved = found
            try:
                e.size = os.path.getsize(found)
            except OSError:
                e.size = None
        else:
            bf.warn("W005", e.line, f"can't find '{e.host}' on the host",
                    "try --root with your QNX_TARGET, or check the name")


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _fmt_attrs(attrs):
    bits = []
    for k in sorted(attrs):
        v = attrs[k]
        bits.append(f"+{k}" if v is True else f"-{k}" if v is False
                    else f"{k}={v}")
    return " ".join(bits)


def report_text(bf):
    lines = []
    lines.append(f"bootcheck {VERSION} — {bf.path}")
    lines.append("")
    boots = bf.bootstrap()
    if boots:
        b = boots[0]
        cpu = bf.virtual_cpu or "?"
        fmt = bf.virtual_format or "?"
        lines.append("BOOT")
        lines.append(f"  cpu {cpu} · format {fmt}")
        cmds = [_cmd_name(ln) for ln in b.inline_lines if ln.strip()]
        starts = [c for c in cmds if c.startswith("startup-")]
        procs = [c for c in cmds if c in PROCNTO_NAMES]
        if starts:
            lines.append(f"  startup: {starts[0]} (first, good)" if cmds and cmds[0] == starts[0]
                         else f"  startup: {starts[0]} (not first — see W002)")
        if procs:
            lines.append(f"  kernel: {procs[0]}")
        scripts = bf.script()
        if scripts:
            n = len([ln for ln in scripts[0].inline_lines if ln.strip()])
            flag = "+script set" if scripts[0].attrs.get("script") else "+script MISSING"
            lines.append(f"  boot script: .script, {n} lines, {flag}")
        lines.append("")
    counts = {"file": 0, "link": 0, "inline": 0, "bootstrap": 0, "script": 0}
    for e in bf.entries:
        counts[e.kind] = counts.get(e.kind, 0) + 1
    total = len(bf.entries)
    lines.append(f"IMAGE — {total} entries "
                 f"({counts['file']} files, {counts['link']} links, "
                 f"{counts['inline'] + counts['bootstrap'] + counts['script']} inline)")
    rows = []
    for e in bf.entries:
        if e.kind in ("bootstrap", "script"):
            detail = f"inline, {len(e.inline_lines)} lines"
        elif e.kind == "link":
            detail = f"-> {e.host}"
        elif e.kind == "inline":
            detail = f"inline, {len(e.inline_lines)} lines"
        else:
            detail = e.host or ""
            if e.size is not None:
                detail += f" ({e.size} bytes)"
        attrs = _fmt_attrs(e.attrs)
        if attrs:
            detail += f" [{attrs}]"
        rows.append((e.target, e.kind, detail))
    width = max((len(r[0]) for r in rows), default=10)
    for target, kind, detail in rows:
        lines.append(f"  {target.ljust(width)}  {kind.ljust(9)}  {detail}")
    found_bytes = sum(e.size or 0 for e in bf.entries if e.size)
    missing = [e for e in bf.entries
               if e.kind == "file" and e.resolved is None]
    if found_bytes:
        lines.append(f"  host bytes found: {found_bytes:,}")
    if missing:
        lines.append(f"  missing on host: {len(missing)} file(s)")
    lines.append("")
    n_err, n_warn = len(bf.errors), len(bf.warnings)
    if n_err == 0 and n_warn == 0:
        lines.append("ISSUES — none. clean. ship it.")
    else:
        lines.append(f"ISSUES — {n_err} error{'s' if n_err != 1 else ''}, "
                     f"{n_warn} warning{'s' if n_warn != 1 else ''}")
        for iss in sorted(bf.issues, key=lambda x: (x.line, x.code)):
            lines.append(f"  {iss}")
    return "\n".join(lines) + "\n"


def report_json(bf):
    data = {
        "tool": "bootcheck",
        "version": VERSION,
        "file": bf.path,
        "virtual": {"cpu": bf.virtual_cpu, "format": bf.virtual_format},
        "summary": {
            "entries": len(bf.entries),
            "errors": len(bf.errors),
            "warnings": len(bf.warnings),
            "host_bytes_found": sum(e.size or 0 for e in bf.entries if e.size),
        },
        "entries": [
            {"line": e.line, "target": e.target, "kind": e.kind,
             "host": e.host, "attrs": e.attrs,
             "inline_lines": len(e.inline_lines),
             "size": e.size, "resolved": e.resolved}
            for e in bf.entries
        ],
        "issues": [i.to_dict() for i in bf.issues],
    }
    return json.dumps(data, indent=2) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(
        prog="bootcheck",
        description="Your QNX buildfile, checked before it bricks your board.")
    p.add_argument("buildfile", help="mkifs buildfile to check ('-' reads stdin)")
    p.add_argument("--root", action="append", default=[],
                   help="host root to look for files in (like mkifs -r); repeatable")
    p.add_argument("--json", action="store_true",
                   help="machine-readable report instead of the human one")
    p.add_argument("--quiet", action="store_true",
                   help="only print the issues, skip the layout")
    p.add_argument("--strict", action="store_true",
                   help="warnings count as errors for the exit code")
    p.add_argument("--no-host-check", action="store_true",
                   help="skip checking that host files exist")
    p.add_argument("--version", action="store_true",
                   help="print the version and bounce")
    args = p.parse_args(argv)

    if args.version:
        print(f"bootcheck {VERSION}")
        return 0

    if args.buildfile == "-":
        lines = sys.stdin.read().splitlines()
        bf = parse_buildfile("<stdin>", lines)
    else:
        bf = parse_buildfile(args.buildfile)

    check_buildfile(bf)
    if not args.no_host_check:
        check_host_files(bf, args.root)

    if args.json:
        print(report_json(bf), end="")
    elif args.quiet:
        if bf.issues:
            for iss in sorted(bf.issues, key=lambda x: (x.line, x.code)):
                print(iss)
        else:
            print("clean. ship it.")
    else:
        print(report_text(bf), end="")

    if bf.errors or (args.strict and bf.warnings):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
