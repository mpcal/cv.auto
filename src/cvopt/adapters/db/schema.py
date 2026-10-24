"""SQLAlchemy Core tables. Every user table carries user_id; references between
user tables are composite foreign keys on (user_id, id)."""

from sqlalchemy import (
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql.schema import SchemaItem

metadata = MetaData()

ID = String(26)  # ULID


def user_table(name: str, *columns: SchemaItem) -> Table:
    return Table(
        name,
        metadata,
        Column("id", ID, primary_key=True),
        Column(
            "user_id",
            String(320),
            ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        Column("created_at", String(32), nullable=False),
        Column("updated_at", String(32), nullable=False),
        *columns,
        UniqueConstraint("user_id", "id"),  # target of composite keys
    )


def user_ref(column: str, table: str, ondelete: str | None = None) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["user_id", column], [f"{table}.user_id", f"{table}.id"], ondelete=ondelete
    )


users = Table(
    "users",
    metadata,
    Column("user_id", String(320), primary_key=True),
    Column("user_key", String(16), nullable=False, unique=True),
    Column("created_at", String(32), nullable=False),
)

profile = Table(
    "profile",
    metadata,
    Column(
        "user_id", String(320), ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True
    ),
    Column("created_at", String(32), nullable=False),
    Column("updated_at", String(32), nullable=False),
    Column("name", Text, nullable=False, default=""),
    Column("email", Text, nullable=False, default=""),
    Column("phone", Text, nullable=False, default=""),
    Column("city", Text, nullable=False, default=""),
    Column("country", Text, nullable=False, default=""),
    Column("website", Text, nullable=False, default=""),
    Column("profiles", Text, nullable=False, default="[]"),  # JSON list of network and url
)

position = user_table(
    "position",
    Column("employer", Text, nullable=False),
    Column("employer_location", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("start_month", String(7), nullable=False),
    Column("end_month", String(7)),
)

education = user_table(
    "education",
    Column("institution", Text, nullable=False),
    Column("area", Text, nullable=False),
    Column("degree_type", Text, nullable=False),
    Column("degree_level", String(16), nullable=False),
    Column("start_month", String(7)),
    Column("end_month", String(7)),
    Column("grade", Text, nullable=False),
)

language = user_table(
    "language",
    Column("language_code", String(8), nullable=False),
    Column("level", String(8), nullable=False),
)

certificate = user_table(
    "certificate",
    Column("name", Text, nullable=False),
    Column("issuer", Text, nullable=False),
    Column("issued_month", String(7)),
    Column("url", Text, nullable=False),
)

source_document = user_table(
    "source_document",
    Column("kind", String(24), nullable=False),
    Column("filename", Text, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("mime_type", String(128), nullable=False),
    Column("language", String(8), nullable=False),
    Column("issuer", Text, nullable=False),
    Column("position_id", ID),
    Column("extracted_text", Text, nullable=False),
    Column("received_at", String(32), nullable=False),
    UniqueConstraint("user_id", "sha256"),
    user_ref("position_id", "position"),
)

fact_source = user_table(
    "fact_source",
    Column("record_type", String(16), nullable=False),
    Column("record_id", ID, nullable=False),
    Column("document_id", ID, nullable=False),
    UniqueConstraint("user_id", "record_type", "record_id", "document_id"),
    user_ref("document_id", "source_document", ondelete="CASCADE"),
)

claim = user_table(
    "claim",
    Column("type", String(16), nullable=False),
    Column("text", Text, nullable=False),
    Column("text_lang", String(8), nullable=False),
    Column("position_id", ID),
    Column("keywords", Text, nullable=False),  # JSON array as text
    Column("status", String(16), nullable=False),
    Column("origin", String(16), nullable=False),
    Column("provability", String(8), nullable=False),
    user_ref("position_id", "position"),
)

evidence = user_table(
    "evidence",
    Column("claim_id", ID, nullable=False),
    Column("kind", String(24), nullable=False),
    Column("document_id", ID),
    Column("excerpt", Text, nullable=False),
    Column("reference_name", Text, nullable=False),
    Column("reference_contact", Text, nullable=False),
    user_ref("claim_id", "claim", ondelete="CASCADE"),
    user_ref("document_id", "source_document", ondelete="CASCADE"),
)

review_batch = user_table(
    "review_batch",
    Column("document_id", ID, nullable=False),
    Column("status", String(16), nullable=False),
    Column("applied_at", String(32)),
    user_ref("document_id", "source_document", ondelete="CASCADE"),
)

proposal_item = user_table(
    "proposal_item",
    Column("batch_id", ID, nullable=False),
    Column("seq", Integer, nullable=False),
    Column("action", String(24), nullable=False),
    Column("target_id", ID),
    Column("payload", Text, nullable=False),
    Column("decision", String(8), nullable=False),
    Column("note", Text, nullable=False),
    UniqueConstraint("user_id", "batch_id", "seq"),
    user_ref("batch_id", "review_batch", ondelete="CASCADE"),
)

llm_cache = Table(
    "llm_cache",
    metadata,
    Column(
        "user_id", String(320), ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True
    ),
    Column("key", String(64), primary_key=True),
    Column("task", String(64), nullable=False),
    Column("prompt_version", String(32), nullable=False),
    Column("model", String(64), nullable=False),
    Column("response", Text, nullable=False),
    Column("created_at", String(32), nullable=False),
)
