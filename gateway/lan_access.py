"""
LAN access for the mobile client.

The desktop app talks to the gateway over loopback (127.0.0.1:19876, plain ws).
That listener never leaves the machine and is left untouched.

Mobile clients need a different path: a second, opt-in listener bound to the LAN
and wrapped in TLS. Two rules keep it safe:

1. Transport is always encrypted. The certificate is self-signed and pinned by
   fingerprint on the client, so no CA infrastructure is required.
2. The QR code never carries the long-lived gateway token. It carries a
   short-lived, single-use pairing code; the client redeems it over TLS and
   receives the real token through the encrypted channel. A photographed screen
   therefore leaks nothing durable.

`cryptography` is an optional dependency: without it LAN access reports itself
as unavailable while loopback keeps working.
"""

import ipaddress
import json
import logging
import os
import secrets
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("lan-access")

_DATA_HOME = os.environ.get("PEBBLE_HOME") or os.path.join(os.path.expanduser("~"), ".pebble")
_LAN_DIR = os.path.join(_DATA_HOME, "lan")
_CONFIG_PATH = os.path.join(_DATA_HOME, "lan.json")
_CERT_PATH = os.path.join(_LAN_DIR, "cert.pem")
_KEY_PATH = os.path.join(_LAN_DIR, "key.pem")

DEFAULT_LAN_PORT = 19877
PAIRING_TTL_SECONDS = 300
_CERT_VALID_DAYS = 3650


class LanUnavailable(RuntimeError):
    """Raised when LAN access is requested but its dependencies are missing."""


def _require_cryptography():
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError as exc:
        raise LanUnavailable(
            "LAN access needs the 'cryptography' package (pip install cryptography)"
        ) from exc
    return x509, hashes, serialization, rsa, NameOID


# ===== Configuration =====


@dataclass
class LanConfig:
    enabled: bool = False
    port: int = DEFAULT_LAN_PORT
    bind_host: str = "0.0.0.0"

    def to_dict(self) -> dict:
        return {"enabled": self.enabled, "port": self.port, "bind_host": self.bind_host}


_config_lock = threading.Lock()
_config_cache: Optional[LanConfig] = None


def load_config() -> LanConfig:
    """Read ~/.pebble/lan.json, falling back to a disabled default."""
    global _config_cache
    with _config_lock:
        if _config_cache is not None:
            return _config_cache
        cfg = LanConfig()
        try:
            if os.path.exists(_CONFIG_PATH):
                with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                if isinstance(raw, dict):
                    cfg.enabled = bool(raw.get("enabled", False))
                    port = raw.get("port", DEFAULT_LAN_PORT)
                    if isinstance(port, int) and 1024 <= port <= 65535:
                        cfg.port = port
                    host = raw.get("bind_host", "0.0.0.0")
                    if isinstance(host, str) and host.strip():
                        cfg.bind_host = host.strip()
        except (OSError, ValueError) as exc:
            logger.warning("[LAN] Ignoring unreadable config: %s", exc)
        _config_cache = cfg
        return cfg


def save_config(cfg: LanConfig) -> bool:
    """Persist the LAN configuration atomically."""
    global _config_cache
    with _config_lock:
        tmp = _CONFIG_PATH + ".tmp"
        try:
            os.makedirs(_DATA_HOME, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(cfg.to_dict(), fh, ensure_ascii=False, indent=2)
            os.replace(tmp, _CONFIG_PATH)
            _config_cache = cfg
            return True
        except OSError as exc:
            logger.error("[LAN] Failed to save config: %s", exc)
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            return False


# ===== Network address discovery =====


def detect_lan_ip() -> str:
    """
    Best-effort primary LAN IPv4 address.

    Uses a UDP socket toward a public address: no packet is sent, the OS simply
    picks the interface it would route through. Falls back to hostname
    resolution, then to loopback.
    """
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.5)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            candidate = info[4][0]
            if candidate and not candidate.startswith("127."):
                return candidate
    except OSError:
        pass
    return "127.0.0.1"


def _local_ipv4_addresses() -> list:
    """Every non-loopback IPv4 address this host currently owns."""
    found = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addr = info[4][0]
            if addr and addr not in found and not addr.startswith("127."):
                found.append(addr)
    except OSError:
        pass
    primary = detect_lan_ip()
    if primary and primary not in found and primary != "127.0.0.1":
        found.insert(0, primary)
    return found


# ===== Certificate =====


def format_fingerprint(der: bytes) -> str:
    """Colon-separated uppercase SHA-256, the shape Android tools display."""
    return ":".join(f"{byte:02X}" for byte in der)


@dataclass
class CertificateInfo:
    cert_path: str
    key_path: str
    fingerprint: str
    not_after: float
    sans: list = field(default_factory=list)


