"""Pure routing logic for the KiraAI model-router plugin."""


class ModelRouter:
    """Apply plugin configuration to a message batch event."""

    def __init__(self, context, config, log_warning=None):
        self._context = context
        self._config = config
        self._log_warning = log_warning or (lambda _message: None)

    def route(self, event):
        if not self._config.get("enabled", True):
            return
        if "primary_model" not in self._config and not self._config.get("fallback_models"):
            return

        existing = list(getattr(event, "model_group", None) or [])
        preserving_existing = bool(existing and self._config.get("respect_existing_model_group", True))
        if preserving_existing:
            chain = list(existing)
        else:
            primary_ref = self._config.get("primary_model", "default")
            if primary_ref == "default":
                primary = self._context.get_default_llm_client()
                if primary is None:
                    self._log_warning("Default primary model is unavailable; normal routing is preserved if no fallback resolves.")
            else:
                primary = self._resolve(primary_ref, "primary", 0)

            chain = [primary] if primary is not None else []
        for index, fallback_ref in enumerate(self._config.get("fallback_models", []), start=1):
            fallback = self._resolve(fallback_ref, "fallback", index)
            if fallback is not None:
                chain.append(fallback)

        chain = self._deduplicate(chain)
        max_chain_length = self._max_chain_length()
        if preserving_existing:
            max_chain_length = max(max_chain_length, len(self._deduplicate(existing)))
        chain = chain[:max_chain_length]
        if chain:
            event.model_group = chain

    def _resolve(self, model_ref, role, index):
        parsed = self._parse_reference(model_ref)
        if parsed is None:
            self._log_warning(f"Ignored invalid {role} model reference at position {index}.")
            return None

        provider_token, model_id = parsed
        resolved = self._context.get_llm_client(
            model_uuid=f"{provider_token}:{model_id}"
        )
        if resolved is not None:
            return resolved

        provider = self._resolve_provider_name(provider_token, role, index)
        if provider is None:
            return None

        provider_id = getattr(provider, "provider_id", None)
        if not isinstance(provider_id, str) or not provider_id:
            self._log_warning(
                f"Ignored unresolved {role} model at position {index}: "
                "provider entry is incompatible."
            )
            return None

        resolved = self._context.get_llm_client(
            model_uuid=f"{provider_id}:{model_id}"
        )
        if resolved is None:
            self._log_warning(f"Ignored unavailable {role} model at position {index}.")
        return resolved

    @staticmethod
    def _parse_reference(model_ref):
        if not isinstance(model_ref, str):
            return None

        separator_positions = [
            position
            for position in (model_ref.find(":"), model_ref.find("："))
            if position >= 0
        ]
        if not separator_positions:
            return None

        separator_position = min(separator_positions)
        provider_token = model_ref[:separator_position].strip()
        model_id = model_ref[separator_position + 1:].strip()
        if not provider_token or not model_id:
            return None
        return provider_token, model_id

    def _resolve_provider_name(self, provider_name, role, index):
        provider_mgr = getattr(self._context, "provider_mgr", None)
        get_all_providers = getattr(provider_mgr, "get_all_providers", None)
        if not callable(get_all_providers):
            self._log_warning(
                f"Ignored unresolved {role} model at position {index}: "
                "provider catalog is unavailable."
            )
            return None

        try:
            providers = get_all_providers()
        except Exception:
            self._log_warning(
                f"Ignored unresolved {role} model at position {index}: "
                "provider catalog is unavailable."
            )
            return None

        if not isinstance(providers, dict):
            self._log_warning(
                f"Ignored unresolved {role} model at position {index}: "
                "provider catalog is incompatible."
            )
            return None

        if provider_name in providers:
            self._log_warning(
                f"Ignored unavailable {role} model at position {index}."
            )
            return None

        matches = [
            provider
            for provider in providers.values()
            if getattr(provider, "provider_name", None) == provider_name
        ]
        if not matches:
            self._log_warning(
                f"Ignored unresolved {role} model at position {index}: "
                "provider name was not found."
            )
            return None
        if len(matches) > 1:
            self._log_warning(
                f"Ignored unresolved {role} model at position {index}: "
                "provider name is ambiguous."
            )
            return None
        return matches[0]

    @staticmethod
    def _deduplicate(clients):
        result = []
        seen = set()
        for client in clients:
            model = getattr(client, "model", None)
            provider_id = getattr(model, "provider_id", None)
            model_id = getattr(model, "model_id", None)
            key = (provider_id, model_id) if provider_id and model_id else ("object", id(client))
            if key not in seen:
                seen.add(key)
                result.append(client)
        return result

    def _max_chain_length(self):
        value = self._config.get("max_chain_length", 5)
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = 0
        if parsed <= 0:
            self._log_warning("Invalid max_chain_length; using the safe default of 5.")
            return 5
        return parsed
