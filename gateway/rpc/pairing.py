"""Mobile pairing: LAN exposure, certificate fingerprint, and pairing codes."""

import logging

from gateway import lan_access

logger = logging.getLogger(__name__)


def _ok(rid, **payload):
    return {"id": rid, "result": {"ok": True, **payload}}


def _err(rid, message, code=-32000):
    return {"id": rid, "error": {"code": code, "message": message}}


def handle(ctx, method):
    rid = ctx.rid
    params = ctx.params

    if method == "pairing.status":
        return _ok(rid, **lan_access.lan_status())

    if method == "pairing.session":
        # Minting a fresh code invalidates the previous one, so every call here
        # represents "show me a QR to scan now".
        status = lan_access.lan_status()
        if not status["supported"]:
            return _err(rid, status.get("error") or "LAN access unavailable")
        if not status["enabled"]:
            return _err(rid, "LAN access is disabled")
        try:
            issued = lan_access.issue_pairing_code()
        except lan_access.LanUnavailable as exc:
            return _err(rid, str(exc))
        except OSError as exc:
            return _err(rid, f"certificate error: {exc}")
        host = status.get("host")
        if not host:
            return _err(rid, "no LAN address detected; connect to a network first")
        uri = lan_access.build_pairing_uri(host, status["port"], status["fingerprint"], issued["code"])
        return _ok(
            rid,
            host=host,
            port=status["port"],
            fingerprint=status["fingerprint"],
            code=issued["code"],
            expires_in=issued["expires_in"],
            uri=uri,
            # None means segno is absent; the UI falls back to showing the URI
            # and the manual-entry fields instead of failing the whole call.
            qr_svg=lan_access.render_qr_svg(uri),
            # The panel prefers this: one canonical bitmap instead of a stroked
            # SVG whose rasterisation varies per browser.
            qr_png=lan_access.render_qr_png_b64(uri),
        )

    if method == "pairing.enable":
        status = lan_access.lan_status()
        if not status["supported"]:
            return _err(rid, status.get("error") or "LAN access unavailable")

        port = params.get("port", lan_access.DEFAULT_LAN_PORT)
        if not isinstance(port, int) or not (1024 <= port <= 65535):
            return _err(rid, "port must be an integer between 1024 and 65535")

        cfg = lan_access.load_config()
        cfg.enabled = True
        cfg.port = port
        if not lan_access.save_config(cfg):
            return _err(rid, "failed to persist LAN config")
        applied = lan_access.request_apply(cfg)
        # The certificate may be created as a side effect of enabling; refresh.
        return _ok(rid, applied=applied, restart_required=not applied, **lan_access.lan_status())

    if method == "pairing.disable":
        cfg = lan_access.load_config()
        cfg.enabled = False
        if not lan_access.save_config(cfg):
            return _err(rid, "failed to persist LAN config")
        lan_access.reset_pairing_codes()
        applied = lan_access.request_apply(cfg)
        return _ok(rid, applied=applied, restart_required=not applied, **lan_access.lan_status())

    if method == "pairing.revoke":
        lan_access.reset_pairing_codes()
        return _ok(rid, **lan_access.lan_status())

    if method == "pairing.rotate_certificate":
        # Invalidates every paired device; the caller must re-scan.
        try:
            info = lan_access.ensure_certificate(force=True)
        except lan_access.LanUnavailable as exc:
            return _err(rid, str(exc))
        except OSError as exc:
            return _err(rid, f"certificate error: {exc}")
        lan_access.reset_pairing_codes()
        cfg = lan_access.load_config()
        applied = lan_access.request_apply(cfg)
        return _ok(rid, fingerprint=info.fingerprint, applied=applied,
                   restart_required=not applied, **lan_access.lan_status())

    return None
