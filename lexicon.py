#!/usr/bin/env python3
"""Lexicon: an offline terminology checker for plain-text and Markdown docs.

A glossary file says which term to use, which aliases to flag, which phrases
are banned and which acronyms must be expanded on first use. Lexicon scans
documents against it, reports line-numbered findings, rewrites the fixable
ones and exits non-zero for CI.
"""

import argparse
import difflib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2

DIRECTIVES = ("term", "ban", "acronym")
TEXT_SUFFIXES = (".md", ".markdown", ".txt", ".rst")
MARKDOWN_SUFFIXES = (".md", ".markdown")
FENCES = ("```", "~~~")
IGNORE_MARKER = "lexicon: ignore"
ACRONYM_RE = re.compile(r"[A-Z][A-Z0-9]{1,9}\Z")
INLINE_CODE_RE = re.compile(r"`[^`]*`")


class UsageError(Exception):
    """A glossary or path problem that stops the run before scanning."""


@dataclass
class Term:
    line: int
    preferred: str
    aliases: list
    definition: str = ""


@dataclass
class Ban:
    line: int
    phrase: str
    replacement: str
    reason: str = ""


@dataclass
class Acronym:
    line: int
    short: str
    expansion: str


@dataclass
class Glossary:
    terms: list = field(default_factory=list)
    bans: list = field(default_factory=list)
    acronyms: list = field(default_factory=list)
    errors: list = field(default_factory=list)  # (line, message)


@dataclass
class Rule:
    kind: str  # "alias" or "ban"
    target: str  # preferred term or replacement text
    reason: str
    pattern: re.Pattern


@dataclass
class Hit:
    line: int
    start: int
    end: int
    rule: Rule
    found: str


@dataclass
class Problem:
    """An acronym used in a document before its expansion appears."""

    line: int
    start: int
    acronym: Acronym


@dataclass
class Scan:
    lines: int = 0
    hits: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    usage: Counter = field(default_factory=Counter)


# ---------------------------------------------------------------- glossary


def parse_glossary(text):
    """Parse glossary text into a Glossary, recording line-numbered errors."""
    glossary = Glossary()
    owners = {}  # lowercase name -> (lowercase preferred term, line)
    seen_bans = {}
    seen_acronyms = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        directive, colon, body = line.partition(":")
        directive = directive.strip().lower()
        if not colon or directive not in DIRECTIVES:
            glossary.errors.append(
                (number, "unknown directive; start the line with term:, ban: or acronym:")
            )
        elif directive == "term":
            _add_term(glossary, owners, number, body.strip())
        elif directive == "ban":
            _add_ban(glossary, seen_bans, number, body.strip())
        else:
            _add_acronym(glossary, seen_acronyms, number, body.strip())
    glossary.errors.sort()
    return glossary


def _add_term(glossary, owners, number, body):
    parts = [part.strip() for part in body.split("|", 2)]
    preferred = parts[0]
    if not preferred:
        glossary.errors.append((number, "term is missing its preferred name"))
        return
    aliases = []
    if len(parts) > 1:
        aliases = [alias.strip() for alias in parts[1].split(",") if alias.strip()]
    definition = parts[2] if len(parts) > 2 else ""
    key = preferred.lower()
    if key in owners:
        glossary.errors.append(
            (number, f"'{preferred}' is already defined on line {owners[key][1]}")
        )
        return
    for alias in aliases:
        if alias.lower() != key and alias.lower() in owners:
            glossary.errors.append(
                (number, f"alias '{alias}' already belongs to another term (line {owners[alias.lower()][1]})")
            )
            return
    owners[key] = (key, number)
    for alias in aliases:
        owners.setdefault(alias.lower(), (key, number))
    glossary.terms.append(Term(number, preferred, aliases, definition))


