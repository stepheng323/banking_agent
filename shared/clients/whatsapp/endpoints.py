"""WhatsApp Graph API endpoint builders."""

GRAPH_API_BASE = "https://graph.facebook.com/v24.0"


def message_url(phone_number_id: str) -> str:
    return f"{GRAPH_API_BASE}/{phone_number_id}/messages"


def media_upload_url(phone_number_id: str) -> str:
    return f"{GRAPH_API_BASE}/{phone_number_id}/media"


def media_metadata_url(media_id: str) -> str:
    return f"{GRAPH_API_BASE}/{media_id}"
