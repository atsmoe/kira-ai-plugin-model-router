# KiraAI Model Failover Router

Public standalone plugin that builds a deterministic, de-duplicated LLM model chain for each KiraAI IM batch message. It selects a primary model, appends ordered fallbacks from the same or different providers, and then hands the chain back to KiraAI core.

The plugin does not call providers, implement retries, catch the final model exception, deploy KiraAI, or modify core files. KiraAI core remains responsible for trying the ordered chain and emitting its normal final exception when every candidate fails.

## Compatibility baseline

V1 was checked against the public [`xxynet/KiraAI`](https://github.com/xxynet/KiraAI) `main` commit [`27b5273dc59983b6843de23f294dfb4dbc06ca5c`](https://github.com/xxynet/KiraAI/commit/27b5273dc59983b6843de23f294dfb4dbc06ca5c) (KiraAI v2.31.4).

The current-source extension points used by this plugin are:

- [`core/plugin/plugin_registry.py`](https://github.com/xxynet/KiraAI/blob/27b5273dc59983b6843de23f294dfb4dbc06ca5c/core/plugin/plugin_registry.py#L486-L490) registers `@on.im_batch_message` for the post-merge batch event.
- [`core/message_manager.py`](https://github.com/xxynet/KiraAI/blob/27b5273dc59983b6843de23f294dfb4dbc06ca5c/core/message_manager.py#L548-L620) runs batch handlers before selecting `event.model_group` for model execution.
- [`core/chat/message_utils.py`](https://github.com/xxynet/KiraAI/blob/27b5273dc59983b6843de23f294dfb4dbc06ca5c/core/chat/message_utils.py#L162-L181) exposes the ordered `KiraMessageBatchEvent.model_group` override.
- [`core/plugin/plugin_context.py`](https://github.com/xxynet/KiraAI/blob/27b5273dc59983b6843de23f294dfb4dbc06ca5c/core/plugin/plugin_context.py#L88-L113) resolves configured `provider_id:model_id` values through `PluginContext.get_llm_client(model_uuid=...)` and exposes the default client.
- [`core/provider/provider_manager.py`](https://github.com/xxynet/KiraAI/blob/27b5273dc59983b6843de23f294dfb4dbc06ca5c/core/provider/provider_manager.py) exposes `get_all_providers()` for exact display-name lookup.
- [`core/provider/provider.py`](https://github.com/xxynet/KiraAI/blob/27b5273dc59983b6843de23f294dfb4dbc06ca5c/core/provider/provider.py) exposes `BaseProvider.provider_id` and `BaseProvider.provider_name`.
- [`core/agent/agent_executor.py`](https://github.com/xxynet/KiraAI/blob/27b5273dc59983b6843de23f294dfb4dbc06ca5c/core/agent/agent_executor.py#L92-L131) advances to the next model for `APIStatusError`, `APITimeoutError`, `APIConnectionError`, and KiraAI `ProviderAPIError`, then preserves the normal final exception flow.

The manifest deliberately declares `core_version: "==2.31.4"`. This release promises compatibility only with the exact core version inspected and tested. A later KiraAI version requires source revalidation and a new plugin release before the range is widened.

## Configuration

| Field | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | Enables routing for IM batch messages. |
| `primary_model` | `default` | `default` uses KiraAI's configured default client; otherwise use one exact `provider_id:model_id` or `Provider Display Name:model_id`. |
| `fallback_models` | `[]` | Ordered list using stable provider IDs or exact provider display names. ASCII `:` and full-width `：` separators are accepted. |
| `max_chain_length` | `5` | Positive upper bound for a chain built by this plugin. |
| `respect_existing_model_group` | `true` | Keeps an upstream group at the start of the chain and appends configured fallbacks. |

Example values below are placeholders. Replace them with provider and model references configured in your own KiraAI instance. Stable provider IDs are recommended for long-lived configurations because display names can be renamed or duplicated.

```json
{
  "enabled": true,
  "primary_model": "provider_a:model_primary",
  "fallback_models": [
    "Friendly Provider:model_backup",
    "Another Provider：model_backup"
  ],
  "max_chain_length": 3,
  "respect_existing_model_group": true
}
```

### Routing rules

1. With no upstream model group, the plugin resolves the configured primary and fallbacks in order.
2. With `respect_existing_model_group: true`, an upstream model group becomes the start of the chain. The configured `primary_model` is not inserted, and configured fallbacks are appended.
3. With `respect_existing_model_group: false`, a valid configured chain replaces the upstream group. If nothing resolves, the event remains unchanged.
4. Duplicate clients are removed by `(provider_id, model_id)` while preserving the first occurrence.
5. Each non-default reference is trimmed and split at the first ASCII `:` or full-width `：`. The model ID after that first separator is preserved, so model IDs may contain additional separators. Provider display names themselves therefore cannot contain either separator.
6. The provider token is tried as a provider ID first. After that direct lookup fails, the plugin reads the current public provider catalog: an existing provider ID with an unavailable model fails closed, while only a token that is not an existing provider ID may resolve through one exact, case-sensitive `provider_name` match.
7. Zero or multiple display-name matches fail closed. The plugin never selects the first duplicate, performs fuzzy matching, or caches display names; renamed providers are observed on the next routed event.
8. Invalid syntax, unresolved names, ambiguity, incompatible provider catalogs, and unavailable models are skipped with role/position/reason diagnostics. Model references, provider configuration, endpoints, credentials, response bodies, and request content are not logged.
9. Empty configuration, a disabled plugin, or a chain with no valid client leaves the event unchanged so normal KiraAI routing stays active.
10. `max_chain_length` limits plugin-built chains. An upstream group is never truncated merely to meet a lower plugin cap; when it already meets or exceeds the cap, no configured fallback is appended.

### Handler composition contract

KiraAI executes batch handlers in descending priority order. This plugin registers at the lowest user-plugin priority, `Priority.LOW`, so ordinary upstream routers registered at `MEDIUM` or `HIGH` finish first. The plugin then performs the final composition step: preserve the upstream group by default, append configured fallbacks, remove duplicates, and enforce the configured cap without truncating the upstream group.

An upstream router that must participate in this composition contract should register above `LOW`. KiraAI does not define a semantic ordering between handlers at the same priority beyond registration order, so another `LOW` handler that mutates `model_group` is not a supported upstream contract. `SYS_LOW` is intentionally not used because current KiraAI reserves system priorities from user plugins.

## Installation and deployment boundary

Install this repository through the supported plugin-management flow for your KiraAI deployment. This repository does not contain deployment scripts and does not restart or alter a running KiraAI instance. Plugin Store publication and live deployment require separate approval.

## Development and tests

The entrypoint suite uses a contract-faithful minimum of KiraAI's abstract `BasePlugin`, loader binding, hook registration, and descending handler ordering. It proves lifecycle instantiation/initialization and final multi-handler event results. Routing tests exercise the real `ModelRouter` public interface with fake model clients and context objects. These are unit tests, not a live KiraAI integration test.

```bash
python -m unittest discover -s tests -v
python -m compileall -q main.py router.py tests
python -m json.tool manifest.json
python -m json.tool schema.json
git diff --check
```

Covered behavior includes disabled routing, default and fixed primaries, ordered same-provider and cross-provider fallbacks, provider IDs, exact display names with ASCII/full-width separators and whitespace, direct-ID precedence, unknown/duplicate-name fail-closed handling, missing/incompatible provider catalogs, safe diagnostics, stable de-duplication, upstream group preservation, empty or wholly invalid configuration, and chain-length enforcement.

## Operational considerations

Fallbacks can change latency, cost, tool support, context limits, multimodal support, and response behavior. Configure only models appropriate for the same workload. This V1 intentionally does not add health scoring, circuit breaking, automatic discovery, semantic-quality fallback, or retries beyond those already implemented by KiraAI core.