def _add_ban(glossary, seen, number, body):
    parts = [part.strip() for part in body.split("|", 2)]
    if len(parts) < 2 or not parts[0] or not parts[1]:
        glossary.errors.append(
            (number, "ban needs a phrase and a replacement: ban: phrase | replacement | reason")
        )
        return
    key = parts[0].lower()
    if key in seen:
        glossary.errors.append((number, f"'{parts[0]}' is already banned on line {seen[key]}"))
        return
    seen[key] = number
    reason = parts[2] if len(parts) > 2 else ""
    glossary.bans.append(Ban(number, parts[0], parts[1], reason))


def _add_acronym(glossary, seen, number, body):
    parts = [part.strip() for part in body.split("|", 1)]
    short = parts[0]
    if len(parts) < 2 or not parts[1]:
        glossary.errors.append(
            (number, "acronym needs an expansion: acronym: ABBR | Expansion")
        )
    elif not ACRONYM_RE.match(short):
        glossary.errors.append(
            (number, f"'{short}' is not an acronym (2-10 capitals or digits, starting with a letter)")
        )
    elif short in seen:
        glossary.errors.append((number, f"'{short}' is already defined on line {seen[short]}"))
    else:
        seen[short] = number
        glossary.acronyms.append(Acronym(number, short, parts[1]))


def load_glossary(path):
    with open(path, encoding="utf-8", newline="") as handle:
        return parse_glossary(handle.read())


def require_clean_glossary(path, glossary):
    """Print glossary errors to stderr and stop the run if there are any."""
    if not glossary.errors:
        return
    for number, message in glossary.errors:
        print(f"{path}:{number}: error: {message}", file=sys.stderr)
    raise UsageError(f"glossary has {len(glossary.errors)} error(s); fix them before checking")


# ---------------------------------------------------------------- matching


def phrase_pattern(phrase, case_sensitive=False):
    """Whole-phrase pattern: not glued to letters, digits or underscores."""
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", flags)


def build_rules(glossary):
    rules = []
    for term in glossary.terms:
        for alias in term.aliases:
            rules.append(Rule("alias", term.preferred, "", phrase_pattern(alias)))
    for ban in glossary.bans:
        rules.append(Rule("ban", ban.replacement, ban.reason, phrase_pattern(ban.phrase)))
    return rules


def checkable_lines(text, markdown):
    """Yield (number, original, masked) for each line Lexicon should read.

    Markdown fenced code blocks and lines carrying the ignore marker are
    skipped. Inline code spans are blanked out so columns still line up.
    """
    fence = None
    for number, original in enumerate(text.splitlines(), start=1):
        stripped = original.strip()
        if markdown:
            if fence:
                if stripped.startswith(fence):
                    fence = None
                continue
            if stripped.startswith(FENCES):
                fence = stripped[:3]
                continue
        if IGNORE_MARKER in original:
            continue
        masked = INLINE_CODE_RE.sub(lambda m: " " * len(m.group()), original)
        yield number, original, masked


def find_matches(masked, rules):
    """Return non-overlapping (start, end, rule) spans; the longest match wins."""
    candidates = []
    for rule in rules:
        for match in rule.pattern.finditer(masked):
            if rule.kind == "alias" and match.group() == rule.target:
                continue  # already spelled the preferred way
            candidates.append((match.start(), match.end(), rule))
    candidates.sort(key=lambda c: (c[0], c[0] - c[1]))
    accepted, cursor = [], 0
    for start, end, rule in candidates:
        if start >= cursor:
            accepted.append((start, end, rule))
            cursor = end
    return accepted


