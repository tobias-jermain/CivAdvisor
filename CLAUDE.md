# CLAUDE.md



## Project Context



This repository contains CivAdvisor, a Civilization-focused application/mod.



When working in this repository:



* Prioritize maintainability, clarity, and minimal changes.

* Match existing code style and architecture.

* Prefer small, focused changes over large refactors.

* Do not introduce new frameworks, patterns, or dependencies unless necessary.

* Avoid speculative improvements unrelated to the requested task.



---



## Communication Style



When creating code, documentation, commits, PR descriptions, or comments:



* Do not mention Claude, Anthropic, AI assistance, LLMs, or automated generation unless explicitly requested.

* Do not add self-referential statements.

* Do not explain obvious implementation details.

* Keep comments concise and useful.

* Write as an experienced contributor to the project.



Avoid phrases such as:



* "As Claude..."

* "This AI-generated..."

* "I analyzed the codebase..."

* "Here's a comprehensive solution..."

* "Let me walk through..."



---



## Development Workflow



Before making changes:



1. Understand the relevant files and architecture.

2. Search for existing patterns and follow them.

3. Prefer modifying existing code over creating parallel implementations.

4. Validate assumptions from the codebase rather than guessing.



After making changes:



1. Ensure the project builds successfully if possible.

2. Run relevant tests if available.

3. Check for obvious regressions.

4. Keep diffs focused on the requested task.



---



## Branch Naming



Use lowercase kebab-case.



Formats:



* feature/<short-description>

* fix/<short-description>

* refactor/<short-description>

* docs/<short-description>

* chore/<short-description>



Examples:



* feature/city-advisor-recommendations

* fix/save-game-parser

* refactor/civ-data-loading

* docs/setup-guide



---



## Commit Conventions



Use concise conventional commits.



Examples:



* feat: add advisor recommendation scoring

* fix: handle missing civilization data

* refactor: simplify save parsing logic

* docs: update installation instructions

* chore: clean up unused assets



Guidelines:



* Keep subject lines under 72 characters.

* Use imperative mood.

* Avoid multi-paragraph commit messages unless necessary.



---



## Pull Request Conventions



PR titles:



* feat: add advisor recommendation scoring

* fix: resolve crash when loading saves

* refactor: simplify recommendation pipeline



PR descriptions should contain:



### Summary



Brief description of the change.



### Changes



* Itemized list of key modifications



### Validation



* Tests executed

* Manual verification performed



### Notes



* Optional implementation details

* Known limitations if applicable



Keep PR descriptions factual and concise.



---



## Code Quality Expectations



* Prefer readability over cleverness.

* Avoid premature optimization.

* Minimize nesting when practical.

* Use descriptive names.

* Remove dead code introduced by the change.

* Do not add TODO comments without a clear justification.



When multiple solutions are possible, choose the simplest approach that satisfies the requirements.



## Output Discipline



Unless explicitly requested:



- Do not create additional documentation files.

- Do not create migration guides.

- Do not create changelogs.

- Do not create example files.

- Do not rewrite unrelated code. 
