"""Content negotiation and response security headers applied to every request."""

from flask import Flask, Response, request

from razzball_api.errors import ApiError

# Clients select the API version through the Accept header.
VENDOR_MEDIA_TYPES = frozenset(
    {"application/vnd.razzball.api", "application/vnd.razzball-v1+json"}
)

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def register_http_policy(app: Flask, *, exempt_endpoints: frozenset[str]) -> None:
    @app.before_request
    def require_vendor_accept() -> None:
        if request.endpoint in exempt_endpoints or request.endpoint is None:
            # Unmatched URLs fall through so the router can answer 404/405.
            return
        offered = {value.lower() for value in request.accept_mimetypes.values()}
        if offered.isdisjoint(VENDOR_MEDIA_TYPES):
            raise ApiError(
                406,
                "Not Acceptable",
                "The Accept header must include one of: "
                + ", ".join(sorted(VENDOR_MEDIA_TYPES)),
            )

    @app.after_request
    def add_security_headers(response: Response) -> Response:
        for name, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response
