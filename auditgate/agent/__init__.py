"""Exception-resolution agent: a hand-built ReAct loop over raw model SDKs.

models.py    provider-neutral transcript + Anthropic / OpenAI-compatible / scripted adapters
tools.py     typed tools, JSON Schema from Pydantic, execution with feedback injection
loop.py      the ReAct loop: step budget, self-correction, PII boundary, step trace
resolver.py  the AuditGate agent: tools, Resolution schema, policy that overrides the model
"""
