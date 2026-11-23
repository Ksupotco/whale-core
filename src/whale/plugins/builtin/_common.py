"""Backwards-compatible re-exports; the helpers now live in ``whale.kernel.http``.

Channels and other core layers share the same outbound-HTTP discipline, so the
implementation moved to the kernel. Plugins may keep importing from here.
"""

from ...kernel.http import (UA, check_base_url, download, error_detail, http_client, is_private_http,  # noqa: F401
                            request_json, status_error, transport_error)
