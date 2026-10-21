#!/usr/bin/env bash
# Generate a throwaway CA + server certificate for the mock mail servers.
#
# Why: FlowCrypt's "Security type = none" leaves MailCore's default connection
# type untouched, so the app may open the mock IMAP/SMTP socket with implicit
# TLS. The mock servers accept either, but for the TLS path the app has to trust
# our certificate -- hence a local CA that gets installed into the booted
# simulator's trust store with `xcrun simctl keychain <udid> add-root-cert`.
#
# Nothing here leaves the runner: the CA is generated per run, valid for a day,
# and only used against 127.0.0.1.
#
# Usage: make_certs.sh <outdir>
set -euo pipefail

OUT="${1:?usage: make_certs.sh <outdir>}"
mkdir -p "$OUT"
cd "$OUT"

openssl req -x509 -newkey rsa:2048 -sha256 -days 1 -nodes \
  -keyout ca.key -out ca.pem \
  -subj "/CN=Mock Mail PoC Root CA" \
  -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" \
  2>/dev/null

openssl req -newkey rsa:2048 -sha256 -nodes \
  -keyout server.key -out server.csr \
  -subj "/CN=127.0.0.1" 2>/dev/null

cat > server.ext <<'EXT'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=IP:127.0.0.1,DNS:localhost,DNS:imap.mockmail.test,DNS:smtp.mockmail.test
EXT

openssl x509 -req -in server.csr -CA ca.pem -CAkey ca.key -CAcreateserial \
  -out server.pem -days 1 -sha256 -extfile server.ext 2>/dev/null

# Server needs leaf + CA so it can present a chain; the simulator trusts ca.pem.
cat server.pem ca.pem > server-chain.pem
chmod 600 ca.key server.key
rm -f server.csr server.ext

echo "ca=$(pwd)/ca.pem leaf=$(pwd)/server-chain.pem key=$(pwd)/server.key"
openssl x509 -in ca.pem -noout -subject -dates
openssl x509 -in server.pem -noout -subject -ext subjectAltName
