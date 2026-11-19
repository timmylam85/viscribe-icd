from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from viscribe_api.config import Settings


def make_session_factory(settings: Settings) -> sessionmaker[Session]:
    engine = create_engine(
        settings.database_url.get_secret_value(), pool_pre_ping=True, hide_parameters=True
    )
    return sessionmaker(engine, expire_on_commit=False)
