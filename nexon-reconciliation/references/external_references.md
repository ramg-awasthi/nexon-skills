# External References

Use this reference only for runtime dependency boundaries and safe usage links. Nexon-specific behavior comes from the runtime contract, approved samples, configured credentials, and owner validation.

## Codex Skills And Plugins

- OpenAI Codex Skills: https://developers.openai.com/codex/skills
- OpenAI Codex plugin build guidance: https://developers.openai.com/codex/plugins/build

Do not use packaging or plugin research to change runtime reconciliation behavior.

## SharePoint Runtime

Runtime SharePoint access uses the native LangSmith SharePoint tool. Do not use Graph API references, browser profiles, or model-driven UI operations to define normal SharePoint behavior.

The active runtime site is `Nexon Reconciliation Automation` at `https://nexonap.sharepoint.com/sites/NexonReconciliationAutomation`, library `Shared Documents`.

## Observability

- LangSmith observability: https://docs.langchain.com/langsmith/observability

Tracing is optional for hosting/observability and must not change deterministic parser/matcher contracts.