def scan_document(name, text, glossary, rules):
    scan = Scan()
    markdown = name.lower().endswith(MARKDOWN_SUFFIXES)
    term_patterns = [(term.preferred, phrase_pattern(term.preferred)) for term in glossary.terms]
    acronym_patterns = [(acr, phrase_pattern(acr.short, True)) for acr in glossary.acronyms]
    introduced = set()
    for number, original, masked in checkable_lines(text, markdown):
        scan.lines += 1
        hits = find_matches(masked, rules)
        alias_spans = set()
        for start, end, rule in hits:
            scan.hits.append(Hit(number, start, end, rule, original[start:end]))
            if rule.kind == "alias":
                alias_spans.add((start, end))
                scan.usage[rule.target] += 1
        for preferred, pattern in term_patterns:
            for match in pattern.finditer(masked):
                if (match.start(), match.end()) not in alias_spans:
                    scan.usage[preferred] += 1
        for acronym, pattern in acronym_patterns:
            scan.usage[acronym.short] += len(pattern.findall(masked))
            if acronym.short in introduced:
                continue
            match = pattern.search(masked)
            if match is None:
                continue
            introduced.add(acronym.short)
            if acronym.expansion.lower() not in original.lower():
                scan.problems.append(Problem(number, match.start(), acronym))
    return scan


def match_case(found, replacement):
    if found.lower() == replacement.lower():
        return replacement  # case-only alias: the canonical casing is the fix
    if len(found) > 1 and found.isupper():
        return replacement.upper()
    if found[:1].isupper() and replacement[:1].islower():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def apply_fixes(text, hits):
    """Return text with every alias and banned-phrase hit rewritten."""
    lines = text.splitlines(keepends=True)
    by_line = defaultdict(list)
    for hit in hits:
        by_line[hit.line].append(hit)
    for number, line_hits in by_line.items():
        index = number - 1
        body = lines[index].rstrip("\r\n")
        eol = lines[index][len(body):]
        for hit in sorted(line_hits, key=lambda h: h.start, reverse=True):
            replacement = match_case(hit.found, hit.rule.target)
            body = body[:hit.start] + replacement + body[hit.end:]
        lines[index] = body + eol
    return "".join(lines)


def to_findings(path, scan):
    findings = []
    for hit in scan.hits:
        if hit.rule.kind == "alias":
            message = f'"{hit.found}" should be "{hit.rule.target}"'
        else:
            message = f'"{hit.found}" is banned; use "{hit.rule.target}"'
            if hit.rule.reason:
                message += f" ({hit.rule.reason})"
        findings.append(_finding(path, hit.line, hit.start + 1, "error", hit.rule.kind,
                                 hit.found, hit.rule.target, message))
    for problem in scan.problems:
        acronym = problem.acronym
        suggestion = f"{acronym.expansion} ({acronym.short})"
        message = f'"{acronym.short}" is used before it is expanded; write "{suggestion}" first'
        findings.append(_finding(path, problem.line, problem.start + 1, "warning", "acronym",
                                 acronym.short, suggestion, message))
    findings.sort(key=lambda f: (f["line"], f["column"]))
    return findings


def _finding(path, line, column, severity, rule, found, suggestion, message):
    return {
        "file": path,
        "line": line,
        "column": column,
        "severity": severity,
        "rule": rule,
        "found": found,
        "suggestion": suggestion,
        "message": message,
    }


# ---------------------------------------------------------------- files


def collect_files(paths, glossary_path):
    """Expand directories to text files; the glossary itself is never scanned."""
    skip = os.path.abspath(glossary_path)
    files, seen = [], set()
    for path in paths:
        if os.path.isdir(path):
            found = []
            for root, dirs, names in os.walk(path):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                for name in sorted(names):
                    if name.lower().endswith(TEXT_SUFFIXES):
                        found.append(os.path.join(root, name))
        elif os.path.isfile(path):
            found = [path]
        else:
            raise UsageError(f"no such file or directory: {path}")
        for item in found:
            key = os.path.abspath(item)
            if key != skip and key not in seen:
                seen.add(key)
                files.append(item)
    return files


def read_text(path):
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            return handle.read()
    except UnicodeDecodeError as error:
        raise UsageError(f"{path} is not UTF-8 text: {error}")


def unified_diff(path, before, after):
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=path,
        tofile=path,
    ))


# ---------------------------------------------------------------- commands


def run_lint(args):
    glossary = load_glossary(args.glossary)
    require_clean_glossary(args.glossary, glossary)
    print(f"glossary OK: {len(glossary.terms)} term(s), {len(glossary.bans)} ban(s), "
          f"{len(glossary.acronyms)} acronym(s)")
    return EXIT_OK