def ensure_certificate(force: bool = False) -> CertificateInfo:
    """
    Create the self-signed certificate on first use, reuse it afterwards.

    Regenerating invalidates every paired client (their pinned fingerprint no
    longer matches), so it only happens when the files are missing, unreadable,
    or explicitly forced.
    """
    x509, hashes, serialization, rsa, NameOID = _require_cryptography()

    os.makedirs(_LAN_DIR, exist_ok=True)

    if not force and os.path.exists(_CERT_PATH) and os.path.exists(_KEY_PATH):
        try:
            with open(_CERT_PATH, "rb") as fh:
                existing = x509.load_pem_x509_certificate(fh.read())
            fingerprint = format_fingerprint(existing.fingerprint(hashes.SHA256()))
            not_after = existing.not_valid_after_utc.timestamp()
            if not_after > time.time() + 86400:
                return CertificateInfo(_CERT_PATH, _KEY_PATH, fingerprint, not_after)
            logger.warning("[LAN] Existing certificate expires within a day; regenerating")
        except (OSError, ValueError) as exc:
            logger.warning("[LAN] Existing certificate unusable (%s); regenerating", exc)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "PebbleAgent Gateway"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "PebbleAgent"),
    ])

    san_entries = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
    ]
    sans = ["localhost", "127.0.0.1"]
    for addr in _local_ipv4_addresses():
        try:
            san_entries.append(x509.IPAddress(ipaddress.IPv4Address(addr)))
            sans.append(addr)
        except ValueError:
            continue

    now = time.time()
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_utc(now - 3600))
        .not_valid_after(_utc(now + _CERT_VALID_DAYS * 86400))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )

    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )

    _write_private(_CERT_PATH, cert_pem)
    _write_private(_KEY_PATH, key_pem)

    fingerprint = format_fingerprint(cert.fingerprint(hashes.SHA256()))
    logger.info("[LAN] Generated self-signed certificate; fingerprint %s", fingerprint)
    return CertificateInfo(
        _CERT_PATH, _KEY_PATH, fingerprint, now + _CERT_VALID_DAYS * 86400, sans
    )


