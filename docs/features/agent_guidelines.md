# Agent Guidelines Integration

This feature provides automated system configurations and instructions to make AI developer agents (such as Gemini and Claude) fully aware of the repository's architectures, tech stack, testing suites, code formatting rules, and communication expectations.

## Files Added/Modified

- `CLAUDE.md`: Expanded to incorporate local build, test, and lint commands, while maintaining essential git attribution override configurations.
- `AGENTS.md`: Created as a unified governance reference file for all AI developer agents.

## Usage

AI agents will automatically read `CLAUDE.md` and `AGENTS.md` upon initialization. This prevents style inconsistencies, broken virtual environments, and unformatted outputs.