def run_check(args):
    glossary = load_glossary(args.glossary)
    require_clean_glossary(args.glossary, glossary)
    rules = build_rules(glossary)
    files = collect_files(args.paths, args.glossary)
    findings, usage, lines = [], Counter(), 0
    for path in files:
        scan = scan_document(path, read_text(path), glossary, rules)
        findings.extend(to_findings(path, scan))
        usage.update(scan.usage)
        lines += scan.lines
    coverage = {term.preferred: usage.get(term.preferred, 0) for term in glossary.terms}
    coverage.update({acr.short: usage.get(acr.short, 0) for acr in glossary.acronyms})
    errors = sum(1 for f in findings if f["severity"] == "error")
    warnings = len(findings) - errors

    if args.format == "json":
        report = {
            "files": len(files),
            "lines": lines,
            "errors": errors,
            "warnings": warnings,
            "findings": findings,
            "coverage": coverage,
        }
        print(json.dumps(report, indent=2))
    else:
        for f in findings:
            print(f"{f['file']}:{f['line']}:{f['column']}: {f['severity']}: {f['message']} [{f['rule']}]")
        print(f"lexicon: {len(files)} file(s), {lines} line(s), {errors} error(s), {warnings} warning(s)")
        if args.coverage:
            unused = sorted(name for name, count in coverage.items() if count == 0)
            print("unused entries: " + (", ".join(unused) if unused else "none"))

    if errors or (args.strict and warnings):
        return EXIT_FINDINGS
    return EXIT_OK


def run_fix(args):
    glossary = load_glossary(args.glossary)
    require_clean_glossary(args.glossary, glossary)
    rules = build_rules(glossary)
    files = collect_files(args.paths, args.glossary)
    changed_files = fixes = 0
    for path in files:
        original = read_text(path)
        scan = scan_document(path, original, glossary, rules)
        fixed = apply_fixes(original, scan.hits)
        if fixed != original:
            changed_files += 1
            fixes += len(scan.hits)
            if args.diff:
                sys.stdout.write(unified_diff(path, original, fixed))
            else:
                with open(path, "w", encoding="utf-8", newline="") as handle:
                    handle.write(fixed)
        for f in to_findings(path, scan):
            if f["severity"] == "warning":
                print(f"{f['file']}:{f['line']}:{f['column']}: {f['severity']}: {f['message']} [{f['rule']}]")
    if args.diff:
        print(f"lexicon: {fixes} fix(es) pending in {changed_files} file(s)")
        return EXIT_FINDINGS if changed_files else EXIT_OK
    print(f"lexicon: {fixes} fix(es) applied in {changed_files} file(s)")
    return EXIT_OK


def build_parser():
    parser = argparse.ArgumentParser(
        prog="lexicon",
        description="Offline terminology checker for plain-text and Markdown docs.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    lint = commands.add_parser("lint", help="validate the glossary file itself")
    lint.add_argument("glossary")

    check = commands.add_parser("check", help="report terminology problems in documents")
    check.add_argument("glossary")
    check.add_argument("paths", nargs="+", help="files or directories to scan")
    check.add_argument("--format", choices=("text", "json"), default="text")
    check.add_argument("--strict", action="store_true", help="treat warnings as failures")
    check.add_argument("--coverage", action="store_true", help="list glossary entries never used")

    fix = commands.add_parser("fix", help="rewrite aliases and banned phrases")
    fix.add_argument("glossary")
    fix.add_argument("paths", nargs="+", help="files or directories to rewrite")
    fix.add_argument("--diff", action="store_true",
                     help="print a unified diff and write nothing; exits 1 if changes are pending")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "lint":
            return run_lint(args)
        if args.command == "check":
            return run_check(args)
        return run_fix(args)
    except (OSError, UsageError) as error:
        print(f"lexicon: {error}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
