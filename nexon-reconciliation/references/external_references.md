# External References

Use this reference only for runtime dependency boundaries and safe usage links. Nexon-specific behavior comes from the runtime contract, approved samples, configured credentials, and owner validation.

## Codex Skills And Plugins

- OpenAI Codex Skills: https://developers.openai.com/codex/skills
- OpenAI Codex plugin build guidance: https://developers.openai.com/codex/plugins/build

Do not use packaging or plugin research to change runtime reconciliation behavior.

## SharePoint Runtime

Runtime SharePoint site discovery, movement, upload, and returned item URLs use
the native SharePoint tool. `sharepoint_file_index.py` exclusively owns source
inventory, candidate resolution, opaque selection IDs, item identity pinning,
and binary download through the active Fleet SharePoint access profile. Its raw
index records the source path, item ID, size, eTag, and validated site/drive
binding; the agent receives only sanitized candidate metadata.

The runtime contract requires site name `Nexon Reconciliation Automation`, path
`/sites/NexonReconciliationAutomation`, and the site's default document library.
The active profile determines the tenant hostname; the resolver validates and
freezes the physical site and drive identity.

## Observability

- LangSmith observability: https://docs.langchain.com/langsmith/observability

Tracing is optional for hosting/observability and must not change deterministic parser/matcher contracts.
