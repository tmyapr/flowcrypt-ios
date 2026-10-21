#!/usr/bin/env python3
"""
Transport auto-detection for the mock mail servers.

The PoC wants the FlowCrypt app to talk to a local mock IMAP/SMTP server.
FlowCrypt maps its "Security type = none" setting to `MCOConnectionType(nil)`,
i.e. it leaves MailCore's default connection type in place -- and that default
differs between MailCore builds. So rather than guessing whether the app will
speak cleartext or implicit TLS, the mock server answers whichever the client
starts with:

  * first byte 0x16  -> TLS ClientHello -> wrap the socket in TLS
  * anything else    -> plaintext, business as usual

Both paths end up in the same protocol handler, so the evidence (the LOGIN
line) is recorded identically either way.
"""
import socket
import ssl

TLS_HANDSHAKE_BYTE = 0x16


class AutoTLSMixin:
    """Mixin for a socketserver.StreamRequestHandler that may be TLS or clear."""

    certfile = None
    keyfile = None

    def setup(self):
        self.transport = "clear"
        self.tls_error = None
        # A plaintext IMAP/SMTP client waits for the server greeting before it
        # writes anything, so a blocking peek would deadlock the session. A TLS
        # client sends its ClientHello immediately, so a short timeout is enough
        # to tell the two apart.
        self.request.settimeout(1.5)
        try:
            first = self.request.recv(1, socket.MSG_PEEK)
        except (OSError, socket.timeout):
            first = b""
        finally:
            self.request.settimeout(None)

        if self.certfile and first[:1] == bytes([TLS_HANDSHAKE_BYTE]):
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(self.certfile, self.keyfile)
            try:
                self.request = ctx.wrap_socket(self.request, server_side=True)
                self.transport = "tls"
            except (ssl.SSLError, OSError) as exc:
                # A rejected handshake is itself useful evidence: it means the
                # app insisted on TLS with a certificate it does not trust.
                self.transport = "tls-handshake-failed"
                self.tls_error = str(exc)
                raise

        super().setup()
