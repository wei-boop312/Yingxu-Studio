"""Validate operator-supplied service URLs before creating clients or runtime state."""

from urllib.parse import urlsplit

LOOPBACK_HTTP = {"localhost", "127.0.0.1"}


def service_url(value: str):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2048
        or any(ord(c) < 33 for c in value)
        or "\\" in value
        or "?" in value
        or "#" in value
    ):
        raise ValueError("invalid_service_url")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("invalid_service_url") from None
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.netloc.endswith(":")
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError("invalid_service_url")
    return parsed


def validate_app_origin(value: str, cookie_secure: bool):
    parsed = service_url(value)
    if type(cookie_secure) is not bool or parsed.path:
        raise ValueError("invalid_app_origin_or_cookie_mode")
    if parsed.scheme == "http":
        if parsed.hostname not in LOOPBACK_HTTP or cookie_secure is not False:
            raise ValueError("http_origin_requires_explicit_loopback_cookie_profile")
    elif cookie_secure is not True:
        raise ValueError("https_origin_requires_secure_cookies")
    try:
        hostname = parsed.hostname.encode("idna").decode("ascii")
    except UnicodeError:
        raise ValueError("invalid_app_origin_hostname") from None
    host = "[" + hostname + "]" if ":" in hostname else hostname
    port = parsed.port
    suffix = (
        "" if port is None or port == (443 if parsed.scheme == "https" else 80) else ":" + str(port)
    )
    return parsed.scheme + "://" + host + suffix


def validate_storage_endpoint(value: str):
    parsed = service_url(value)
    if parsed.scheme == "http" and parsed.hostname not in LOOPBACK_HTTP:
        raise ValueError("remote_storage_requires_https")
    return value
