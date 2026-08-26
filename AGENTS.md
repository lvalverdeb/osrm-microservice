# Agent Developer Guidelines and Instructions

This repository is designed to be maintained and expanded by AI developer agents (such as Gemini, Claude, Cursor, and ChatGPT). This file serves as your core governance profile, operational mandates, and styling standards.

---

## 🛠️ Operational Mandates

1. **Environment Isolation**:
   - **DO NOT** install global Python packages using raw `pip`.
   - All Python code must be developed and run within a virtual environment managed by **uv**.
   - Standard command prefix: `uv run`
2. **Execution Control**:
   - Before modifying any file or executing any modifying shell command, you **must** present a plan and ask for explicit user confirmation.
3. **Behavior Preservation**:
   - Do not perform destructive refactorings. Keep existing test suites passing green.
   - Do not revert codebase changes unless explicitly instructed.

---

## 🏛️ Architecture and Stack

- **Tech Stack**: Python 3.13+, FastAPI, Redis, OSRM, NetworkX, Ruff.
- **Core Principles**:
  - **Separation of Concerns (SoC)**: Keep routing and handlers (`main.rs`, `handlers.rs`), the upstream adapter (`osrm/client.rs`), and the optimisation algorithms (`vrp/allocate.rs`, `vrp/solve.rs`) completely separated.
  - **Statelessness**: Compute endpoints must be stateless, delegating state cache to Redis or OSRM backend.
  - **Idempotency**: Optimization and calculation endpoints should be strictly side-effect free.
  - **Observability**: Include structured logging, OpenTelemetry tracing hooks, and Prometheus metric instrumentation.

---

## 🎨 Coding Style & Conventions

- **Indentation**: 4 spaces.
- **Formatting & Linting**: Strictly adhere to `ruff` rules. Run `make lint` before declaring work complete.
- **Documentation**:
  - Public interfaces must have JSDoc (for JS/TS) or **Google Style Docstrings** (for Python).
  - Annotate Python docstrings with:
    ```python
    def my_function(param1: int, param2: str) -> bool:
        """Brief description of the function.

        Args:
            param1: Description of param1.
            param2: Description of param2.

        Returns:
            Description of the return value.

        Raises:
            ValueError: Under what conditions ValueError is raised.
        """
    ```
- **Comments**: Focus strictly on the **why** (rationale) rather than the **what** (action).

---

## 📥 Output and Communication Style

- **Tone**: Strictly professional, direct, and objective CLI expert. No conversational filler or preamble.
- **Summaries**: Use bullet points exclusively.
- **Explanations**: Structure using **Observation, Impact, Proposal** format.
- **Output Format Standard**: Every non-trivial response must adhere to the following ATX-style header sequence:
  1. `# Proposal for [User's Request Summary]`
  2. `## 📝 Rationale and Architectural Impact`
  3. `## 💡 Suggested Code Implementation` (Preceded by a `File: [path]` header)
  4. `## 📖 Generated Feature README.md` (Preceded by a `File: README.md` header)
  5. `## 📜 Generated CHANGELOG.md Entry` (Preceded by a `File: CHANGELOG.md` header)
  6. `## ✅ Verification (Testing/Usage)`
  7. `## ⚙️ Next Steps`

---

## 📝 Commit Standard

- Never append co-author credits, attribution lines, or footers to git commits.
- Follow **Conventional Commit** formatting:
  - `feat(<scope>): <message>`
  - `fix(<scope>): <message>`
  - `refactor(<scope>): <message>`
  - `docs(<scope>): <message>`