def _utc(timestamp: float):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _write_private(path: str, payload: bytes) -> None:
    """Write a file readable only by the current user where the OS supports it."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    mode = 0o600
    fd = os.open(path, flags, mode)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)


def build_ssl_context():
    """Server-side TLS context for the LAN listener."""
    import ssl

    info = ensure_certificate()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(certfile=info.cert_path, keyfile=info.key_path)
    # The gateway never speaks HTTP/2; drop ALPN to avoid protocol confusion.
    try:
        ctx.set_alpn_protocols(["http/1.1"])
    except NotImplementedError:
        pass
    return ctx


def certificate_fingerprint() -> str:
    """Fingerprint of the current certificate, generating one if absent."""
    return ensure_certificate().fingerprint


# ===== Pairing codes =====


@dataclass
class _PairingEntry:
    created_at: float


_pairing_lock = threading.Lock()
_pairing_codes: dict = {}

# Unknown codes are rejected outright; this window is what actually stops a
# guesser from hammering the listener. The code space is ~192 bits, so the real
# value here is keeping the rejection path cheap and auditable.
_pair_failures: list = []
_PAIR_FAIL_WINDOW = 60
_PAIR_FAIL_LIMIT = 10


def _purge_expired(now: Optional[float] = None) -> None:
    now = now or time.time()
    expired = [code for code, entry in _pairing_codes.items()
               if now - entry.created_at > PAIRING_TTL_SECONDS]
    for code in expired:
        _pairing_codes.pop(code, None)


def issue_pairing_code() -> dict:
    """
    Mint a single-use pairing code.

    The previous outstanding code is dropped so only one is ever live: a code
    shown on screen is the only one that can connect.
    """
    with _pairing_lock:
        now = time.time()
        _purge_expired(now)
        _pairing_codes.clear()
        code = secrets.token_urlsafe(24)
        _pairing_codes[code] = _PairingEntry(created_at=now)
        return {
            "code": code,
            "expires_in": PAIRING_TTL_SECONDS,
            "issued_at": now,
        }


def redeem_pairing_code(code: str) -> bool:
    """
    Consume a pairing code.

    Returns True only for a live code presented within its TTL and under the
    attempt limit. Failures are counted so a guessed code cannot be retried
    indefinitely.
    """
    if not isinstance(code, str) or not code:
        return False
    with _pairing_lock:
        now = time.time()
        _pair_failures[:] = [t for t in _pair_failures if now - t < _PAIR_FAIL_WINDOW]
        if len(_pair_failures) >= _PAIR_FAIL_LIMIT:
            logger.warning("[LAN] Pairing locked: too many failed attempts in %ss", _PAIR_FAIL_WINDOW)
            return False
        _purge_expired(now)
        if code not in _pairing_codes:
            _pair_failures.append(now)
            logger.warning("[LAN] Rejected unknown or expired pairing code")
            return False
        # Valid codes are single-use: burn before returning so a replayed
        # handshake with the same code cannot succeed.
        _pairing_codes.pop(code, None)
        _pair_failures.clear()
        logger.info("[LAN] Pairing code redeemed")
        return True


def pairing_state() -> dict:
    with _pairing_lock:
        now = time.time()
        _purge_expired(now)
        return {
            "active": bool(_pairing_codes),
            "ttl_remaining": max(
                (int(PAIRING_TTL_SECONDS - (now - e.created_at)) for e in _pairing_codes.values()),
                default=0,
            ),
        }


def reset_pairing_codes() -> None:
    with _pairing_lock:
        _pairing_codes.clear()


# ===== Public description used by the RPC layer =====


_apply_hook = None


def register_apply_hook(fn) -> None:
    """
    Register the callback that reconfigures the live listener.

    Kept as a hook so this module does not import the websocket server, which
    imports the RPC layer that calls back into here.
    """
    global _apply_hook
    _apply_hook = fn


def request_apply(cfg: LanConfig) -> bool:
    """Ask the server to apply a new LAN configuration without a restart."""
    if _apply_hook is None:
        logger.warning("[LAN] No apply hook registered; change applies after restart")
        return False
    try:
        return bool(_apply_hook(cfg))
    except Exception as exc:
        logger.error("[LAN] Apply hook failed: %s", exc)
        return False


_runtime_error: Optional[str] = None


def set_runtime_error(message: Optional[str]) -> None:
    """Record why the listener could not start, for display in the UI."""
    global _runtime_error
    _runtime_error = message


def get_runtime_error() -> Optional[str]:
    return _runtime_error


PAIR_URI_SCHEME = "pebble://pair"


def build_pairing_uri(host: str, port: int, fingerprint: str, code: str) -> str:
    """
    Pack everything a phone needs into one scannable string.

    The fingerprint is emitted as bare lowercase hex (no colons) to keep the
    payload short enough for a comfortable QR size; the client re-inserts the
    separators before comparing.
    """
    from urllib.parse import urlencode

    params = urlencode({
        "h": host,
        "p": str(port),
        "f": fingerprint.replace(":", "").lower()[:32],
        "c": code,
    })
    return f"{PAIR_URI_SCHEME}?{params}"


def render_qr_svg(payload: str) -> Optional[str]:
    """
    Render the pairing URI as an inline SVG.

    Returns None when segno is absent; the caller reports that to the UI instead
    of failing, mirroring how the rest of the optional dependencies behave.
    """
    try:
        import io

        import segno
    except ImportError:
        return None

    try:
        qr = segno.make(payload, error="l")
        # segno's SVG serializer emits bytes, so StringIO raises
        # "string argument expected, got 'bytes'".
        buf = io.BytesIO()
        # Light modules on a dark backdrop is what phone cameras struggle with,
        # so the QR is always black-on-white regardless of the app theme.
        # border=4 is the quiet zone the spec requires; at 2 ZXing cannot locate
        # the finder patterns and every scan fails. scale=8 keeps the intrinsic
        # SVG large so the panel's CSS width is the only downscale.
        qr.save(buf, kind="svg", scale=8, border=4, dark="#111111", light="#ffffff")
        svg = buf.getvalue().decode("utf-8")
        # Drop the XML declaration: it is not valid inside an inline HTML
        # document and it also breaks prefix checks on the caller side.
        if svg.startswith("<?xml"):
            svg = svg.split("?>", 1)[1].lstrip()
        return svg
    except Exception as exc:
        logger.error("[LAN] QR rendering failed: %s", exc)
        return None


def render_qr_png_b64(payload: str, scale: int = 12, border: int = 4) -> Optional[str]:
    """
    Render the pairing URI as a base64 PNG.

    The SVG path draws modules as stroked paths, which every browser rasterises
    slightly differently — sub-pixel edges are exactly what a camera cannot
    resolve. A PNG has one correct rendering, so the phone sees the same bitmap
    the encoder produced.
    """
    try:
        import base64
        import io

        import segno
        from PIL import Image
    except ImportError:
        return None

    try:
        qr = segno.make(payload, error="l")
        matrix = qr.matrix
        n = len(matrix)
        size = (n + 2 * border) * scale
        img = Image.new("L", (size, size), 255)
        px = img.load()
        for y in range(n):
            row = matrix[y]
            for x in range(n):
                if not row[x]:
                    continue
                x0 = (x + border) * scale
                y0 = (y + border) * scale
                for dy in range(scale):
                    for dx in range(scale):
                        px[x0 + dx, y0 + dy] = 0
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as exc:
        logger.error("[LAN] QR PNG rendering failed: %s", exc)
        return None


def lan_status() -> dict:
    """Everything the desktop UI needs to render the pairing screen."""
    cfg = load_config()
    status = {
        "supported": True,
        "enabled": cfg.enabled,
        "port": cfg.port,
        "bind_host": cfg.bind_host,
        "host": detect_lan_ip(),
        "fingerprint": None,
        "cert_expires_at": None,
        "pairing": pairing_state(),
        "error": None,
        "runtime_error": _runtime_error,
    }
    try:
        info = ensure_certificate()
        status["fingerprint"] = info.fingerprint
        status["cert_expires_at"] = info.not_after
    except LanUnavailable as exc:
        status["supported"] = False
        status["error"] = str(exc)
    except OSError as exc:
        status["error"] = f"certificate error: {exc}"
    return status
