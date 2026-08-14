"""Create the RSA key pair for the Snowflake service user.

Writes an encrypted PKCS#8 private key outside the repository (default
``~/.snowflake/ldl_svc_rsa_key.p8``) and prints the public key in the form
``ALTER USER ... SET RSA_PUBLIC_KEY`` expects. The passphrase is read from
the environment variable SNOWFLAKE_PRIVATE_KEY_PASSPHRASE, or prompted for.

Usage:
    python snowflake/make_keypair.py [--out ~/.snowflake/ldl_svc_rsa_key.p8]
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=Path.home() / ".snowflake" / "ldl_svc_rsa_key.p8")
    args = parser.parse_args(argv)

    if args.out.exists():
        print(f"{args.out} exists; delete it first if you want a new key")
        return 1
    passphrase = os.getenv("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE") or getpass.getpass("Passphrase for the private key: ")
    if not passphrase:
        print("a passphrase is required")
        return 1

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.BestAvailableEncryption(passphrase.encode()),
        )
    )
    try:
        args.out.chmod(0o600)
    except OSError:
        pass

    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    body = "".join(line for line in public.splitlines() if not line.startswith("-----"))
    print(f"private key written to {args.out}")
    print("public key for 02_roles_and_users.sql (one line, without the BEGIN/END markers):")
    print(body)
    print()
    print("environment for the loader and dbt:")
    print(f"  SNOWFLAKE_PRIVATE_KEY_PATH={args.out}")
    print("  SNOWFLAKE_PRIVATE_KEY_PASSPHRASE=<the passphrase>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
