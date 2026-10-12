# EACOS

Cameron's workspace for AI-engineering work: agents, tutorials and business tooling for Valley AgentWorks (VAW).

## Layout

- `agents/growth_agent/`: LangGraph + Claude agent that brainstorms B2B ideas, launches one and auto-posts to social. Its README covers usage and setup.
  - The model code is in `growth_agent/llm.py`. `brainstorm` and `launch` use Sonnet 5.5, the hourly `autopilot` uses Haiku 5.5, and without `ANTHROPIC_API_KEY` it falls back to `FakeLLM`.
  - The workflow that matters is in `growth_agent/graphs.py`. Prompts are in `prompts.py` and data shapes in `schemas.py`.
  - Tests run offline: `cd agents/growth_agent && pip install -r requirements.txt && python -m pytest -q`.
  - CI: `.github/workflows/growth-agent-tests.yml` runs the tests. `growth-agent.yml` runs the hourly autopilot (off until `GROWTH_AGENT_ENABLED=true`).
- `tutorials/langgraph/`: four standalone LangGraph lessons (`01`–`04`). Each one runs on its own.
- `architecture/`, `case-studies/`, `docs/`, `frameworks/`, `governance/`, `portfolio/`, `research/`, `templates/`: empty placeholder READMEs. Don't explore them.

## Working here

- Keep changes small and run the growth-agent tests before pushing anything under `agents/`.
- Cameron mostly works from a phone. Keep replies short and give commands ready to paste.
- Cameron is a Christian. Keep content and examples consistent with that.
