"""
OpenAI-compatible tool/function-calling schema for the Qwen3-8B assistant.

Works with Ollama, vLLM, LM Studio, and text-generation-webui — any stack
that accepts the standard `tools` parameter in chat completions.

Six tools, each a thin wrapper around AnalyticsRepository methods.
"""

QWEN_TOOL_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "get_analytics_overview",
            "description": (
                "High-level summary of the current analytics run: total accounts "
                "analysed, counts of each fraud pattern (round-trips, layering, "
                "fan-in, fan-out, smurfing, odd-hours), and number of "
                "communities/rings found."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_account_profile",
            "description": (
                "Full risk analytics for one specific account: risk score, risk "
                "tier (LOW/MEDIUM/HIGH/CRITICAL), triggered fraud patterns, "
                "plain-language risk reasoning, and isolation-forest anomaly "
                "scores if available."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "account_id": {
                        "type": "string",
                        "description": (
                            "The account identifier, e.g. ACC000123 or a bank "
                            "account number."
                        ),
                    }
                },
                "required": ["account_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_money_trail",
            "description": (
                "Trace where a tainted sum of money moved to or came from, "
                "hop by hop, for one account."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "account_id": {
                        "type": "string",
                        "description": "The seed account to trace from.",
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["forward", "backward"],
                        "description": (
                            "forward = where money went after arriving; "
                            "backward = where it came from before leaving. "
                            "Omit for both."
                        ),
                    },
                },
                "required": ["account_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_community_profile",
            "description": (
                "Details on one detected community/ring: member count, average "
                "and max risk score, and optionally its top member accounts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "community_id": {
                        "type": "string",
                        "description": "The community/ring identifier.",
                    },
                    "include_members": {
                        "type": "boolean",
                        "description": (
                            "True if the investigator wants individual member "
                            "accounts, not just aggregate stats."
                        ),
                    },
                },
                "required": ["community_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_top_risk_entities",
            "description": (
                "Ranked list of the highest-risk accounts or communities in "
                "the current dataset."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_type": {
                        "type": "string",
                        "enum": ["accounts", "communities"],
                    },
                    "limit": {
                        "type": "integer",
                        "description": "How many to return. Default 5.",
                    },
                },
                "required": ["entity_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_full_case_report",
            "description": (
                "The full narrative investigator case report from the latest "
                "analytics run. Large — use only when explicitly asked for "
                "'the report' or a shareable case summary."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]
