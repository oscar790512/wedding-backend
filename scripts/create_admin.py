#!/usr/bin/env python3
"""Create an admin user in Supabase. Run from wedding-backend root."""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.auth import hash_password  # noqa: E402
from app.config import settings  # noqa: E402, F401
from app.database import get_supabase  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a wedding admin user")
    parser.add_argument("username")
    parser.add_argument("password")
    parser.add_argument(
        "--role",
        choices=["admin", "staff"],
        default="admin",
    )
    args = parser.parse_args()

    response = (
        get_supabase()
        .table("admin_users")
        .insert(
            {
                "username": args.username,
                "password_hash": hash_password(args.password),
                "role": args.role,
            }
        )
        .execute()
    )

    if not response.data:
        print("Failed to create admin user", file=sys.stderr)
        sys.exit(1)

    user = response.data[0]
    print(f"Created {user['role']} user: {user['username']} ({user['id']})")


if __name__ == "__main__":
    main()
