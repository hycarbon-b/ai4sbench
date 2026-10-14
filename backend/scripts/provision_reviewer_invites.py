"""One-time, operator-run provisioning from a reviewed JSON recipient list.

Input is a JSON array of {email, status, existing_github_login?}. Without --issue,
the transaction is rolled back and stdout contains only a reconciliation report.
With --issue, stdout contains one-time invitation URLs and HTML; redirect it to a
private file. This script never sends email and never stores plaintext tokens.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sqlite3
import sys
import time
import uuid
from pathlib import Path

from fastapi_users.password import PasswordHelper

from src.mailing.notifications import render_reviewer_invite


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--input", required=True, help="JSON file path or - for stdin")
    parser.add_argument("--issue", action="store_true")
    parser.add_argument("--base-url", default="https://dashboard.ai4sbench.org")
    args = parser.parse_args()
    raw_input = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8")
    recipients = json.loads(raw_input)
    if not isinstance(recipients, list) or len(recipients) != 18:
        raise ValueError("Expected exactly 18 reviewed recipients")
    if len({item["email"].casefold() for item in recipients}) != len(recipients):
        raise ValueError("Duplicate recipient email")

    connection = sqlite3.connect(args.database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("BEGIN IMMEDIATE")
    try:
        prepared = []
        for item in recipients:
            email = str(item["email"]).strip().lower()
            status = str(item["status"])
            if status not in {"approved", "pending"}:
                raise ValueError(f"Invalid invitation status for {email}")
            applications = connection.execute(
                "SELECT id,name,email,status,user_id FROM reviewer_applications WHERE lower(email)=?",
                (str(item.get("application_email", email)).lower(),),
            ).fetchall()
            if len(applications) != 1 or applications[0]["status"] != status:
                raise ValueError(f"Application count or status changed for {email}")
            application = applications[0]
            existing_login = item.get("existing_github_login")
            if existing_login:
                users = connection.execute(
                    "SELECT id,email,github_login FROM user WHERE lower(github_login)=?",
                    (str(existing_login).lower(),),
                ).fetchall()
                if len(users) != 1:
                    raise ValueError(f"Existing account not found for {email}")
                user = users[0]
                oauth_email = connection.execute(
                    "SELECT account_email FROM oauth_account WHERE user_id=? AND oauth_name='github'",
                    (user["id"],),
                ).fetchone()
                if oauth_email is None or oauth_email["account_email"].lower() != email:
                    raise ValueError(f"OAuth email does not match the invitation recipient {email}")
            else:
                if email != application["email"].lower():
                    raise ValueError(f"New account email differs from application email: {email}")
                users = connection.execute(
                    "SELECT id,email,github_login FROM user WHERE lower(email)=?", (email,)
                ).fetchall()
                if len(users) > 1 or (users and users[0]["github_login"]):
                    raise ValueError(f"Existing GitHub account requires manual review: {email}")
                user = users[0] if users else None
            if application["user_id"] and (user is None or application["user_id"] != user["id"]):
                raise ValueError(f"Application is linked to a different account: {email}")
            prepared.append((item, application, user, email))

        report = []
        now = int(time.time())
        password_helper = PasswordHelper()
        for _item, application, user, email in prepared:
            if user is None:
                user_id = str(uuid.uuid4())
                if args.issue:
                    connection.execute(
                        """INSERT INTO user
                        (id,email,hashed_password,is_active,is_superuser,is_verified,role,github_login,email_status)
                        VALUES (?,?,?,1,0,0,'member',NULL,'unauthorized')""",
                        (user_id, email, password_helper.hash(secrets.token_urlsafe(32))),
                    )
            else:
                user_id = user["id"]
            if args.issue:
                connection.execute(
                    "UPDATE reviewer_applications SET user_id=? WHERE id=?",
                    (user_id, application["id"]),
                )
                connection.execute(
                    """UPDATE temporary_credentials SET revoked_at=?
                    WHERE user_id=? AND kind='invite' AND used_at IS NULL AND revoked_at IS NULL""",
                    (now, user_id),
                )
                token = "tbinvite_" + secrets.token_urlsafe(32)
                expires_at = now + 7 * 24 * 60 * 60
                connection.execute(
                    """INSERT INTO temporary_credentials
                    (id,user_id,kind,token_hash,email,created_at,expires_at,used_at,revoked_at)
                    VALUES (?,?,'invite',?,?,?,?,NULL,NULL)""",
                    (str(uuid.uuid4()), user_id, hashlib.sha256(token.encode()).hexdigest(),
                     user["email"] if user else email, now, expires_at),
                )
                login_url = f"{args.base_url.rstrip('/')}/auth/invite#{token}"
                rendered = render_reviewer_invite(application["name"], login_url, application["status"])
                report.append({
                    "name": application["name"], "recipient": email,
                    "subject": rendered.subject, "html": rendered.html,
                    "login_url": login_url, "expires_at": expires_at,
                })
            else:
                report.append({
                    "name": application["name"], "recipient": email, "status": application["status"],
                    "account": "existing" if user else "new", "user_id": user_id if user else None,
                    "account_email": user["email"] if user else None,
                })
        if args.issue:
            connection.commit()
        else:
            connection.rollback()
        json.dump(report, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
