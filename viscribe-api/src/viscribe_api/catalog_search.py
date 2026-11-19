import re

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from viscribe_api.azure_clients import AzureCallError, AzureGateway
from viscribe_api.catalog import normalize
from viscribe_api.models import ICDCode, ICDCodeEmbedding


def search_codes(
    session: Session, azure: AzureGateway, version: str, query: str, limit: int
) -> list[ICDCode]:
    base = select(ICDCode).where(
        ICDCode.catalog_version == version, ICDCode.active.is_(True), ICDCode.selectable.is_(True)
    )
    code = query.replace(".", "").upper()
    if re.fullmatch(r"[A-Z][0-9][0-9A-Z]{0,5}", code):
        code_column = func.replace(ICDCode.code, ".", "")
        return list(
            session.scalars(
                base.where(code_column.startswith(code))
                .order_by(case((code_column == code, 0), else_=1), ICDCode.code)
                .limit(limit)
            )
        )

    title_query = normalize(query)
    if not title_query:
        return []
    lexical = session.scalars(
        base.where(
            *(
                ICDCode.search_document.contains(f" {term} ", autoescape=True)
                for term in title_query.split()
            )
        )
        .order_by(
            case(
                (ICDCode.search_title == title_query, 0),
                (ICDCode.search_title.startswith(f"{title_query} ", autoescape=True), 1),
                else_=2,
            ),
            ICDCode.code,
        )
        .limit(limit)
    ).all()
    if lexical or not azure.chat_configured:
        return list(lexical)
    deployment = azure.settings.azure_openai_embedding
    joined = base.join(
        ICDCodeEmbedding,
        (ICDCodeEmbedding.catalog_version == ICDCode.catalog_version)
        & (ICDCodeEmbedding.code == ICDCode.code)
        & (ICDCodeEmbedding.deployment == deployment),
    )
    if session.scalar(select(joined.exists())) is not True:
        return []
    try:
        (query_vector,) = azure.embed([query], operation="icd_search_embedding", attempts=1)
    except AzureCallError:
        return []
    return list(
        session.scalars(
            joined.order_by(
                ICDCodeEmbedding.embedding.cosine_distance(query_vector), ICDCode.code
            ).limit(limit)
        )
    )
