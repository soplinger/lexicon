# Lexicon

**Keep your docs saying the same thing.** Lexicon is an offline terminology checker: a plain-text glossary says which term to use, which aliases and banned phrases to flag, and which acronyms must be expanded on first use. It scans your Markdown and text docs, reports line-numbered findings, rewrites the fixable ones and exits non-zero for CI.

It is one Python file, uses only the standard library, and makes no network calls.

## Features

- **Plain-text glossary**: one `term:`, `ban:` or `acronym:` entry per line, with `#` comments.
- **Preferred terms and aliases**: flags `Github`, `github` or `Git Hub` and asks for `GitHub`.
- **Banned phrases** with a replacement and an optional reason, such as inclusive-language swaps.
- **Acronym rule**: the first use of a glossary acronym in each document must show its expansion, otherwise it is warned about.
- **Whole-word, case-insensitive matching**, so `digitgithub` is not flagged, but `GITHUB` in a shouting heading is.
- **Longest match wins**: `Github pages site` beats `Github` when both are aliases.
- **Markdown-aware**: fenced code blocks and inline code spans are never checked.
- **Per-line escape hatch**: add `lexicon: ignore` to any line to exempt it.
- **Exact positions**: every finding is `file:line:column`, with the found text and the suggestion.
- **Automatic fixes**: `fix` rewrites aliases and banned phrases, keeping capitalisation and CRLF line endings.
- **Dry-run diff**: `fix --diff` prints a unified diff and writes nothing; it exits 1 when changes are pending.
- **Coverage report**: `check --coverage` lists glossary entries that no document uses.
- **Glossary linting**: `lint` validates the glossary itself, with line-numbered errors for unknown directives, duplicate terms, alias clashes, bad acronyms and incomplete bans.
- **JSON output** for tools and dashboards with `check --format json`.
- **CI exit codes**: `0` clean, `1` findings, `2` glossary or usage error. `--strict` makes warnings fail too.
- **Directory scanning** that skips hidden folders and the glossary file itself.

## Quick start

The repository includes a small example. Run this from the project root:

```sh
python3 lexicon.py check examples/glossary.txt examples --coverage
```

`examples/glossary.txt` holds the rules:

```text
term: GitHub | Github, github, Git Hub | Code hosting service
term: pull request | pull-request, PR | Proposed change to a branch
term: allowlist | whitelist, white list | Permitted values
term: sign in | log in, login | Authenticate with an account
ban: master branch | main branch | inclusive terminology
ban: blacklist | denylist | inclusive terminology
acronym: API | Application Programming Interface
acronym: CLI | Command-line interface
```

`examples/docs/guide.md` is a document that breaks some of them. Checking it prints:

```text
examples/docs/guide.md:3:12: error: "Github" should be "GitHub" [alias]
examples/docs/guide.md:3:55: error: "pull-request" should be "pull request" [alias]
examples/docs/guide.md:5:5: warning: "CLI" is used before it is expanded; write "Command-line interface (CLI)" first [acronym]
examples/docs/guide.md:5:22: warning: "API" is used before it is expanded; write "Application Programming Interface (API)" first [acronym]
examples/docs/guide.md:7:31: error: "master branch" is banned; use "main branch" (inclusive terminology) [ban]
examples/docs/guide.md:9:6: error: "whitelist" should be "allowlist" [alias]
lexicon: 1 file(s), 15 line(s), 4 error(s), 2 warning(s)
unused entries: none
```

The fenced `github login` block, the inline `` `github` `` span and the line marked `lexicon: ignore` are all skipped on purpose. The exit code is `1`.

To preview the fixes without touching anything:

```sh
python3 lexicon.py fix examples/glossary.txt examples --diff
```

To apply them, run `fix` on a copy so the example stays intact:

```sh
cp -r examples /tmp/lexicon-demo && python3 lexicon.py fix examples/glossary.txt /tmp/lexicon-demo
```

The acronym warnings are not auto-fixed, because the right expansion is a judgement call for a person.

## How to run

Lexicon needs Python 3.9 or newer and nothing else to run. To run the tests you also need `pytest`.

```sh
python3 lexicon.py lint GLOSSARY                       # validate the glossary
python3 lexicon.py check GLOSSARY PATH [PATH ...]      # report problems
python3 lexicon.py fix GLOSSARY PATH [PATH ...]        # rewrite aliases and bans
```

Useful options:

| Option | Command | Effect |
| --- | --- | --- |
| `--format json` | `check` | Print a JSON report with findings and coverage |
| `--strict` | `check` | Treat warnings as failures |
| `--coverage` | `check` | List glossary entries no document uses |
| `--diff` | `fix` | Print a unified diff, write nothing |

Paths can be files or directories. Directories are searched recursively for `.md`, `.markdown`, `.txt` and `.rst` files. A file passed explicitly is always scanned.

### Glossary format

```text
# comments start with #
term: Preferred | alias one, alias two | optional definition
ban: phrase to avoid | replacement | optional reason
acronym: ABBR | Expansion of the abbreviation
```

Each glossary line is checked, and a bad line gives `GLOSSARY:LINE: error: ...` on stderr with exit code `2`. Aliases and terms may not appear in two entries, and acronyms must be 2 to 10 capitals or digits starting with a letter.

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Clean, or fixes applied |
| `1` | Errors found, or `--strict` with warnings, or `fix --diff` has pending changes |
| `2` | Glossary error, unreadable path, bad usage |

## How to test

```sh
python3 -m pip install pytest
python3 -m pytest tests
```

The tests cover glossary parsing and its error line numbers, case-insensitive whole-word matching, longest-match resolution, code-block and ignore-marker handling, the acronym rule, fix rewriting with CRLF endings, directory collection, coverage counting, JSON output, and the CLI exit codes.

## Project layout

```text
lexicon.py              the whole tool: parser, scanner, fixer and CLI
examples/glossary.txt   a sample glossary
examples/docs/guide.md  a sample document with deliberate problems
tests/conftest.py       puts the project root on the import path
tests/test_lexicon.py   the test suite
```
