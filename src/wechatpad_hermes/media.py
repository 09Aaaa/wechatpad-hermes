from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
import urllib.parse
import urllib.request
from pathlib import Path

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MAX_REPLY_IMAGES = 3
MEDIA_CACHE_DIR = Path("/tmp/wechatpad-hermes-media")

_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\((https?://[^\s)]+)\)", re.IGNORECASE)
_MEDIA_LINE_RE = re.compile(r"(?m)^\s*MEDIA:(/[^\r\n]+)\s*$")
_LOCAL_IMAGE_LINE_RE = re.compile(r"(?m)^\s*(/[^\r\n]+\.(?:jpe?g|png|gif|webp))\s*$", re.IGNORECASE)

# SSRF guard: only http/https, no private/reserved IPs
_ALLOWED_SCHEMES = frozenset({"http", "https"})
_NON_GLOBAL_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),    # Carrier-grade NAT
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),   # Link-local
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),     # IETF protocol assignments
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),    # Benchmarking
    ipaddress.ip_network("224.0.0.0/4"),      # Multicast
    ipaddress.ip_network("240.0.0.0/4"),      # Reserved
    ipaddress.ip_network("255.255.255.255/32"),
]


def extract_reply_media(text: str, *, max_images: int = MAX_REPLY_IMAGES) -> tuple[str, list[str]]:
    """Return reply text with media markers removed plus image paths/URLs.

    Supported markers:
    - MEDIA:/absolute/path/to/image.png on its own line
    - /absolute/path/to/image.png on its own line
    - markdown image URL: ![alt](https://example.com/image.png)
    """
    if not text:
        return "", []

    media: list[str] = []

    def add(candidate: str) -> None:
        if len(media) >= max_images:
            return
        value = candidate.strip().strip('"\'')
        if _is_supported_image_reference(value) and value not in media:
            media.append(value)

    for match in _MARKDOWN_IMAGE_RE.finditer(text):
        add(match.group(1))
    clean = _MARKDOWN_IMAGE_RE.sub("", text)

    for match in _MEDIA_LINE_RE.finditer(clean):
        add(match.group(1))
    clean = _MEDIA_LINE_RE.sub("", clean)

    for match in _LOCAL_IMAGE_LINE_RE.finditer(clean):
        add(match.group(1))
    clean = _LOCAL_IMAGE_LINE_RE.sub("", clean)

    lines = [line.rstrip() for line in clean.splitlines()]
    clean = "\n".join(line for line in lines if line.strip()).strip()
    return clean, media


def prepare_image_reference(reference: str) -> str:
    """Return a local image path suitable for WeChatPadProMAX send-image APIs."""
    if reference.startswith(("http://", "https://")):
        _validate_download_url(reference)
        return str(_download_image(reference))
    return reference


def _download_image(url: str) -> Path:
    MEDIA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    parsed = urllib.parse.urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix not in IMAGE_EXTENSIONS:
        suffix = ".jpg"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    target = MEDIA_CACHE_DIR / f"{digest}{suffix}"
    if target.exists() and target.stat().st_size > 0:
        return target
    req = urllib.request.Request(url, headers={"User-Agent": "wechatpad-hermes/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        content_type = resp.headers.get("Content-Type", "").lower()
        if "image/" not in content_type:
            raise RuntimeError(f"URL is not an image: {content_type or 'unknown content-type'}")
        data = resp.read(12 * 1024 * 1024 + 1)
    if len(data) > 12 * 1024 * 1024:
        raise RuntimeError("image exceeds 12MiB limit")
    target.write_bytes(data)
    return target


def _is_supported_image_reference(value: str) -> bool:
    if not value:
        return False
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme in {"http", "https"}:
        return Path(parsed.path).suffix.lower() in IMAGE_EXTENSIONS
    if value.startswith("/"):
        return Path(value).suffix.lower() in IMAGE_EXTENSIONS
    return False


def _validate_download_url(url: str) -> None:
    """SSRF guard: reject URLs pointing to private/reserved networks.

    Only http/https schemes allowed. Hostnames are resolved and checked.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"URL scheme not allowed: {parsed.scheme!r}")

    host = parsed.hostname or ""
    if not host:
        raise ValueError("URL has no hostname")

    # If the hostname is already a bare IP, check it directly
    try:
        addr = ipaddress.ip_address(host)
        _check_ip(addr)
        return
    except ValueError:
        pass  # hostname is a domain name — resolve below

    # Resolve to IPs and check each one
    try:
        addrinfo = socket.getaddrinfo(host, 80)
    except socket.gaierror as exc:
        raise ValueError(f"Cannot resolve hostname: {host} ({exc})")

    seen = set()
    for family, _, _, _, sockaddr in addrinfo:
        ip_str = sockaddr[0]
        if ip_str in seen:
            continue
        seen.add(ip_str)
        try:
            addr = ipaddress.ip_address(ip_str)
            _check_ip(addr)
        except ValueError as e:
            raise ValueError(f"Blocked resolved IP {ip_str} for {host}: {e}")


def _check_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    """Raise ValueError if addr is in a non-global range."""
    if isinstance(addr, ipaddress.IPv6Address):
        # Allow only global unicast IPv6
        if not addr.is_global:
            raise ValueError(f"Non-global IPv6 address: {addr}")
        return
    for net in _NON_GLOBAL_NETWORKS:
        if addr in net:
            raise ValueError(f"Address in non-global range {net}: {addr}")
