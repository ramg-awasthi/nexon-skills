# External References

These links are for implementation research only. Nexon-specific behavior comes from approved design docs, discovery exports, samples, credentials, and owner validation.

## Codex Skills And Plugins

- OpenAI Codex Skills: https://developers.openai.com/codex/skills
- OpenAI Codex plugin build guidance: https://developers.openai.com/codex/plugins/build

Implementation note: use a local skill while iterating. Package as a plugin later only if sharing, bundling app integrations/MCP config, or distributing a stable package is needed.

## SharePoint Runtime

Runtime SharePoint access uses the native LangSmith SharePoint tool. Do not use Graph API references, browser profiles, or model-driven UI operations to define normal Phase 1 SharePoint behavior.

## Observability

- LangSmith observability: https://docs.langchain.com/langsmith/observability

Implementation note: tracing is optional for hosting/observability and should not change deterministic parser/matcher contracts.
