import sqlite3

from database import DB_PATH


ELIGIBLE_STATUSES = {
    "NEW",
    "DISCOVERED",
    "WEBSITE_PENDING",
    "VERIFYING",
}


def list_identity_queue(limit=100):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT
            id,
            creator,
            first_block,
            last_block,
            member_count,
            platform_types,
            brand_hint,
            status,
            website_url,
            domain,
            confirmed_brand
        FROM platform_families
        WHERE status IN (
            'NEW',
            'DISCOVERED',
            'WEBSITE_PENDING',
            'VERIFYING'
        )
        ORDER BY
            last_block DESC,
            member_count DESC
        LIMIT ?
    """, (int(limit),)).fetchall()

    out = []

    for row in rows:
        family = dict(row)

        members = conn.execute("""
            SELECT
                p.address,
                p.token_name,
                p.token_symbol,
                p.infra_roles,
                p.discovery_source,
                p.block_number
            FROM platform_family_members m
            JOIN platform_candidates p
              ON lower(p.address)=lower(m.candidate_address)
            WHERE m.family_id=?
            ORDER BY p.block_number ASC
        """, (family["id"],)).fetchall()

        family["members"] = [
            dict(x)
            for x in members
        ]

        out.append(family)

    conn.close()
    return out


if __name__ == "__main__":
    rows = list_identity_queue()

    print("identity_queue =", len(rows))

    for row in rows:
        print()
        print("=" * 80)
        print("FAMILY")
        print(row)
