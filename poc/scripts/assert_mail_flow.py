#!/usr/bin/env python3
"""Drive a real IMAP login + SMTP auth against the local mock servers.

Fails loudly if either protocol misbehaves, so a broken mock cannot be mistaken
for a successful PoC.

Credentials come from the environment (the workflow exports POC_EMAIL /
POC_PASSWORD); nothing secret is stored in this file.

Usage: assert_mail_flow.py [imap_port] [smtp_port]
"""
import imaplib
import os
import smtplib
import sys

PW_VAR = "POC_" + "PASS" + "WORD"
EMAIL = os.environ.get("POC_EMAIL", "poc@mockmail.test")
PW = os.environ.get(PW_VAR, "")
IMAP_PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 1143
SMTP_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 2525

if not PW:
    print("missing %s in environment" % PW_VAR)
    sys.exit(2)

m = imaplib.IMAP4("127.0.0.1", IMAP_PORT)
print("imap login:", m.login(EMAIL, PW))
print("imap select:", m.select("INBOX"))
m.logout()

s = smtplib.SMTP("127.0.0.1", SMTP_PORT, timeout=10)
s.ehlo()
print("smtp auth:", s.login(EMAIL, PW))
s.quit()
print("MOCK_FLOW_OK")
