from typing import Any

from app.odoo.client import OdooClient


class OdooService:
    def __init__(self, client: OdooClient):
        self.client = client

    def get_user_profile(self, uid: int, secret: str) -> dict[str, Any] | None:
        fields = ["id", "name", "login", "active", "share", "tz", "partner_id"]
        records = self.client.execute(
            uid=uid,
            secret=secret,
            model="res.users",
            method="read",
            args=[[uid]],
            kwargs={"fields": fields},
        )
        if records and isinstance(records, list):
            return records[0]
        return None

    def search_my_leads(
        self,
        uid: int,
        secret: str,
        search_term: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        domain: list[Any] = [
            ("user_id", "=", uid),
            ("active", "=", True),
        ]
        if search_term:
            domain.append(("name", "ilike", search_term.strip()[:100]))

        fields = ["id", "name", "stage_id"]
        order = "write_date desc, id desc"
        return self.client.execute(
            uid=uid,
            secret=secret,
            model="crm.lead",
            method="search_read",
            args=[domain],
            kwargs={"fields": fields, "order": order, "limit": limit},
        )
