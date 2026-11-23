"""Tests for bootcheck. stdlib unittest only — no deps to install.

Run:  python3 -m unittest discover -s tests -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bootcheck

GOOD = """[virtual=aarch64le,raw] .bootstrap = {
    startup-bsp
    PATH=/proc/boot procnto -vv
}
[+script] .script = {
    devc-ser8250 &
    reopen
}
[type=link] /dev/console=/dev/ser1
libc.so.2
"""

MINIMAL = """[virtual=x86_64,raw] .bootstrap = {
    startup-bsp
    PATH=/proc/boot procnto
}
[+script] .script = {
    reopen
}
"""


def parse(text):
    bf = bootcheck.parse_buildfile("<test>", text.splitlines())
    bootcheck.check_buildfile(bf)
    return bf


def codes(bf, level=None):
    return sorted(i.code for i in bf.issues
                  if level is None or i.level == level)


class TestGoodFiles(unittest.TestCase):
    def test_good_buildfile_is_clean(self):
        bf = parse(GOOD)
        self.assertEqual(bf.errors, [])
        self.assertEqual(bf.warnings, [])

    def test_minimal_buildfile_is_clean(self):
        bf = parse(MINIMAL)
        self.assertEqual(bf.errors, [])
        self.assertEqual(bf.warnings, [])

    def test_real_example_files(self):
        root = os.path.join(os.path.dirname(__file__), "..", "examples")
        for name in ("good-aarch64.bld", "minimal.bld"):
            bf = bootcheck.parse_buildfile(os.path.join(root, name))
            bootcheck.check_buildfile(bf)
            bootcheck.check_host_files(
                bf, [os.path.join(root, "host")])
            self.assertEqual(bf.errors, [], f"{name}: {bf.issues}")
            self.assertEqual(bf.warnings, [], f"{name}: {bf.issues}")

    def test_virtual_parsed(self):
        bf = parse(GOOD)
        self.assertEqual(bf.virtual_cpu, "aarch64le")
        self.assertEqual(bf.virtual_format, "raw")

    def test_layout_counts(self):
        bf = parse(GOOD)
        kinds = [e.kind for e in bf.entries]
        self.assertEqual(kinds.count("bootstrap"), 1)
        self.assertEqual(kinds.count("script"), 1)
        self.assertEqual(kinds.count("link"), 1)
        self.assertEqual(kinds.count("file"), 1)


class TestBootstrapErrors(unittest.TestCase):
    def test_no_bootstrap(self):
        bf = parse("[+script] .script = {\n    reopen\n}\n")
        self.assertIn("E001", codes(bf, "error"))

    def test_bootstrap_without_virtual(self):
        bf = parse(".bootstrap = {\n    startup-bsp\n    PATH=/proc/boot procnto\n}\n"
                   "[+script] .script = {\n    reopen\n}\n")
        self.assertIn("E002", codes(bf, "error"))

    def test_bootstrap_without_procnto(self):
        bf = parse("[virtual=aarch64le,raw] .bootstrap = {\n    startup-bsp\n}\n"
                   "[+script] .script = {\n    reopen\n}\n")
        self.assertIn("E003", codes(bf, "error"))

    def test_bootstrap_twice(self):
        bf = parse("[virtual=aarch64le,raw] .bootstrap = {\n    startup-bsp\n    procnto\n}\n"
                   "[virtual=aarch64le,raw] .bootstrap = {\n    startup-bsp\n    procnto\n}\n")
        self.assertIn("E009", codes(bf, "error"))

    def test_bootstrap_empty(self):
        bf = parse("[virtual=aarch64le,raw] .bootstrap = {\n}\n")
        self.assertIn("E017", codes(bf, "error"))

    def test_startup_not_first(self):
        bf = parse("[virtual=aarch64le,raw] .bootstrap = {\n"
                   "    PATH=/proc/boot procnto\n    startup-bsp\n}\n")
        self.assertIn("W002", codes(bf, "warning"))

    def test_no_path_before_procnto(self):
        bf = parse("[virtual=aarch64le,raw] .bootstrap = {\n"
                   "    startup-bsp\n    procnto\n}\n")
        self.assertIn("W011", codes(bf, "warning"))

    def test_unknown_cpu_warns(self):
        bf = parse("[virtual=mipsle,raw] .bootstrap = {\n"
                   "    startup-bsp\n    PATH=/proc/boot procnto\n}\n")
        self.assertIn("W015", codes(bf, "warning"))

    def test_no_script_warns(self):
        bf = parse("[virtual=aarch64le,raw] .bootstrap = {\n"
                   "    startup-bsp\n    PATH=/proc/boot procnto\n}\n")
        self.assertIn("W001", codes(bf, "warning"))

    def test_script_without_plus_script(self):
        bf = parse("[virtual=aarch64le,raw] .bootstrap = {\n"
                   "    startup-bsp\n    PATH=/proc/boot procnto\n}\n"
                   ".script = {\n    reopen\n}\n")
        self.assertIn("E010", codes(bf, "error"))


class TestAttributeErrors(unittest.TestCase):
    def test_unknown_attribute_with_suggestion(self):
        bf = parse("[uidd=0] foo\n")
        self.assertIn("E004", codes(bf, "error"))
        msg = " ".join(i.message + i.hint for i in bf.issues)
        self.assertIn("uid", msg)

    def test_two_attribute_groups(self):
        bf = parse("[uid=5] [gid=5] foo\n")
        self.assertIn("E005", codes(bf, "error"))

    def test_empty_brackets(self):
        bf = parse("[] foo\n")
        self.assertIn("E016", codes(bf, "error"))

    def test_uid_not_numeric(self):
        bf = parse("[uid=root] foo\n")
        self.assertIn("E011", codes(bf, "error"))

    def test_perms_not_octal(self):
        bf = parse("[perms=999] foo\n")
        self.assertIn("E012", codes(bf, "error"))

    def test_legacy_data_warns(self):
        bf = parse("[data=copy] foo\n")
        self.assertIn("W006", codes(bf, "warning"))

    def test_plus_script_on_plain_file(self):
        bf = parse("[+script] foo\n")
        self.assertIn("W004", codes(bf, "warning"))

    def test_sticky_attributes_apply(self):
        bf = parse("[uid=0 gid=0]\nfoo\nbar\n")
        self.assertEqual(bf.entries[0].attrs.get("uid"), "0")
        self.assertEqual(bf.entries[1].attrs.get("gid"), "0")

    def test_conditional_attribute_accepted(self):
        # parse only — no .bootstrap here, so skip the semantic check
        bf = bootcheck.parse_buildfile("<test>", "[?+keeplinked] foo".splitlines())
        self.assertEqual(bf.errors, [])

    def test_comment_without_space_warns(self):
        bf = parse("foo# no space\n")
        self.assertIn("W003", codes(bf, "warning"))


class TestFileSpecErrors(unittest.TestCase):
    def test_duplicate_target(self):
        bf = parse("foo\nfoo\n")
        self.assertIn("E007", codes(bf, "error"))

    def test_link_without_source(self):
        bf = parse("[type=link] /dev/console\n")
        self.assertIn("E008", codes(bf, "error"))

    def test_unclosed_inline(self):
        bf = parse("/proc/boot/note={\n    never closes\n")
        self.assertIn("E006", codes(bf, "error"))

    def test_content_on_brace_line(self):
        bf = parse(".script = { reopen\n    esh\n}\n")
        self.assertIn("E014", codes(bf, "error"))

    def test_empty_buildfile(self):
        bf = parse("# just a comment\n\n")
        self.assertIn("E013", codes(bf, "error"))

    def test_inline_contents_not_parsed(self):
        # a '#' inside an inline file is data, not a comment;
        # [+session] inside .script is data, not an attribute
        bf = parse("[virtual=aarch64le,raw] .bootstrap = {\n"
                   "    startup-bsp\n    PATH=/proc/boot procnto\n}\n"
                   "[+script] .script = {\n"
                   "    # this is a comment for the target shell\n"
                   "    [+session] esh\n"
                   "}\n")
        self.assertEqual(bf.errors, [])

    def test_line_continuation(self):
        # parse only — no .bootstrap here, so skip the semantic check
        bf = bootcheck.parse_buildfile(
            "<test>", "[uid=0 gid=0 \\\n perms=0755] foo".splitlines())
        self.assertEqual(bf.errors, [])
        self.assertEqual(bf.entries[0].attrs.get("perms"), "0755")


class TestHostCheck(unittest.TestCase):
    def test_found_and_missing(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "aarch64le", "bin"))
            with open(os.path.join(d, "aarch64le", "bin", "esh"), "wb") as f:
                f.write(b"x" * 100)
            bf = parse("[virtual=aarch64le,raw] .bootstrap = {\n"
                       "    startup-bsp\n    PATH=/proc/boot procnto\n}\n"
                       "[+script] .script = {\n    reopen\n}\n"
                       "esh\nnope-not-here\n")
            bootcheck.check_host_files(bf, [d])
            self.assertEqual([e for e in bf.entries if e.host == "esh"][0].size, 100)
            self.assertIn("W005", codes(bf, "warning"))


class TestCLI(unittest.TestCase):
    def test_json_output_parses(self):
        root = os.path.join(os.path.dirname(__file__), "..")
        out = subprocess.run(
            [sys.executable, os.path.join(root, "bootcheck.py"), "--json",
             os.path.join(root, "examples", "good-aarch64.bld")],
            capture_output=True, text=True)
        self.assertEqual(out.returncode, 0)
        data = json.loads(out.stdout)
        self.assertEqual(data["summary"]["errors"], 0)
        self.assertEqual(data["virtual"]["cpu"], "aarch64le")

    def test_exit_code_on_broken(self):
        root = os.path.join(os.path.dirname(__file__), "..")
        out = subprocess.run(
            [sys.executable, os.path.join(root, "bootcheck.py"),
             os.path.join(root, "examples", "broken.bld")],
            capture_output=True, text=True)
        self.assertEqual(out.returncode, 1)
        self.assertIn("error", out.stdout)

    def test_broken_example_issue_count(self):
        root = os.path.join(os.path.dirname(__file__), "..")
        bf = bootcheck.parse_buildfile(
            os.path.join(root, "examples", "broken.bld"))
        bootcheck.check_buildfile(bf)
        bootcheck.check_host_files(
            bf, [os.path.join(root, "examples", "host")])
        self.assertEqual(len(bf.errors), 14)
        self.assertEqual(len(bf.warnings), 6)

    def test_stdin(self):
        out = subprocess.run(
            [sys.executable,
             os.path.join(os.path.dirname(__file__), "..", "bootcheck.py"),
             "--no-host-check", "-"],
            input=MINIMAL, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0)


if __name__ == "__main__":
    unittest.main()
