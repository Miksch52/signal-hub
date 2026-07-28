#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IMAP-Login-Diagnose fuer freenet. Fragt das Passwort LIVE ab (versteckt),
umgeht den Schluesselbund komplett -> isoliert, ob es am Passwort liegt.

Aufruf im Terminal:
    cd "<...>/Signal-Hub"
    python3 src/imap_test.py
"""
import getpass
import imaplib
import ssl
import sys

try:
    import certifi
    CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    CTX = ssl.create_default_context()

HOST, PORT = "mx.freenet.de", 993
USER = "mschwillo@freenet.de"

print(f"Teste IMAP-Login fuer {USER} an {HOST}:{PORT}")
print("Passwort eingeben (Eingabe ist unsichtbar) und Enter:")
pw = getpass.getpass("Passwort: ")
print(f"  -> eingegebene Laenge: {len(pw)} Zeichen")

try:
    M = imaplib.IMAP4_SSL(HOST, PORT, ssl_context=CTX)
except Exception as e:
    sys.exit(f"VERBINDUNG fehlgeschlagen (SSL/Netz): {e}")

try:
    M.login(USER, pw)
except imaplib.IMAP4.error as e:
    print(f"\n❌ LOGIN ABGELEHNT: {e}")
    print("   -> Passwort ist fuer IMAP NICHT gueltig.")
    print("   -> Pruefe: 1) IMAP im Webmail wirklich aktiviert (gespeichert)?")
    print("              2) 2FA aktiv? Dann freenet-APP-PASSWORT erzeugen und DIESES nehmen.")
    print("              3) Ist mschwillo@freenet.de die PRIMAER-Adresse (kein Alias)?")
    sys.exit(1)

print("\n✅ LOGIN OK! Das Passwort funktioniert.")
print("=== Deine Ordner ===")
typ, ordner = M.list()
for o in ordner:
    print("  ", o.decode("utf-8", "replace"))
M.logout()
print("\nJetzt dasselbe Passwort in den Schluesselbund legen:")
print('  security add-generic-password -a "mschwillo@freenet.de" -s "signal-hub-imap" -U -w')
