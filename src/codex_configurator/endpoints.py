ORIGIN = "https://api.xi-ai.net"
API_BASE = f"{ORIGIN}/v1"
MODELS_URL = f"{API_BASE}/models"
RESPONSES_URL = f"{API_BASE}/responses"
PROVIDER_ID = "xi_ai"


def api_base_from_origin(origin: str = ORIGIN) -> str:
    """Return the fixed API base while preventing duplicate /v1 segments."""
    normalized = origin.rstrip("/")
    if normalized.endswith("/v1"):
        return normalized
    return f"{normalized}/v1"


def resource_url(resource: str, origin: str = ORIGIN) -> str:
    return f"{api_base_from_origin(origin)}/{resource.lstrip('/')}"
